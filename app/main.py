"""SprintMind HTTP API (FastAPI).

Run:  uvicorn app.main:app --reload --port 8000     Docs: http://localhost:8000/docs
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import SprintMindAgent
from .config import Settings, get_settings
from .ingest import SOURCE_TYPES, Ingestor, Registry
from .inspector import Inspector
from .llm import LLM, GroqLLM, LLMError
from .meetings import ACTION_STATUSES, MEETING_TYPES, SCRUM_STAGES, AudioTooLarge, MeetingService, MeetingStore
from .memory import HindsightStore, LocalStore, MemoryService, MemoryStore
from .seed import demo_transcript, load_documents
from .team import Team
from .transcribe import GroqWhisper, LocalWhisper, Transcriber, TranscriptionError

STATIC_DIR = Path(__file__).resolve().parent / "static"


# ----------------------------------------------------------------------------- request models
class IngestRequest(BaseModel):
    source_type: Literal[tuple(sorted(SOURCE_TYPES))] = Field(..., examples=["meeting"])  # type: ignore[valid-type]
    title: str = Field(..., examples=["Acme Corp weekly sync (MS Teams)"])
    content: str = Field(..., min_length=1)
    occurred_at: datetime | None = None
    customer: str | None = Field(None, examples=["Acme Corp"])
    participants: list[str] | None = None
    sprint: str | None = None
    document_id: str | None = None

class AskRequest(BaseModel):
    question: str = Field(..., min_length=2)
    person_id: str | None = Field(None, examples=["priya"])
    budget: Literal["low", "mid", "high"] = "mid"

class QuestionRequest(BaseModel):
    question: str = Field(..., min_length=2)

class FeedbackRequest(BaseModel):
    interaction_id: str
    helpful: bool = True
    correction: str | None = None
    person_id: str | None = None

class MeetingRequest(BaseModel):
    transcript: str = Field(..., min_length=1, description="Pasted transcript (Teams/Zoom export or notes)")
    title: str = Field(..., examples=["Acme Corp weekly sync"])
    meeting_type: Literal[tuple(sorted(MEETING_TYPES))] = "other"  # type: ignore[valid-type]
    occurred_at: datetime | None = None
    customer: str | None = None
    participants: list[str] | None = None

class ActionUpdate(BaseModel):
    status: Literal[ACTION_STATUSES] | None = None  # type: ignore[valid-type]
    owner: str | None = Field(None, description="Name or person id")
    due: str | None = Field(None, examples=["2026-10-02"])
    note: str | None = None
    person_id: str | None = Field(None, description="Who made the change")

class RecallRequest(BaseModel):
    query: str
    tags: list[str] | None = None
    tags_match: Literal["any", "all", "any_strict", "all_strict", "exact"] = "any"
    types: list[Literal["world", "experience", "observation"]] | None = None
    budget: Literal["low", "mid", "high"] = "mid"


# ----------------------------------------------------------------------------- wiring
@dataclass
class Services:
    settings: Settings
    inspector: Inspector
    memory: MemoryService
    llm: LLM | None
    team: Team
    registry: Registry
    ingestor: Ingestor
    agent: SprintMindAgent
    meetings: MeetingService
    transcriber_error: str | None = None


def build_transcriber(settings: Settings, inspector: Inspector) -> tuple[Transcriber | None, str | None]:
    try:
        if settings.transcriber == "groq":
            return GroqWhisper(settings.groq_api_key, settings.groq_base_url, settings.groq_transcribe_model,
                               settings.llm_timeout, inspector), None
        if settings.transcriber == "local":
            return LocalWhisper(settings.local_whisper_model, inspector), None
        return None, "TRANSCRIBER=none"
    except TranscriptionError as exc:
        return None, str(exc)


def build_services(settings: Settings, store: MemoryStore | None = None, llm: LLM | None = None,
                   transcriber: Transcriber | None = None) -> Services:
    inspector = Inspector()
    if store is None:
        store = (LocalStore() if settings.memory_backend == "local" else
                 HindsightStore(settings.hindsight_base_url, settings.hindsight_api_key,
                                settings.hindsight_timeout, settings.retain_async))
    if llm is None and settings.groq_api_key:
        llm = GroqLLM(settings.groq_api_key, settings.groq_base_url, settings.groq_model,
                      settings.groq_fallback_model, settings.llm_timeout, inspector, settings.llm_max_tokens)
    team = Team.load(settings.team_file, settings.team_name)
    memory = MemoryService(store, inspector, settings.bank_id, team.name)
    registry = Registry(settings.registry_file)
    transcriber_error = None
    if transcriber is None:
        transcriber, transcriber_error = build_transcriber(settings, inspector)
    ingestor = Ingestor(memory, llm, team, registry, settings.current_sprint)
    return Services(
        settings=settings, inspector=inspector, memory=memory, llm=llm, team=team, registry=registry,
        ingestor=ingestor,
        agent=SprintMindAgent(memory, llm, team, settings.current_sprint, settings.learn_from_interactions),
        meetings=MeetingService(ingestor, llm, transcriber, team, MeetingStore(settings.meetings_dir),
                                max_audio_mb=settings.max_audio_mb, keep_audio=settings.keep_audio),
        transcriber_error=transcriber_error,
    )


def create_app(services: Services | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.svc = services or build_services(get_settings())
        yield
        await app.state.svc.memory.store.close()

    app = FastAPI(
        title="SprintMind API",
        version="1.0.0",
        description="Scrum task & delivery memory copilot built on Hindsight (retain / recall / reflect) and Groq.",
        lifespan=lifespan,
    )
    origins = (services.settings if services else get_settings()).cors_origins
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in origins.split(",")],
                       allow_methods=["*"], allow_headers=["*"])

    def svc(request: Request) -> Services:
        return request.app.state.svc

    async def guard(coro):
        """Map backend failures to clean HTTP errors instead of 500 stack traces."""
        try:
            return await coro
        except KeyError as exc:
            raise HTTPException(404, f"not found: {exc.args[0]}") from exc
        except AudioTooLarge as exc:
            raise HTTPException(413, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (LLMError, RuntimeError) as exc:
            raise HTTPException(503, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - upstream (Hindsight/Groq) errors
            raise HTTPException(502, f"upstream error: {type(exc).__name__}: {exc}") from exc

    # ------------------------------------------------------------------ web UI
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> HTMLResponse:
        # Version asset URLs by mtime so browsers never run a stale app.js / app.css after an update.
        html = (STATIC_DIR / "index.html").read_text()
        for name in ("app.css", "app.js"):
            html = html.replace(f"/static/{name}", f"/static/{name}?v={int((STATIC_DIR / name).stat().st_mtime)}")
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    # ------------------------------------------------------------------ system
    @app.get("/health", tags=["system"])
    async def health(request: Request) -> dict[str, Any]:
        s = svc(request)
        try:
            mem = await asyncio.wait_for(s.memory.health(), timeout=10)
            mem_ok = True
        except Exception as exc:  # noqa: BLE001
            mem, mem_ok = {"error": str(exc)}, False
        return {"ok": mem_ok and s.llm is not None, "bank_id": s.memory.bank_id, "memory": mem,
                "llm": {"configured": s.llm is not None, "model": s.settings.groq_model},
                "transcriber": {"engine": getattr(s.meetings.transcriber, "engine", None),
                                "error": s.transcriber_error},
                "sprint": s.settings.current_sprint}

    @app.post("/admin/setup", tags=["system"], summary="Create/configure the Hindsight bank (mission, directives, mental models)")
    async def setup(request: Request) -> dict[str, Any]:
        return await guard(svc(request).memory.setup())

    @app.post("/admin/reset", tags=["system"], summary="Delete the bank and the local source registry")
    async def reset(request: Request) -> dict[str, Any]:
        s = svc(request)
        await guard(s.memory.reset())
        s.registry.clear()
        s.meetings.store.clear()
        s.inspector.clear()
        return {"reset": True, "bank_id": s.memory.bank_id}

    @app.post("/admin/seed", tags=["system"], summary="Load the synthetic Sprint 14 dataset (SOPs, standups, planning, incident)")
    async def seed(request: Request, include_demo: bool = False) -> dict[str, Any]:
        """Keep include_demo=false for the live demo: the Acme sync is pasted in on stage."""
        s = svc(request)
        await guard(s.memory.setup())
        results = []
        for doc in load_documents(include_demo=include_demo):
            r = await guard(s.ingestor.ingest(**doc))
            results.append({k: r[k] for k in ("document_id", "title", "source_type", "occurred_at", "facts_retained",
                                              "extraction_error")})
        return {"ingested": len(results), "documents": results}

    @app.get("/demo/transcript", tags=["demo"], summary="The Acme Teams sync transcript to paste during the demo")
    async def transcript() -> dict[str, Any]:
        return demo_transcript()

    # ------------------------------------------------------------------ team
    @app.get("/team", tags=["team"])
    async def team(request: Request) -> dict[str, Any]:
        t = svc(request).team
        return {"team": t.name, "members": t.members, "customers": t.customers}

    # ---------------------------------------------------------------- ingest
    @app.post("/ingest", tags=["ingest"], summary="Retain a standup / meeting transcript / SOP / ticket update")
    async def ingest(body: IngestRequest, request: Request) -> dict[str, Any]:
        return await guard(svc(request).ingestor.ingest(**body.model_dump()))

    @app.post("/ingest/batch", tags=["ingest"])
    async def ingest_batch(body: list[IngestRequest], request: Request) -> dict[str, Any]:
        s = svc(request)
        results = []
        for doc in body:  # sequential keeps timestamps/evolution ordered and avoids rate limits
            results.append(await guard(s.ingestor.ingest(**doc.model_dump())))
        return {"ingested": len(results), "documents": results}

    @app.get("/sources", tags=["ingest"], summary="Documents ingested so far")
    async def sources(request: Request, source_type: str | None = None) -> list[dict[str, Any]]:
        return svc(request).registry.list(source_type)

    # -------------------------------------------------------- employee portal
    @app.post("/employee/ask", tags=["employee"], summary="Ask anything; answered from recalled memories with citations")
    async def employee_ask(body: AskRequest, request: Request) -> dict[str, Any]:
        s = svc(request)
        if body.person_id and not s.team.member(body.person_id):
            raise HTTPException(404, f"unknown person_id {body.person_id}")
        return await guard(s.agent.ask(body.question, body.person_id, budget=body.budget))

    @app.get("/employee/{person_id}/briefing", tags=["employee"], summary="Personal 'What's next?' briefing")
    async def briefing(person_id: str, request: Request) -> dict[str, Any]:
        return await guard(svc(request).agent.briefing(person_id))

    # ---------------------------------------------------------- manager radar
    @app.get("/manager/radar", tags=["manager"], summary="Sprint health, blockers, commitments, workload (reflect)")
    async def radar(request: Request, budget: Literal["low", "mid", "high"] = "mid") -> dict[str, Any]:
        return await guard(svc(request).agent.radar(budget))

    @app.post("/manager/ask", tags=["manager"], summary="Free-form manager question answered by reflect()")
    async def manager_ask(body: QuestionRequest, request: Request) -> dict[str, Any]:
        return await guard(svc(request).agent.manager_ask(body.question))

    # --------------------------------------------------------------- SOP vault
    @app.get("/sops", tags=["sop"])
    async def sops(request: Request) -> list[dict[str, Any]]:
        return svc(request).registry.list("sop")

    @app.post("/sops/ask", tags=["sop"])
    async def sop_ask(body: QuestionRequest, request: Request) -> dict[str, Any]:
        return await guard(svc(request).agent.sop_ask(body.question))

    # ------------------------------------------------------------ demo helpers
    @app.post("/demo/baseline", tags=["demo"], summary="Same question to the LLM with NO memory")
    async def baseline(body: QuestionRequest, request: Request) -> dict[str, Any]:
        return await guard(svc(request).agent.baseline(body.question))

    @app.post("/demo/compare", tags=["demo"], summary="Side-by-side: without memory vs with Hindsight memory")
    async def compare(body: AskRequest, request: Request) -> dict[str, Any]:
        return await guard(svc(request).agent.compare(body.question, body.person_id))

    @app.post("/feedback", tags=["learning"], summary="Rate or correct an answer; corrections are retained")
    async def feedback(body: FeedbackRequest, request: Request) -> dict[str, Any]:
        return await guard(svc(request).agent.feedback(
            body.interaction_id, body.helpful, body.correction, body.person_id))

    # --------------------------------------------------- meetings: record → actions
    @app.get("/recorder", include_in_schema=False)
    async def recorder() -> RedirectResponse:
        return RedirectResponse("/#/meetings/new")

    @app.get("/meetings/options", tags=["meetings"], summary="Meeting types, scrum stages and action statuses")
    async def meeting_options() -> dict[str, Any]:
        return {"meeting_types": list(MEETING_TYPES), "scrum_stages": SCRUM_STAGES,
                "action_statuses": list(ACTION_STATUSES)}

    @app.post("/meetings/transcribe", tags=["meetings"], summary="Speech-to-text only (free Whisper); nothing is retained")
    async def transcribe(request: Request, audio: UploadFile = File(...),
                         language: str | None = Form(None)) -> dict[str, Any]:
        data = await audio.read()
        return await guard(svc(request).meetings.transcribe(data, audio.filename or "meeting.webm", language))

    @app.post("/meetings/record", tags=["meetings"],
              summary="Upload a recording: transcribe → summary + scrum action events → Hindsight retain")
    async def record_meeting(
        request: Request,
        audio: UploadFile = File(..., description="webm/ogg/mp3/m4a/wav recording"),
        title: str = Form(...),
        meeting_type: str = Form("other"),
        occurred_at: datetime | None = Form(None),
        customer: str | None = Form(None),
        participants: str | None = Form(None, description="Comma-separated names"),
        language: str | None = Form(None, description="ISO code like 'en'; auto-detect when empty"),
    ) -> dict[str, Any]:
        data = await audio.read()
        people = [p.strip() for p in (participants or "").split(",") if p.strip()] or None
        return await guard(svc(request).meetings.process_audio(
            data, audio.filename or "meeting.webm", language=language or None, title=title,
            meeting_type=meeting_type, occurred_at=occurred_at, customer=customer or None, participants=people))

    @app.post("/meetings", tags=["meetings"], summary="Same pipeline for a pasted transcript (no audio)")
    async def create_meeting(body: MeetingRequest, request: Request) -> dict[str, Any]:
        return await guard(svc(request).meetings.process(**body.model_dump()))

    @app.get("/meetings", tags=["meetings"], summary="Saved meetings with their summaries (no transcripts)")
    async def list_meetings(request: Request) -> list[dict[str, Any]]:
        return [{k: v for k, v in m.items() if k not in ("transcript", "segments")}
                for m in svc(request).meetings.store.list()]

    @app.get("/meetings/{meeting_id}", tags=["meetings"])
    async def get_meeting(meeting_id: str, request: Request) -> dict[str, Any]:
        meeting = svc(request).meetings.store.get(meeting_id)
        if meeting is None:
            raise HTTPException(404, f"unknown meeting {meeting_id}")
        return meeting

    @app.get("/actions", tags=["meetings"], summary="Scrum action board across all meetings")
    async def list_actions(request: Request, status: str | None = None, owner: str | None = None,
                           stage: str | None = None, meeting_id: str | None = None) -> dict[str, Any]:
        actions = svc(request).meetings.list_actions(status=status, owner=owner, stage=stage, meeting_id=meeting_id)
        return {"count": len(actions), "actions": actions}

    @app.patch("/actions/{action_id}", tags=["meetings"],
               summary="Move an action through the sprint; the change is retained in Hindsight")
    async def update_action(action_id: str, body: ActionUpdate, request: Request) -> dict[str, Any]:
        return await guard(svc(request).meetings.update_action(action_id, **body.model_dump()))

    # ------------------------------------------------------ memory (raw access)
    @app.post("/memory/recall", tags=["memory"], summary="Raw Hindsight recall (context verification panel)")
    async def recall(body: RecallRequest, request: Request) -> dict[str, Any]:
        facts = await guard(svc(request).memory.recall(
            body.query, tags=body.tags, tags_match=body.tags_match, types=body.types,
            budget=body.budget, label="manual recall"))
        return {"count": len(facts), "memories": facts}

    @app.get("/memory/list", tags=["memory"])
    async def list_memories(
        request: Request,
        type: Literal["world", "experience", "observation"] | None = None,
        q: str | None = None,
        limit: int = Query(50, le=500),
        offset: int = 0,
    ) -> dict[str, Any]:
        kw: dict[str, Any] = {"limit": limit, "offset": offset}
        if type:
            kw["type"] = type
        if q:
            kw["search_query"] = q
        items = await guard(svc(request).memory.list_memories(**kw))
        return {"count": len(items), "memories": items}

    # ------------------------------------------------------- live inspector
    @app.get("/inspector/events", tags=["inspector"], summary="Memory operations log (poll with ?after=<last id>)")
    async def events(request: Request, after: int = 0, limit: int = Query(200, le=500),
                     op: str | None = None) -> dict[str, Any]:
        s = svc(request)
        ev = s.inspector.events(after=after, limit=limit, op=op)
        return {"events": ev, "last_id": ev[-1]["id"] if ev else after, "stats": s.inspector.stats()}

    @app.get("/inspector/stream", tags=["inspector"], summary="Server-sent events stream of memory operations")
    async def stream(request: Request) -> StreamingResponse:
        s = svc(request)

        async def gen():
            yield ": connected\n\n"
            async for event in s.inspector.subscribe():
                if await request.is_disconnected():
                    break
                yield f"id: {event['id']}\nevent: {event['op']}\ndata: {json.dumps(event, default=str)}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.delete("/inspector/events", tags=["inspector"])
    async def clear_events(request: Request) -> dict[str, bool]:
        svc(request).inspector.clear()
        return {"cleared": True}

    return app


app = create_app()
