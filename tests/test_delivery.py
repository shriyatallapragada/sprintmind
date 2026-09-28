"""Ledger, GitHub pipeline and risk engine, exercised directly (no HTTP, no LLM, offline memory)."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import pytest

from app.delivery import DeliveryService
from app.github import SignatureError, demo_payload, find_tickets, ticket_regex, verify_signature
from app.inspector import Inspector
from app.ledger import Ledger
from app.memory import LocalStore, MemoryService
from app.risk import business_days_between
from app.team import Team

NOW = datetime(2026, 9, 30, 5, 0, tzinfo=timezone.utc)  # Wednesday 10:30 IST


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def svc():
    team = Team.load("data/team.json", "Northwind")
    memory = MemoryService(LocalStore(), Inspector(), "test", team.name)
    clock = {"now": NOW}
    s = DeliveryService(Ledger(":memory:"), memory, team, sprint="14", repo="northwind/platform",
                        clock=lambda: clock["now"])
    s._clock = clock
    return s


def doc(svc, items, *, source="meeting", title="Acme sync", when=NOW - timedelta(hours=2), doc_id=None, customer="acme"):
    return run(svc.record_extraction(source_type=source, title=title, document_id=doc_id or f"{source}-{title}-{when.isoformat()}",
                                     occurred_at=when, items=items, customer=customer))


def gh(svc, kind, *, ticket="NW-231", pr=488, when=None, run_id=None):
    event, payload = demo_payload(kind, repo="northwind/platform", ticket=ticket, pr_number=pr, title="Acme tenant limits",
                                  run_id=run_id, at=when or svc.now())
    return run(svc.process_github(event, payload, delivery_id=f"test-{kind}-{run_id}"))


def open_rules(svc, item_id):
    return {r["rule"]: r for r in svc.ledger.risks(status="open", item_id=item_id)}


SANDBOX = {"kind": "commitment", "title": "Acme sandbox tenant at 1,200 req/min", "owner": "Priya Raman",
           "due": "2026-10-02", "ticket": "NW-231", "customer": "Acme Corp", "status": "in_progress"}


def test_business_days():
    wed = NOW.date()
    assert business_days_between(wed, wed + timedelta(days=2)) == 2          # Fri
    assert business_days_between(wed, wed + timedelta(days=5)) == 3          # Mon
    assert business_days_between(wed, wed - timedelta(days=3)) == -3         # Sun: Mon, Tue, Wed lie between
    assert business_days_between(wed, wed) == 0


def test_ticket_linking_from_branch_title_and_flags():
    rx = ticket_regex("NW")
    assert find_tickets(rx, "feature/nw-231-tenant-limits", "NW-236: SSO", "ff_nw240_flag", "utf-8 NWX-9") == \
        ["NW-231", "NW-236", "NW-240"]


def test_signature_verification():
    body = b'{"zen": "hi"}'
    good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    verify_signature("s3cret", body, good)
    with pytest.raises(SignatureError):
        verify_signature("s3cret", body, "sha256=deadbeef")
    with pytest.raises(SignatureError):
        verify_signature("s3cret", body, None)


def test_commitment_ci_failure_raises_risk_and_recovery_resolves_it(svc):
    [cid] = doc(svc, [SANDBOX])
    item = svc.ledger.get_item(cid)
    assert item["kind"] == "commitment" and item["owner_id"] == "priya" and item["ticket"] == "NW-231"

    res = gh(svc, "ci_failed", run_id=1)
    assert res["status"] == "processed" and res["tickets"] == ["NW-231"]
    risk = open_rules(svc, cid)["ci_failing"]
    assert risk["severity"] == "high"                                   # due in 2 business days
    assert risk["evidence"][0]["source"] == "github" and "ci / test" in risk["explanation"]
    assert "ci_failing" not in open_rules(svc, "tkt-NW-231")            # one CI failure = one alert (on the commitment)

    dup = gh(svc, "ci_failed", run_id=1)
    assert dup["status"] == "duplicate"
    assert len(svc.ledger.events(source="github")) == 1

    svc._clock["now"] = NOW + timedelta(hours=3)
    gh(svc, "ci_passed", run_id=2, when=svc.now())
    assert "ci_failing" not in open_rules(svc, cid)
    resolved = svc.ledger.risks(status="resolved", item_id=cid)
    assert resolved and "CI 'ci / test' passed" in resolved[0]["resolved_reason"]
    assert [h["change"] for h in resolved[0]["history"]] == ["opened", "resolved"]


def test_pr_merged_marks_ticket_done_and_links_commitment(svc):
    [cid] = doc(svc, [SANDBOX])
    gh(svc, "pr_opened", when=NOW - timedelta(hours=1))
    assert svc.ledger.get_item("tkt-NW-231")["status"] == "in_review"
    gh(svc, "pr_merged")
    t = svc.ledger.get_item("tkt-NW-231")
    assert t["status"] == "done" and t["engineering"]["prs"]["488"]["state"] == "merged"
    assert any(e["kind"] == "github.pr_merged" for e in svc.ledger.events(item_id=cid))


def test_blocker_approval_and_resolution(svc):
    [tid] = doc(svc, [{"kind": "blocker", "ticket": "NW-240", "title": "Postgres 16 upgrade", "owner": "Arjun Mehta",
                       "blocked_on": "Acme's written approval of the maintenance window", "due": "2026-10-05"}],
                source="standup", title="Daily standup", when=NOW - timedelta(days=2))
    item = svc.ledger.get_item(tid)
    assert item["status"] == "blocked"
    [dep] = svc.ledger.dependencies(tid, status="open")
    assert dep["kind"] == "approval" and dep["party"] == "Acme Corp"
    assert "pending_approval" in open_rules(svc, tid)

    # a statement that approval is still pending must not count as approval
    doc(svc, [{"kind": "decision", "title": "Maintenance window approval still pending from Acme"}], title="Standup d2",
        source="standup", when=NOW - timedelta(days=1))
    assert svc.ledger.dependencies(tid, status="open")

    doc(svc, [{"kind": "decision", "title": "Acme approved the Sunday 3-5 AM IST maintenance window"}])
    assert not svc.ledger.dependencies(tid, status="open")
    item = svc.ledger.get_item(tid)
    assert item["status"] == "todo"                                     # unblocked, not invented as "in progress"
    assert "pending_approval" not in open_rules(svc, tid)
    assert any(e["kind"] == "approval.granted" for e in svc.ledger.events(item_id=tid))


def test_stale_statement_does_not_override_newer_github_state(svc):
    doc(svc, [{"kind": "task", "ticket": "NW-236", "title": "Okta SSO", "owner": "Rahul Verma", "status": "in_progress"}],
        source="standup", when=NOW - timedelta(days=3))
    gh(svc, "pr_merged", ticket="NW-236", pr=479, when=NOW - timedelta(days=1))
    # a meeting recorded later, but talking about an older state: "SSO still in progress" (lower authority, within 3 days)
    doc(svc, [{"kind": "status_update", "ticket": "NW-236", "title": "Okta SSO", "status": "in_progress"}],
        when=NOW - timedelta(hours=2))
    item = svc.ledger.get_item("tkt-NW-236")
    assert item["status"] == "done"
    skipped = [h for h in svc.ledger.history(item["id"]) if not h["applied"]]
    assert skipped and "less authoritative" in skipped[0]["note"]
    assert open_rules(svc, item["id"]) == {}                            # done items carry no open risks

    # an older statement arriving late is kept as history but not applied
    doc(svc, [{"kind": "status_update", "ticket": "NW-236", "title": "Okta SSO", "status": "blocked",
               "blocked_on": "IdP metadata"}], source="task_update", title="Old Jira digest", when=NOW - timedelta(days=5))
    assert svc.ledger.get_item("tkt-NW-236")["status"] == "done"
    assert any("older than the current value" in (h["note"] or "") for h in svc.ledger.history(item["id"]))


def test_conflict_is_flagged_when_open_item_disagrees(svc):
    gh(svc, "pr_opened", ticket="NW-244", pr=490, when=NOW - timedelta(hours=5))
    doc(svc, [{"kind": "status_update", "ticket": "NW-244", "title": "Audit export", "status": "done"}],
        source="standup", when=NOW - timedelta(hours=1))
    item = svc.ledger.get_item("tkt-NW-244")
    assert item["status"] == "in_review"
    risk = open_rules(svc, item["id"])["conflicting_evidence"]
    assert "standup" in risk["explanation"] and "github" in risk["explanation"]


def test_deadline_and_owner_changes_keep_history(svc):
    [cid] = doc(svc, [SANDBOX], when=NOW - timedelta(days=2))
    doc(svc, [SANDBOX | {"due": "2026-10-07", "owner": "Vikram Rao"}], title="Acme follow-up", when=NOW - timedelta(days=1))
    item = svc.ledger.get_item(cid)
    assert item["due"] == "2026-10-07" and item["owner_id"] == "vikram"
    due_hist = [h for h in svc.ledger.history(cid, fields=("due",)) if h["applied"]]
    assert [h["new"] for h in due_hist] == ["2026-10-07", "2026-10-02"]
    assert "due changed from 2026-10-02 to 2026-10-07" in svc.state_line(item)


def test_missing_information_is_not_a_risk(svc):
    [cid] = doc(svc, [{"kind": "commitment", "title": "Share the audit export sample with Acme compliance"}])
    rules = open_rules(svc, cid)
    assert {"missing_owner", "missing_due"} <= set(rules)
    assert all(r["kind"] == "missing_info" and r["severity"] == "info" for r in rules.values())
    assert svc.dashboard()["summary"]["open_risks"] == 0


def test_overdue_and_stale(svc):
    [cid] = doc(svc, [SANDBOX | {"due": "2026-09-28", "ticket": None, "title": "Send rollback plan to Acme",
                                 "owner": "Arjun Mehta"}], when=NOW - timedelta(days=6))
    assert open_rules(svc, cid)["overdue"]["severity"] == "high"
    [sid] = doc(svc, [{"kind": "task", "title": "Grafana tenant dashboard panels", "owner": "Arjun Mehta",
                       "due": "2026-10-07", "status": "in_progress"}], source="standup", title="old standup",
                when=NOW - timedelta(days=7))
    assert "stale_progress" in open_rules(svc, sid)


def test_dashboard_edit_and_workload(svc):
    [cid] = doc(svc, [SANDBOX])
    out = run(svc.update_item(cid, status="done", note="Sandbox live, Acme confirmed", actor="Priya Raman"))
    assert out["item"]["status"] == "done" and "Sandbox live" in out["memory"]
    d = svc.dashboard()
    assert d["summary"]["commitments_open"] == 0
    assert any(h["field"] == "status" and h["new"] == "done" for h in d["change_history"])
    priya = next(w for w in d["workload"] if w["person_id"] == "priya")
    assert priya["open"] == 0
    with pytest.raises(ValueError):
        run(svc.update_item(cid, owner="Nobody Here"))


def test_rules_learned_from_real_extractions(svc):
    # a blocker note mentioning the ticket first, then the planned task: the planned title wins
    doc(svc, [{"kind": "blocker", "ticket": "NW-240", "title": "Flyway scripts written", "owner": "Arjun Mehta",
               "blocked_on": "Acme approval"},
              {"kind": "task", "ticket": "NW-240", "title": "Postgres 16 upgrade and orders partitioning", "owner": "Arjun Mehta"}],
        source="meeting", title="Sprint 14 Planning", when=NOW - timedelta(days=5), customer=None)
    assert svc.ledger.get_item("tkt-NW-240")["title"] == "Postgres 16 upgrade and orders partitioning"
    # "commitment" with no customer is sprint work on the ticket, not a customer commitment
    doc(svc, [{"kind": "commitment", "ticket": "NW-244", "title": "Audit log CSV export", "owner": "Sara Kim", "due": "2026-10-02"}],
        source="meeting", title="Sprint 14 Planning 2", when=NOW - timedelta(days=5), customer=None)
    assert not svc.ledger.items(kind="commitment") and svc.ledger.get_item("tkt-NW-244")["due"] == "2026-10-02"
    # a standup finishing a sub-step does not close the ticket
    doc(svc, [{"kind": "status_update", "ticket": "NW-244", "title": "Wireframes reviewed", "status": "done"}],
        source="standup", title="Daily standup", when=NOW - timedelta(days=1), customer=None)
    item = svc.ledger.get_item("tkt-NW-244")
    assert item["status"] == "todo"
    assert any("sub-step reported done" in (h["note"] or "") for h in svc.ledger.history(item["id"]))
    # ... but Jira saying the ticket is done does
    doc(svc, [{"kind": "status_update", "ticket": "NW-244", "title": "Wireframes reviewed", "status": "done"}],
        source="task_update", title="Jira digest", when=NOW - timedelta(hours=3), customer=None)
    assert svc.ledger.get_item("tkt-NW-244")["status"] == "done"
    # between reported sources the newest statement wins (no conflict with an older Jira digest)
    doc(svc, [{"kind": "status_update", "ticket": "NW-231", "title": "Rate limiting", "status": "in_review"}],
        source="task_update", title="Jira digest 2", when=NOW - timedelta(days=2), customer=None)
    doc(svc, [{"kind": "status_update", "ticket": "NW-231", "title": "Rate limiting", "status": "in_progress"}],
        source="standup", title="Standup", when=NOW - timedelta(days=1), customer=None)
    assert svc.ledger.get_item("tkt-NW-231")["status"] == "in_progress" and not svc.ledger.conflicts("tkt-NW-231")


def test_status_reports_do_not_reassign_owners_and_titles_keep_rank(svc):
    doc(svc, [{"kind": "task", "ticket": "NW-252", "title": "Grafana tenant dashboard", "owner": "Arjun Mehta"}],
        source="meeting", title="Planning", when=NOW - timedelta(days=6), customer=None)
    doc(svc, [{"kind": "status_update", "ticket": "NW-252", "title": "Priority changed to P3 (deferred)", "owner": "Neha Kulkarni"}],
        source="task_update", title="Jira digest", when=NOW - timedelta(days=2), customer=None)
    item = svc.ledger.get_item("tkt-NW-252")
    assert item["owner_id"] == "arjun" and item["title"] == "Grafana tenant dashboard"
    doc(svc, [{"kind": "task", "ticket": "NW-252", "title": "Grafana tenant dashboard", "owner": "Sara Kim"}],
        source="meeting", title="Re-planning", when=NOW - timedelta(days=1), customer=None)
    assert svc.ledger.get_item("tkt-NW-252")["owner_id"] == "sara"          # an explicit assignment does change it
