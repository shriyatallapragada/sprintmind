"""Scripted end-to-end delivery demo that runs through the real pipelines.

  1. commitment   A short Acme check-in is processed by the meeting pipeline: Priya commits to the Acme
                  sandbox at 1,200 req/min in 2 business days (NW-231). A PR is opened on GitHub.
  2. ci_failure   GitHub reports a failed CI run on that PR -> ledger -> risk engine opens a HIGH
                  "CI failing on linked work" risk on the commitment, with evidence, retained in Hindsight.
  3. fix          CI passes and the PR is merged -> the CI risk resolves, NW-231 is done.
  4. close        Priya confirms the sandbox is live -> the commitment is closed from the dashboard.

Nothing here writes results directly: each step builds the same inputs a person or GitHub would send.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .github import demo_payload

TICKET, PR, BRANCH = "NW-231", 488, "nw-231-acme-tenant-limits"
PR_TITLE = "Per-tenant limits for the Acme sandbox tenant"
MEETING_TITLE = "Acme sandbox check-in (demo)"
STEPS = ("commitment", "ci_failure", "fix", "close")


def add_business_days(day, n: int):
    while n > 0:
        day += timedelta(days=1)
        if day.weekday() < 5:
            n -= 1
    return day


def _commitment(svc: Any) -> dict[str, Any] | None:
    """The newest commitment created by the demo meeting (older seeded commitments are left alone)."""
    mine = [it for it in svc.delivery.ledger.items(kind="commitment", ticket=TICKET)
            if "acme-sandbox-check-in-demo" in (it.get("meeting_id") or "")]
    return max(mine, key=lambda it: it["created_at"]) if mine else None


async def run_step(svc: Any, step: str, actor: str | None) -> dict[str, Any]:
    d = svc.delivery
    now = d.now()
    if step == "commitment":
        today = now.astimezone(ZoneInfo(d.tz)).date()
        due = add_business_days(today, 2)
        weekday = due.strftime("%A")
        transcript = (f"0:00:05 Lisa Moreno: Our importer test run is on {weekday}. We need the sandbox tenant at 1,200 "
                      "requests per minute, burst 200, and Retry-After in integer seconds.\n"
                      f"0:00:31 Priya Raman: Limits are per tenant now under NW-231. I'll have your sandbox configured by {weekday}.\n"
                      "0:00:52 Tom Becker: Good, that's the commitment then.\n"
                      "0:01:10 Vikram Rao: PR is up; I'll review it today.")
        meeting = await svc.meetings.process(
            transcript=transcript, title=MEETING_TITLE, meeting_type="customer_sync", occurred_at=now - timedelta(minutes=30),
            customer="Acme Corp", participants=["Priya Raman", "Vikram Rao", "Neha Kulkarni"],
            extraction={
                "summary": f"Acme needs its sandbox tenant at 1,200 req/min (burst 200, integer Retry-After) for an importer "
                           f"test on {weekday}. Priya committed to deliver it under NW-231 by {due.isoformat()}.",
                "decisions": ["Acme sandbox limits: 1,200 req/min sustained, burst 200, integer Retry-After"],
                "items": [
                    {"kind": "commitment", "scrum_stage": "sprint_review", "ticket": TICKET, "customer": "Acme Corp",
                     "title": "Acme sandbox tenant at 1,200 req/min (burst 200, integer Retry-After)",
                     "owner": "Priya Raman", "due": due.isoformat(), "status": "in_progress", "priority": "P1",
                     "detail": f"Sandbox ready for Acme's importer test on {weekday}"},
                    {"kind": "task", "ticket": TICKET, "title": "Per-tenant API rate limiting", "owner": "Priya Raman",
                     "status": "in_progress"},
                ]})
        event, payload = demo_payload("pr_opened", repo=d.repo, ticket=TICKET, pr_number=PR, title=PR_TITLE,
                                      branch=BRANCH, at=now - timedelta(minutes=20))
        gh = await d.process_github(event, payload, delivery_id=f"demo-{uuid.uuid4()}", demo=True)
        return {"meeting_id": meeting["id"], "github": gh["status"], "commitment": d.item_view(_commitment(svc)["id"])}

    item = _commitment(svc)
    if item is None:
        raise ValueError("run the 'commitment' step first")
    run_id = uuid.uuid4().int % 10**9
    if step == "ci_failure":
        event, payload = demo_payload("ci_failed", repo=d.repo, ticket=TICKET, pr_number=PR, title=PR_TITLE,
                                      branch=BRANCH, run_id=run_id, check_name="ci / integration-tests", at=now)
        return {"github": await d.process_github(event, payload, delivery_id=f"demo-{uuid.uuid4()}", demo=True),
                "commitment": d.item_view(item["id"])}
    if step == "fix":
        results = []
        for kind, at in (("ci_passed", now), ("pr_merged", now + timedelta(seconds=30))):
            event, payload = demo_payload(kind, repo=d.repo, ticket=TICKET, pr_number=PR, title=PR_TITLE, branch=BRANCH,
                                          run_id=run_id, check_name="ci / integration-tests", at=at)
            results.append(await d.process_github(event, payload, delivery_id=f"demo-{uuid.uuid4()}", demo=True))
        return {"github": results, "commitment": d.item_view(item["id"])}
    if step == "close":
        out = await d.update_item(item["id"], status="done", actor=actor or "Priya Raman",
                                  note="Sandbox live at 1,200 req/min; Acme ran a test import successfully.")
        return {"update": out["result"], "commitment": out["item"]}
    raise ValueError(f"step must be one of {STEPS}")


def status(svc: Any) -> dict[str, Any]:
    item = _commitment(svc)
    if item is None:
        return {"steps_done": [], "commitment": None}
    events = svc.delivery.ledger.events(item_id=item["id"], limit=100)
    kinds = {e["kind"] for e in events}
    done = ["commitment"]
    if "github.ci_failed" in kinds:
        done.append("ci_failure")
    if "github.pr_merged" in kinds:
        done.append("fix")
    if item["status"] == "done":
        done.append("close")
    return {"steps_done": done, "commitment": svc.delivery.item_view(item["id"])}
