"""API-level tests for delivery intelligence: GitHub webhooks, the demo flow, access control, corrections."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.agent import check_citations
from app.config import Settings
from app.github import demo_payload
from app.main import build_services, create_app
from app.memory import LocalStore
from tests.test_api import ARJUN, MANAGER, PRIYA, FakeLLM, FakeTranscriber

SECRET = "test-webhook-secret-123"


@pytest.fixture
def api(tmp_path):
    settings = Settings(memory_backend="local", registry_file=str(tmp_path / "reg.json"), team_file="data/team.json",
                        groq_api_key=None, hindsight_api_key=None, meetings_dir=str(tmp_path / "meetings"),
                        ledger_path=":memory:", github_webhook_secret=SECRET)
    llm = FakeLLM()
    services = build_services(settings, store=LocalStore(), llm=llm, transcriber=FakeTranscriber())
    with TestClient(create_app(services), headers=MANAGER) as client:
        yield client, services, llm


def signed(client, event, payload, secret=SECRET, delivery="d-1"):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/integrations/github/webhook", content=body,
                       headers={"X-GitHub-Event": event, "X-Hub-Signature-256": sig, "X-GitHub-Delivery": delivery,
                                "Content-Type": "application/json"})


def test_webhook_signature_dedup_and_ping(api):
    client, services, _ = api
    event, payload = demo_payload("ci_failed", repo="northwind/platform", ticket="NW-231", pr_number=488,
                                  title="Tenant limits", run_id=42)
    r = signed(client, event, payload)
    assert r.status_code == 200 and r.json()["status"] == "processed", r.text
    assert r.json()["event"]["source"] == "github" and r.json()["tickets"] == ["NW-231"]
    assert signed(client, event, payload, delivery="d-2").json()["status"] == "duplicate"   # redelivery
    assert signed(client, event, payload, secret="wrong-secret").status_code == 401
    assert client.post("/integrations/github/webhook", content=b"{}", headers={"X-GitHub-Event": "push"}).status_code == 401
    assert signed(client, "ping", {"zen": "hi"}).json() == {"status": "pong"}
    assert signed(client, "push", {"ref": "refs/heads/main"}).json()["status"] == "ignored"
    assert len(services.delivery.ledger.events(source="github")) == 1


def test_webhook_refused_without_secret(tmp_path):
    settings = Settings(memory_backend="local", registry_file=str(tmp_path / "r.json"), ledger_path=":memory:",
                        groq_api_key=None, github_webhook_secret=None)
    with TestClient(create_app(build_services(settings, store=LocalStore(), llm=FakeLLM()))) as client:
        assert client.post("/integrations/github/webhook", content=b"{}").status_code == 503


def test_end_to_end_delivery_demo(api):
    client, services, _ = api
    r = client.post("/demo/delivery/commitment")
    assert r.status_code == 200, r.text
    commitment = r.json()["commitment"]
    assert commitment["kind"] == "commitment" and commitment["owner_id"] == "priya" and commitment["ticket"] == "NW-231"
    assert client.get("/manager/dashboard").json()["summary"]["open_risks"] == 0

    r = client.post("/demo/delivery/ci_failure")
    assert r.json()["github"]["status"] == "processed"
    dash = client.get("/manager/dashboard").json()
    risk = next(x for x in dash["risks"] if x["item_id"] == commitment["id"])
    assert risk["rule"] == "ci_failing" and risk["severity"] == "high"
    assert risk["evidence"][0]["url"].startswith("https://github.com/northwind/platform/actions/runs/")
    assert risk["item"]["owner"] == "Priya Raman" and risk["recommended_action"]
    assert dash["engineering"][0]["kind"] == "github.ci_failed"
    assert any("[delivery risk opened]" in m["text"] for m in services.memory.store.banks[services.memory.bank_id])

    exp = client.post(f"/risks/{risk['id']}/explain").json()
    assert "[E1]" in exp["explanation"] and "[E9]" not in exp["explanation"]   # citations to missing evidence removed
    assert client.post(f"/risks/{risk['id']}/explain").json()["cached"] is True

    client.post("/demo/delivery/fix")
    dash = client.get("/manager/dashboard").json()
    assert not [x for x in dash["risks"] if x["item_id"] == commitment["id"]]
    assert any(e["payload"]["rule"] == "ci_failing" and e["kind"] == "risk.resolved" for e in dash["risk_history"])
    assert client.get("/items/tkt-NW-231").json()["status"] == "done"

    client.post("/demo/delivery/close")
    status = client.get("/demo/delivery").json()
    assert status["steps_done"] == ["commitment", "ci_failure", "fix", "close"]
    assert status["commitment"]["status"] == "done"
    kinds = [e["kind"] for e in client.get("/events", params={"item_id": commitment["id"]}).json()["events"]]
    assert {"statement.commitment", "github.ci_failed", "github.ci_passed", "risk.opened", "risk.resolved", "item.updated"} <= set(kinds)


def test_access_control(api):
    client, services, _ = api
    assert client.get("/actions", headers={"X-SprintMind-User": ""}).status_code == 401
    assert client.get("/actions", headers={"X-SprintMind-User": "mallory"}).status_code == 401
    for method, path in (("get", "/manager/dashboard"), ("get", "/risks"), ("get", "/events"), ("get", "/memory/list"),
                         ("post", "/admin/seed"), ("post", "/demo/delivery/commitment")):
        assert getattr(client, method)(path, headers=PRIYA).status_code == 403, path
    assert client.post("/memory/recall", json={"query": "x"}, headers=PRIYA).status_code == 403
    assert client.post("/employee/ask", json={"question": "my tasks?", "person_id": "arjun"}, headers=PRIYA).status_code == 403
    assert client.get("/employee/arjun/briefing", headers=PRIYA).status_code == 403
    assert client.post("/employee/ask", json={"question": "my tasks?", "person_id": "priya"}, headers=PRIYA).status_code == 200
    ev = client.get("/inspector/events", headers=PRIYA).json()["events"]
    assert ev and all(e.get("redacted") and "response" not in e for e in ev)
    assert client.get("/me", headers=PRIYA).json() == {"person_id": "priya", "name": "Priya Raman", "role": "employee"}


def test_token_mode(tmp_path):
    settings = Settings(memory_backend="local", registry_file=str(tmp_path / "r.json"), ledger_path=":memory:",
                        groq_api_key=None, auth_mode="token", api_tokens="neha=neha-token-0123456789,priya=priya-token-0123456789")
    with TestClient(create_app(build_services(settings, store=LocalStore(), llm=FakeLLM()))) as client:
        assert client.get("/actions", headers={"X-SprintMind-User": "neha"}).status_code == 401   # header alone is not enough
        assert client.get("/actions", headers={"Authorization": "Bearer nope-nope-nope-nope"}).status_code == 401
        assert client.get("/me", headers={"Authorization": "Bearer priya-token-0123456789"}).json()["role"] == "employee"
        assert client.get("/manager/dashboard", headers={"Authorization": "Bearer neha-token-0123456789"}).status_code == 200


def test_private_interactions_stay_private(api):
    client, services, _ = api
    client.post("/employee/ask", json={"question": "salary review preparation zebra", "person_id": "arjun"}, headers=ARJUN)
    mine = client.post("/employee/ask", json={"question": "salary review preparation zebra"}, headers=PRIYA).json()
    assert not any("Arjun Mehta asked" in m["text"] for m in mine["memories"])
    boss = client.post("/employee/ask", json={"question": "salary review preparation zebra"}).json()
    assert any("Arjun Mehta asked" in m["text"] for m in boss["memories"])
    iid = mine["interaction_id"]
    assert client.post("/feedback", json={"interaction_id": iid, "helpful": True}, headers=ARJUN).status_code == 403


def test_correction_is_shared_recalled_and_applied(api):
    client, services, _ = api
    client.post("/ingest", json={"source_type": "standup", "title": "Standup", "occurred_at": "2026-09-25T10:00:00+05:30",
                                 "content": "Priya Raman: NW-231 in review."})
    assert services.delivery.ledger.get_item("tkt-NW-231")["status"] == "in_progress"
    iid = client.post("/employee/ask", json={"question": "Is NW-231 finished?", "person_id": "priya"}, headers=PRIYA).json()["interaction_id"]
    r = client.post("/feedback", json={"interaction_id": iid, "helpful": False, "correction": "NW-231 is done, merged yesterday"},
                    headers=PRIYA).json()
    assert r["applied_to_state"] and r["applied_to_state"][0]["item_id"] == "tkt-NW-231"
    item = client.get("/items/tkt-NW-231").json()
    assert item["status"] == "done" and item["evidence"]["status"]["source"] == "feedback"
    ans = client.post("/employee/ask", json={"question": "Is NW-231 finished?"}, headers=ARJUN).json()
    assert any(m["text"].startswith("CORRECTION from Priya Raman") for m in ans["memories"])   # shared, not private
    assert ans["memories"][0]["type"] == "current_state" and "status done" in ans["memories"][0]["text"]


def test_commitment_approval_dependency_flow(api):
    client, services, _ = api
    c = client.post("/commitments", json={"title": "Enable SSO enforcement for acme.com", "customer": "Acme Corp",
                                          "owner": "Rahul Verma", "due": "2099-01-15"}).json()
    dep = client.post(f"/items/{c['id']}/dependencies", json={"description": "Acme sign-off on enforcement date",
                                                               "kind": "approval"}, headers=PRIYA)
    assert dep.status_code == 403                                   # not Priya's item
    dep = client.post(f"/items/{c['id']}/dependencies", json={"description": "Acme sign-off on enforcement date",
                                                               "kind": "approval"}).json()
    dash = client.get("/manager/dashboard").json()
    assert dash["approvals"]["pending"][0]["id"] == dep["id"] and dash["approvals"]["pending"][0]["party"] == "Acme Corp"
    assert any(r["rule"] == "pending_approval" for r in dash["risks"])
    res = client.post(f"/dependencies/{dep['id']}/resolve", json={"note": "Lisa confirmed by email"}).json()
    assert res["status"] == "resolved" and "Lisa confirmed" in res["resolution"]
    dash = client.get("/manager/dashboard").json()
    assert not any(r["rule"] == "pending_approval" for r in dash["risks"])
    assert dash["approvals"]["granted"][0]["kind"] == "approval.granted"
    assert client.post("/commitments", json={"title": "x"}).status_code == 422
    assert client.post("/commitments", json={"title": "Valid title", "due": "soon"}).status_code == 422


def test_check_citations_drops_invalid_numbers():
    from app.llm import _TYPOGRAPHIC
    raw = "Blocked on Acme [2]. Due Friday [1, 7]. Owner Arjun \u30103-4\u3011.".translate(_TYPOGRAPHIC)
    text, info = check_citations(raw, 3)
    assert text == "Blocked on Acme [2]. Due Friday [1]. Owner Arjun [3]."
    assert info == {"cited": [1, 2, 3], "invalid_removed": [4, 7], "has_citations": True}


def test_prompt_budget_trims_history_before_current_state():
    from app.agent import PROMPT_TOKEN_BUDGET, _number_facts, fit_evidence
    state = [{"type": "current_state", "text": "S" * 450} for _ in range(10)]
    mems = [{"type": "world", "text": "M" * 2000, "occurred_start": f"2026-09-{10 + i}"} for i in range(24)]
    s, m = fit_evidence(state, mems, 2500)
    assert len(s) == 10 and 0 < len(m) < 24
    assert (2500 + len(_number_facts(s + m))) / 4 <= PROMPT_TOKEN_BUDGET
