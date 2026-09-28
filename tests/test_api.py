"""Offline tests: LocalStore memory + a scripted fake LLM. No network, no API keys."""
from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm import LLMError, extract_json
from app.main import build_services, create_app
from app.memory import LocalStore


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_extraction = False

    async def complete(self, system: str, user: str, *, label: str, temperature: float = 0.2) -> str:
        self.calls.append(label)
        if label.startswith("baseline"):
            return "I don't have access to your meetings."
        return "NW-240 is blocked on Acme approval [1]."

    async def complete_json(self, system: str, user: str, *, label: str) -> dict[str, Any]:
        self.calls.append(label)
        if label.startswith("extract"):
            if self.fail_extraction:
                raise LLMError("boom")
            items = []
            for ticket in sorted(set(re.findall(r"NW-\d+", user))):
                owner = "Arjun Mehta" if ticket == "NW-240" else "Priya Raman"
                status = "blocked" if ticket == "NW-240" and "BLOCKED" in user else "in_progress"
                items.append({"kind": "status_update", "ticket": ticket, "title": f"work on {ticket}",
                              "owner": owner, "status": status, "blocked_on": "Acme approval" if status == "blocked" else None,
                              "detail": f"{ticket} update"})
            return {"summary": "doc", "items": items}
        if label.startswith("meeting:summarise"):
            return {"summary": "Acme asked for higher rate limits; DB window approved for Sunday.",
                    "decisions": ["Maintenance window is Sunday 3-5 AM IST"],
                    "open_questions": ["JIT provisioning scope"],
                    "items": [
                        {"kind": "commitment", "scrum_stage": "sprint_review", "ticket": "NW-231",
                         "title": "Set up Acme sandbox at 1,200 req/min", "owner": "Priya Raman", "status": "todo",
                         "due": "2026-10-02", "customer": "Acme Corp", "detail": "burst 200, integer Retry-After"},
                        {"kind": "blocker", "scrum_stage": "bogus", "ticket": "NW-240", "title": "Rollback plan",
                         "owner": "Arjun", "blocked_on": "Acme sign-off"},
                        {"kind": "task", "scrum_stage": "product_backlog", "title": "JIT user provisioning",
                         "owner": None},
                        {"kind": "decision", "title": "Sunday window approved"},
                    ]}
        if label == "briefing":
            return {"headline": "Ship NW-231", "top_priority": {"task": "NW-231", "sources": [1]}, "my_tasks": [],
                    "blockers": [], "customer_commitments": [], "sops_to_follow": []}
        return {"summary": "ok", "health": "at_risk", "completed": [], "in_progress": [], "blocked": [],
                "customer_commitments": [], "workload": []}


class FakeTranscriber:
    engine = "groq-whisper"

    def __init__(self) -> None:
        self.prompts: list[str | None] = []

    async def transcribe(self, audio: bytes, filename: str, *, language=None, prompt=None) -> dict[str, Any]:
        self.prompts.append(prompt)
        segments = [{"start": 0.0, "end": 4.0, "text": " Priya will set up the Acme sandbox by Friday."},
                    {"start": 65.0, "end": 70.0, "text": " Arjun is blocked on NW-240 until Acme signs off."}]
        return {"text": "0:00:00 Priya will set up the Acme sandbox by Friday.\n"
                        "0:01:05 Arjun is blocked on NW-240 until Acme signs off.",
                "segments": segments, "language": "en", "duration_s": 70.0, "engine": self.engine}


@pytest.fixture
def ctx(tmp_path):
    settings = Settings(memory_backend="local", registry_file=str(tmp_path / "reg.json"), team_file="data/team.json",
                        groq_api_key=None, hindsight_api_key=None, meetings_dir=str(tmp_path / "meetings"),
                        max_audio_mb=0.001)
    llm = FakeLLM()
    services = build_services(settings, store=LocalStore(), llm=llm, transcriber=FakeTranscriber())
    with TestClient(create_app(services)) as client:
        yield client, services, llm


def _ingest(client, **over):
    body = {"source_type": "standup", "title": "Daily Standup",
            "content": "Arjun Mehta: BLOCKED — NW-240 waiting on Acme approval. Priya Raman: NW-231 in review.",
            "occurred_at": "2026-09-25T10:00:00+05:30", **over}
    r = client.post("/ingest", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_health_and_team(ctx):
    client, *_ = ctx
    assert client.get("/health").json()["memory"]["backend"] == "local"
    team = client.get("/team").json()
    assert {m["id"] for m in team["members"]} >= {"priya", "arjun", "neha"}


def test_ingest_retains_raw_and_tagged_facts(ctx):
    client, services, _ = ctx
    body = _ingest(client)
    assert body["facts_retained"] == 2
    assert set(body["people"]) == {"arjun", "priya"}
    bank = services.memory.store.banks[services.memory.bank_id]
    assert len(bank) == 3  # 1 raw document + 2 facts
    blocked = [m for m in bank if "status:blocked" in m["tags"]]
    assert blocked and "person:arjun" in blocked[0]["tags"] and "ticket:NW-240" in blocked[0]["tags"]
    assert client.get("/sources").json()[0]["facts_retained"] == 2


def test_reingest_same_document_is_idempotent(ctx):
    client, services, _ = ctx
    _ingest(client)
    _ingest(client)
    assert len(services.memory.store.banks[services.memory.bank_id]) == 3


def test_extraction_failure_still_retains_raw(ctx):
    client, services, llm = ctx
    llm.fail_extraction = True
    body = _ingest(client)
    assert body["facts_retained"] == 0 and body["extraction_error"]
    assert len(services.memory.store.banks[services.memory.bank_id]) == 1


def test_bad_source_type_rejected(ctx):
    client, *_ = ctx
    assert client.post("/ingest", json={"source_type": "tweet", "title": "x", "content": "y"}).status_code == 422


def test_employee_ask_uses_personal_recall_and_learns(ctx):
    client, services, _ = ctx
    _ingest(client)
    r = client.post("/employee/ask", json={"question": "Is NW-240 blocked?", "person_id": "arjun"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["memory_count"] > 0 and "[1]" in body["answer"]
    labels = [e["request"].get("label") for e in client.get("/inspector/events?op=recall").json()["events"]]
    assert "personal:arjun" in labels and "team-wide" in labels
    interactions = [m for m in services.memory.store.banks[services.memory.bank_id]
                    if "source:interaction" in m["tags"]]
    assert len(interactions) == 1


def test_unknown_person_404(ctx):
    client, *_ = ctx
    assert client.post("/employee/ask", json={"question": "hi there", "person_id": "nobody"}).status_code == 404


def test_feedback_correction_is_retained(ctx):
    client, services, _ = ctx
    _ingest(client)
    iid = client.post("/employee/ask", json={"question": "NW-240 status?", "person_id": "arjun"}).json()["interaction_id"]
    r = client.post("/feedback", json={"interaction_id": iid, "helpful": False,
                                       "correction": "Acme approved Sunday 03:00 IST", "person_id": "arjun"})
    assert r.status_code == 200
    texts = [m["text"] for m in services.memory.store.banks[services.memory.bank_id]]
    assert any("CORRECTION" in t and "Sunday" in t for t in texts)
    assert client.post("/feedback", json={"interaction_id": "missing"}).status_code == 404


def test_compare_baseline_vs_memory(ctx):
    client, *_ = ctx
    _ingest(client)
    body = client.post("/demo/compare", json={"question": "What is blocked on NW-240?"}).json()
    assert body["without_memory"]["memories"] == []
    assert body["with_memory"]["memory_count"] > 0


def test_radar_falls_back_to_llm_structuring(ctx):
    client, *_ = ctx
    _ingest(client)
    body = client.get("/manager/radar").json()
    assert body["report"]["health"] == "at_risk"
    assert any(e["op"] == "reflect" and e["status"] == "ok" for e in client.get("/inspector/events").json()["events"])


def test_briefing(ctx):
    client, *_ = ctx
    _ingest(client)
    body = client.get("/employee/priya/briefing").json()
    assert body["briefing"]["headline"] == "Ship NW-231"
    assert client.get("/employee/ghost/briefing").status_code == 404


def test_sop_ask_scoped_to_sops(ctx):
    client, *_ = ctx
    _ingest(client)
    _ingest(client, source_type="sop", title="SOP-004 Hotfix", content="Hotfix: canary 10% for 15 minutes.")
    body = client.post("/sops/ask", json={"question": "hotfix canary"}).json()
    assert body["memories"] and all("source:sop" in m["tags"] for m in body["memories"])
    assert len(client.get("/sops").json()) == 1


def test_llm_missing_returns_503(tmp_path):
    settings = Settings(memory_backend="local", registry_file=str(tmp_path / "r.json"), groq_api_key=None)
    with TestClient(create_app(build_services(settings, store=LocalStore()))) as client:
        assert client.post("/demo/baseline", json={"question": "hello"}).status_code == 503


def test_seed_dataset_loads(ctx):
    from app.seed import load_documents

    docs = load_documents(include_demo=True)
    assert len(docs) == 13 and docs == sorted(docs, key=lambda d: d["occurred_at"])
    assert all(d["occurred_at"].weekday() < 5 for d in docs)


@pytest.mark.parametrize("raw,expected", [
    ('{"a": 1}', {"a": 1}),
    ('<think>hmm</think>\n```json\n{"a": 2}\n```', {"a": 2}),
    ('Sure! Here it is: {"a": {"b": 3}} hope it helps', {"a": {"b": 3}}),
    ('{"ticket": "NW\u2011240", "s": "Sprint\u202f15"}', {"ticket": "NW-240", "s": "Sprint 15"}),
])
def test_extract_json(raw, expected):
    assert extract_json(raw) == expected


def test_extract_json_raises():
    with pytest.raises(LLMError):
        extract_json("no json here")


# ------------------------------------------------------------------ meetings
def _record(client, audio=b"x" * 500, **form):
    data = {"title": "Acme weekly sync", "meeting_type": "customer_sync", "customer": "Acme Corp",
            "occurred_at": "2026-09-25T16:00:00+05:30", **form}
    return client.post("/meetings/record", data=data, files={"audio": ("meeting.webm", audio, "audio/webm")})


def test_record_meeting_creates_scrum_actions_and_memories(ctx):
    client, services, _ = ctx
    r = _record(client)
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["summary"].startswith("Acme asked") and m["decisions"] and m["transcription"]["engine"] == "groq-whisper"
    assert "0:01:05" in m["transcript"]
    actions = {a["title"]: a for a in m["actions"]}
    assert len(actions) == 3  # the decision is retained as a fact, not an action
    assert actions["Rollback plan"]["scrum_stage"] == "impediment" and actions["Rollback plan"]["status"] == "blocked"
    assert actions["Rollback plan"]["owner_id"] == "arjun"
    assert actions["JIT user provisioning"]["scrum_stage"] == "product_backlog"

    bank = services.memory.store.banks[services.memory.bank_id]
    raw = [x for x in bank if x["document_id"] == m["id"]]
    assert raw and "\n\nSUMMARY: Acme asked" in raw[0]["text"]
    facts = [x for x in bank if x["document_id"] == f"{m['id']}::facts"]
    assert len(facts) == 4
    sandbox = next(x for x in facts if "sandbox" in x["text"])
    assert {"scrum:sprint_review", "person:priya", "customer:acme", "ticket:NW-231"} <= set(sandbox["tags"])
    assert sandbox["metadata"]["action_id"] == actions["Set up Acme sandbox at 1,200 req/min"]["id"]

    retains = [e for e in client.get("/inspector/events?op=retain").json()["events"] if e["status"] == "ok"]
    assert [e["request"]["document_id"] for e in retains] == [m["id"], f"{m['id']}::facts"]
    assert "Priya Raman" in services.meetings.transcriber.prompts[0]  # Whisper is primed with the roster


def test_action_status_update_is_retained(ctx):
    client, services, _ = ctx
    m = _record(client).json()
    action = next(a for a in m["actions"] if a["owner_id"] == "priya")
    r = client.patch(f"/actions/{action['id']}", json={"status": "done", "note": "sandbox live", "person_id": "priya"})
    assert r.status_code == 200, r.text
    assert r.json()["action"]["status"] == "done"
    assert "status: done" in r.json()["memory"] and "sandbox live" in r.json()["memory"]
    update = [x for x in services.memory.store.banks[services.memory.bank_id] if f"action:{action['id']}" in x["tags"]]
    assert update and "person:priya" in update[0]["tags"] and "source:task_update" in update[0]["tags"]

    saved = client.get(f"/meetings/{m['id']}").json()
    history = next(a for a in saved["actions"] if a["id"] == action["id"])["history"]
    assert [h["status"] for h in history] == ["todo", "done"] and history[-1]["by"] == "Priya Raman"

    assert client.get("/actions", params={"status": "done"}).json()["count"] == 1
    assert client.get("/actions", params={"owner": "arjun", "stage": "impediment"}).json()["count"] == 1
    assert client.patch(f"/actions/{action['id']}", json={"status": "nope"}).status_code == 422
    assert client.patch("/actions/missing-01", json={"status": "done"}).status_code == 404
    assert client.patch(f"/actions/{action['id']}", json={"owner": "ghost"}).status_code == 422


def test_pasted_transcript_meeting_and_listing(ctx):
    client, *_ = ctx
    r = client.post("/meetings", json={"transcript": "Neha: Priya owns the sandbox.", "title": "Daily standup",
                                        "meeting_type": "standup"})
    assert r.status_code == 200, r.text
    assert r.json()["source_type"] == "standup" and r.json()["transcription"] is None
    listed = client.get("/meetings").json()
    assert len(listed) == 1 and "transcript" not in listed[0]
    assert client.get("/meetings/nope").status_code == 404
    assert client.post("/meetings", json={"transcript": "x", "title": "t", "meeting_type": "party"}).status_code == 422


def test_audio_too_large_and_empty(ctx):
    client, *_ = ctx
    assert _record(client, audio=b"x" * 5000).status_code == 413
    assert _record(client, audio=b"").status_code == 422


def test_meeting_without_transcriber_returns_503(tmp_path):
    settings = Settings(memory_backend="local", registry_file=str(tmp_path / "r.json"), groq_api_key=None,
                        meetings_dir=str(tmp_path / "m"))
    services = build_services(settings, store=LocalStore(), llm=FakeLLM())
    assert services.meetings.transcriber is None and "GROQ_API_KEY" in services.transcriber_error
    with TestClient(create_app(services)) as client:
        assert _record(client).status_code == 503
        assert client.get("/health").json()["transcriber"]["engine"] is None


def test_meeting_summary_failure_keeps_transcript(ctx):
    client, services, llm = ctx

    async def boom(*a, **k):
        raise LLMError("rate limited")
    llm.complete_json = boom
    m = _record(client).json()
    assert m["actions"] == [] and "rate limited" in m["extraction_error"]
    assert any(x["document_id"] == m["id"] for x in services.memory.store.banks[services.memory.bank_id])


def test_long_transcript_is_chunked(ctx):
    client, services, llm = ctx
    services.meetings.chunk_chars = 60
    text = "\n".join(f"0:0{i}:00 line number {i} about the Acme sandbox" for i in range(6))
    m = client.post("/meetings", json={"transcript": text, "title": "Long sync"}).json()
    labels = [c for c in llm.calls if c.startswith("meeting:")]
    assert len(labels) > 2 and labels[-1] == "meeting:condense"
    assert len({a["id"] for a in m["actions"]}) == len(m["actions"])


def test_web_ui_served(ctx):
    client, *_ = ctx
    r = client.get("/")
    assert r.status_code == 200 and "/static/app.js?v=" in r.text
    assert "MediaRecorder" in client.get("/static/app.js").text
    assert client.get("/static/app.css").status_code == 200
    r = client.get("/recorder", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/#/meetings/new"
    assert set(client.get("/meetings/options").json()["scrum_stages"]) >= {"sprint_backlog", "impediment"}
