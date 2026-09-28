"""SprintMind HTTP API (FastAPI).

Run:  uvicorn app.main:app --reload --port 8000     Docs: http://localhost:8000/docs

Access (see auth.py): the web UI shell, /health, /team, /meetings/options and the signed GitHub
webhook are public. Everything that returns team data needs an identified team member; the delivery
dashboard, risks, events, raw memory access, admin and demo triggers need a manager.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import demo as demo_flow
from .agent import SprintMindAgent
from .auth import (Caller, Forbidden, Unauthenticated, can_edit_item, parse_tokens, require_manager, require_person,
                   require_self_or_manager, resolve_caller)
from .config import Settings, get_settings
from .delivery import DeliveryService
from .github import SignatureError, demo_payload, verify_signature
from .ingest import SOURCE_TYPES, Ingestor, Registry
from .inspector import Inspector
from .ledger import ITEM_STATUSES, Ledger
from .llm import LLM, GroqLLM, LLMError
from .meetings import MEETING_TYPES, SCRUM_STAGES, AudioTooLarge, MeetingService, MeetingStore
from .memory import HindsightStore, LocalStore, MemoryService, MemoryStore
from .risk import RULES
from .seed import demo_transcript, load_documents
from .team import Team
from .transcribe import GroqWhisper, LocalWhisper, Transcriber, TranscriptionError

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_WEBHOOK_BYTES = 5 * 1024 * 1024
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
TICKET_PATTERN = r"^[A-Za-z][A-Za-z0-9]{1,9}-\d{1,6}$"


# ----------------------------------------------------------------------------- request models
class IngestRequest(BaseModel):
    source_type: Literal[tuple(sorted(SOURCE_TYPES))] = Field(..., examples=["meeting"])  # type: ignore[valid-type]
    title: str = Field(..., min_length=1, max_length=300, examples=["Acme Corp weekly sync (MS Teams)"])
    content: str = Field(..., min_length=1, max_length=200_000)
    occurred_at: datetime | None = None
    customer: str | None = Field(None, max_length=120, examples=["Acme Corp"])
    participants: list[str] | None = Field(None, max_length=50)
    sprint: str | None = Field(None, max_length=20)
    document_id: str | None = Field(None, max_length=200)

class AskRequest(BaseModel):
    question: str = Field(..., min_length=2, max_length=2000)
    person_id: str | None = Field(None, max_length=60, examples=["priya"])
    budget: Literal["low", "mid", "high"] = "mid"

class QuestionRequest(BaseModel):
    question: str = Field(..., min_length=2, max_length=2000)

class FeedbackRequest(BaseModel):
    interaction_id: str = Field(..., max_length=40)
    helpful: bool = True
    correction: str | None = Field(None, max_length=2000)

class MeetingRequest(BaseModel):
    transcript: str = Field(..., min_length=1, max_length=400_000, description="Pasted transcript (Teams/Zoom export or notes)")
    title: str = Field(..., min_length=1, max_length=300, examples=["Acme Corp weekly sync"])
    meeting_type: Literal[tuple(sorted(MEETING_TYPES))] = "other"  # type: ignore[valid-type]
    occurred_at: datetime | None = None
    customer: str | None = Field(None, max_length=120)
    participants: list[str] | None = Field(None, max_length=50)

class ActionUpdate(BaseModel):
    status: Literal[ITEM_STATUSES] | None = None  # type: ignore[valid-type]
    owner: str | None = Field(None, max_length=120, description="Name or person id")
    due: str | None = Field(None, pattern=DATE_PATTERN, examples=["2026-10-02"])
    ticket: str | None = Field(None, pattern=TICKET_PATTERN, examples=["NW-231"])
    note: str | None = Field(None, max_length=1000)

class CommitmentRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=300)
    customer: str | None = Field(None, max_length=120)
    owner: str | None = Field(None, max_length=120)
    due: str | None = Field(None, pattern=DATE_PATTERN)
    ticket: str | None = Field(None, pattern=TICKET_PATTERN)
    detail: str | None = Field(None, max_length=1000)

class DependencyRequest(BaseModel):
    description: str = Field(..., min_length=3, max_length=400)
    kind: Literal["approval", "external", "item"] | None = None
    party: str | None = Field(None, max_length=120)
    depends_on: str | None = Field(None, pattern=TICKET_PATTERN, description="Ticket this item depends on")

class ResolveRequest(BaseModel):
    note: str | None = Field(None, max_length=400)

class DemoGithubRequest(BaseModel):
    kind: Literal["pr_opened", "pr_merged", "ci_failed", "ci_passed"]
    ticket: str = Field(..., pattern=TICKET_PATTERN)
    pr_number: int = Field(..., ge=1, le=10_000_000)
    title: str = Field("Demo change", max_length=200)
    branch: str | None = Field(None, max_length=200)
    check_name: str = Field("ci / test", max_length=100)

class RecallRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    tags: list[str] | None = Field(None, max_length=20)
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
    delivery: DeliveryService
    tokens: dict[str, str]
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
                   transcriber: Transcriber | None = None, clock: Any = None) -> Services:
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
    delivery = DeliveryService(Ledger(settings.ledger_path), memory, team, sprint=settings.current_sprint,
                               ticket_prefixes=settings.ticket_prefixes, repo=settings.github_repo,
                               tz=settings.team_timezone, ticket_url_template=settings.ticket_url_template, llm=llm,
                               **({"clock": clock} if clock else {}))
    ingestor = Ingestor(memory, llm, team, registry, settings.current_sprint, delivery=delivery)
    return Services(
        settings=settings, inspector=inspector, memory=memory, llm=llm, team=team, registry=registry,
        ingestor=ingestor, delivery=delivery, tokens=parse_tokens(settings.api_tokens),
        agent=SprintMindAgent(memory, llm, team, settings.current_sprint, settings.learn_from_interactions,
                              delivery=delivery, extract=ingestor.extract),
        meetings=MeetingService(ingestor, llm, transcriber, team, MeetingStore(settings.meetings_dir),
                                max_audio_mb=settings.max_audio_mb, keep_audio=settings.keep_audio, delivery=delivery),
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
        version="2.0.0",
        description="Engineering delivery intelligence over your existing tools: commitments, GitHub activity and "
                    "Hindsight memory (retain / recall / reflect) connected into explainable delivery risks.",
        lifespan=lifespan,
    )
    origins = (services.settings if services else get_settings()).cors_origins
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in origins.split(",")],
                       allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(Unauthenticated)
    async def _unauth(_: Request, exc: Unauthenticated) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=401)

    @app.exception_handler(Forbidden)
    async def _forbidden(_: Request, exc: Forbidden) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=403)

    def svc(request: Request) -> Services:
        return request.app.state.svc

    def caller(request: Request) -> Caller:
        s = svc(request)
        return resolve_caller(request.headers, s.settings.auth_mode, s.team, s.tokens)

    def person(c: Caller = Depends(caller)) -> Caller:
        require_person(c)
        return c

    def manager(c: Caller = Depends(caller)) -> Caller:
        require_manager(c)
        return c

    async def guard(coro):
        """Map backend failures to clean HTTP errors instead of 500 stack traces."""
        try:
            return await coro
        except (Unauthenticated, Forbidden):
            raise
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

    @app.get("/recorder", include_in_schema=False)
    async def recorder() -> RedirectResponse:
        return RedirectResponse("/#/meetings/new")

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
                "github": {"webhook_configured": bool(s.settings.github_webhook_secret), "repo": s.settings.github_repo},
                "auth_mode": s.settings.auth_mode, "sprint": s.settings.current_sprint}

    @app.get("/me", tags=["system"], summary="Who the API thinks you are")
    async def me(c: Caller = Depends(caller)) -> dict[str, Any]:
        return {"person_id": c.person_id, "name": c.name, "role": c.role}

    @app.post("/admin/setup", tags=["system"], summary="Create/configure the Hindsight bank (mission, directives, mental models)")
    async def setup(request: Request, _: Caller = Depends(manager)) -> dict[str, Any]:
        return await guard(svc(request).memory.setup())

    @app.post("/admin/reset", tags=["system"], summary="Delete the bank, the ledger and the local source registry")
    async def reset(request: Request, _: Caller = Depends(manager)) -> dict[str, Any]:
        s = svc(request)
        await guard(s.memory.reset())
        s.registry.clear()
        s.meetings.store.clear()
        s.delivery.ledger.reset()
        s.inspector.clear()
        return {"reset": True, "bank_id": s.memory.bank_id}

    @app.post("/admin/seed", tags=["system"], summary="Load the synthetic Sprint 14 dataset (SOPs, standups, planning, incident)")
    async def seed(request: Request, include_demo: bool = False, _: Caller = Depends(manager)) -> dict[str, Any]:
        """Keep include_demo=false for the live demo: the Acme sync is pasted in on stage."""
        s = svc(request)
        await guard(s.memory.setup())
        results = []
        for doc in load_documents(include_demo=include_demo):
            r = await guard(s.ingestor.ingest(**doc))
            results.append({k: r[k] for k in ("document_id", "title", "source_type", "occurred_at", "facts_retained",
                                              "extraction_error")})
        return {"ingested": len(results), "documents": results}

    # ------------------------------------------------------------------ team
    @app.get("/team", tags=["team"])
    async def team(request: Request) -> dict[str, Any]:
        t = svc(request).team
        return {"team": t.name, "members": t.members, "customers": t.customers}

    # ---------------------------------------------------------------- ingest
    @app.post("/ingest", tags=["ingest"], summary="Retain a standup / meeting transcript / SOP / ticket update")
    async def ingest(body: IngestRequest, request: Request, _: Caller = Depends(person)) -> dict[str, Any]:
        return await guard(svc(request).ingestor.ingest(**body.model_dump()))

    @app.post("/ingest/batch", tags=["ingest"])
    async def ingest_batch(body: list[IngestRequest], request: Request, _: Caller = Depends(person)) -> dict[str, Any]:
        if len(body) > 50:
            raise HTTPException(422, "at most 50 documents per batch")
        s = svc(request)
        results = []
        for doc in body:  # sequential keeps timestamps/evolution ordered and avoids rate limits
            results.append(await guard(s.ingestor.ingest(**doc.model_dump())))
        return {"ingested": len(results), "documents": results}

    @app.get("/sources", tags=["ingest"], summary="Documents ingested so far")
    async def sources(request: Request, source_type: str | None = None, _: Caller = Depends(person)) -> list[dict[str, Any]]:
        return svc(request).registry.list(source_type)

    # -------------------------------------------------------- employee portal
    @app.post("/employee/ask", tags=["employee"], summary="Ask anything; answered from state + recalled memories, with citations")
    async def employee_ask(body: AskRequest, request: Request, c: Caller = Depends(person)) -> dict[str, Any]:
        s = svc(request)
        if body.person_id and not s.team.member(body.person_id):
            raise HTTPException(404, f"unknown person_id {body.person_id}")
        require_self_or_manager(c, body.person_id)
        return await guard(s.agent.ask(body.question, body.person_id, budget=body.budget, caller=c))

    @app.get("/employee/{person_id}/briefing", tags=["employee"], summary="Personal 'What's next?' briefing")
    async def briefing(person_id: str, request: Request, c: Caller = Depends(person)) -> dict[str, Any]:
        if not svc(request).team.member(person_id):
            raise HTTPException(404, f"not found: {person_id}")
        require_self_or_manager(c, person_id)
        return await guard(svc(request).agent.briefing(person_id, caller=c))

    # ---------------------------------------------------------- manager views
    @app.get("/manager/dashboard", tags=["manager"], summary="Delivery risks, commitments, dependencies, approvals, GitHub activity, workload")
    async def dashboard(request: Request, _: Caller = Depends(manager)) -> dict[str, Any]:
        return svc(request).delivery.dashboard()

    @app.get("/manager/radar", tags=["manager"], summary="Hindsight reflect() narrative of sprint health, grounded in current state")
    async def radar(request: Request, budget: Literal["low", "mid", "high"] = "mid",
                    _: Caller = Depends(manager)) -> dict[str, Any]:
        return await guard(svc(request).agent.radar(budget))

    @app.post("/manager/ask", tags=["manager"], summary="Free-form manager question, answered with citations")
    async def manager_ask(body: QuestionRequest, request: Request, c: Caller = Depends(manager)) -> dict[str, Any]:
        return await guard(svc(request).agent.manager_ask(body.question, caller=c))

    # ---------------------------------------------------- delivery: items & risks
    @app.get("/items/{item_id}", tags=["delivery"], summary="One ticket / commitment / action with history, evidence and risks")
    async def get_item(item_id: str, request: Request, _: Caller = Depends(person)) -> dict[str, Any]:
        try:
            return svc(request).delivery.item_view(item_id)
        except KeyError as exc:
            raise HTTPException(404, f"not found: {item_id}") from exc

    @app.get("/commitments", tags=["delivery"], summary="Customer commitments (open by default)")
    async def commitments(request: Request, include_closed: bool = False, _: Caller = Depends(person)) -> list[dict[str, Any]]:
        items = svc(request).delivery.list_items(kind="commitment")
        return [i for i in items if include_closed or i["status"] not in ("done", "dropped")]

    @app.post("/commitments", tags=["delivery"], summary="Record a customer commitment")
    async def create_commitment(body: CommitmentRequest, request: Request, c: Caller = Depends(person)) -> dict[str, Any]:
        return await guard(svc(request).delivery.create_commitment(**body.model_dump(), actor=c.name))

    def _editable(request: Request, item_id: str, c: Caller) -> dict[str, Any]:
        item = svc(request).delivery.ledger.get_item(item_id)
        if item is None:
            raise HTTPException(404, f"not found: {item_id}")
        if not can_edit_item(c, item):
            raise Forbidden(f"only {item.get('owner') or 'the owner'} or a manager can change this item")
        return item

    @app.post("/items/{item_id}/dependencies", tags=["delivery"], summary="Record a dependency or a pending approval")
    async def add_dependency(item_id: str, body: DependencyRequest, request: Request,
                             c: Caller = Depends(person)) -> dict[str, Any]:
        _editable(request, item_id, c)
        return await guard(svc(request).delivery.add_dependency(item_id, description=body.description, kind=body.kind,
                                                                party=body.party, depends_on=body.depends_on, actor=c.name))

    @app.post("/dependencies/{dep_id}/resolve", tags=["delivery"], summary="Resolve a dependency / record an approval")
    async def resolve_dependency(dep_id: str, body: ResolveRequest, request: Request,
                                 c: Caller = Depends(person)) -> dict[str, Any]:
        dep = svc(request).delivery.ledger.get_dependency(dep_id)
        if dep is None:
            raise HTTPException(404, f"not found: {dep_id}")
        _editable(request, dep["item_id"], c)
        return await guard(svc(request).delivery.resolve_dependency(dep_id, note=body.note, actor=c.name))

    @app.get("/risks", tags=["delivery"], summary="Delivery risks and missing information")
    async def risks(request: Request, status: Literal["open", "resolved"] | None = "open",
                    kind: Literal["risk", "missing_info"] | None = None, _: Caller = Depends(manager)) -> dict[str, Any]:
        rows = svc(request).delivery.ledger.risks(status=status, kind=kind)
        return {"count": len(rows), "risks": rows, "rules": RULES}

    @app.post("/risks/recompute", tags=["delivery"], summary="Re-evaluate every rule now")
    async def recompute(request: Request, _: Caller = Depends(manager)) -> dict[str, Any]:
        changes = await guard(svc(request).delivery.recompute())
        return {"changes": changes}

    @app.post("/risks/{risk_id}/explain", tags=["delivery"], summary="LLM explanation of a risk from its evidence only")
    async def explain(risk_id: str, request: Request, refresh: bool = False, c: Caller = Depends(person)) -> dict[str, Any]:
        d = svc(request).delivery
        risk = d.ledger.get_risk(risk_id)
        if risk is None:
            raise HTTPException(404, f"not found: {risk_id}")
        item = d.ledger.get_item(risk["item_id"]) or {}
        if not (c.is_manager or item.get("owner_id") == c.person_id):
            raise Forbidden("only the owner or a manager can view this risk")
        return await guard(d.explain_risk(risk_id, force=refresh))

    @app.get("/events", tags=["delivery"], summary="Unified event log (meetings, standups, GitHub, approvals, risks)")
    async def events_log(request: Request, source: str | None = None, kind: str | None = None, item_id: str | None = None,
                         limit: int = Query(100, le=500), _: Caller = Depends(manager)) -> dict[str, Any]:
        rows = svc(request).delivery.ledger.events(source=source, kind_prefix=kind, item_id=item_id, limit=limit)
        return {"count": len(rows), "events": rows}

    # ------------------------------------------------------------------ GitHub
    @app.post("/integrations/github/webhook", tags=["integrations"], summary="GitHub webhook (HMAC-SHA256 verified)")
    async def github_webhook(request: Request) -> dict[str, Any]:
        s = svc(request)
        secret = s.settings.github_webhook_secret
        if not secret:
            raise HTTPException(503, "GITHUB_WEBHOOK_SECRET is not set; refusing unsigned webhooks")
        if int(request.headers.get("content-length") or 0) > MAX_WEBHOOK_BYTES:
            raise HTTPException(413, "payload too large")
        body = await request.body()
        if len(body) > MAX_WEBHOOK_BYTES:
            raise HTTPException(413, "payload too large")
        try:
            verify_signature(secret, body, request.headers.get("x-hub-signature-256"))
        except SignatureError as exc:
            raise HTTPException(401, f"invalid signature: {exc}") from exc
        event = request.headers.get("x-github-event") or ""
        if event == "ping":
            return {"status": "pong"}
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HTTPException(400, "body is not JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(400, "body must be a JSON object")
        return await guard(s.delivery.process_github(event, payload, request.headers.get("x-github-delivery")))

    # --------------------------------------------------------------- SOP vault
    @app.get("/sops", tags=["sop"])
    async def sops(request: Request, _: Caller = Depends(person)) -> list[dict[str, Any]]:
        return svc(request).registry.list("sop")

    @app.post("/sops/ask", tags=["sop"])
    async def sop_ask(body: QuestionRequest, request: Request, _: Caller = Depends(person)) -> dict[str, Any]:
        return await guard(svc(request).agent.sop_ask(body.question))

    # ------------------------------------------------------------ demo helpers
    @app.get("/demo/transcript", tags=["demo"], summary="The Acme Teams sync transcript to paste during the demo")
    async def transcript(_: Caller = Depends(person)) -> dict[str, Any]:
        return demo_transcript()

    @app.post("/demo/baseline", tags=["demo"], summary="Same question to the LLM with NO memory")
    async def baseline(body: QuestionRequest, request: Request, _: Caller = Depends(person)) -> dict[str, Any]:
        return await guard(svc(request).agent.baseline(body.question))

    @app.post("/demo/compare", tags=["demo"], summary="Side-by-side: without memory vs with Hindsight memory")
    async def compare(body: AskRequest, request: Request, c: Caller = Depends(person)) -> dict[str, Any]:
        require_self_or_manager(c, body.person_id)
        return await guard(svc(request).agent.compare(body.question, body.person_id, caller=c))

    @app.post("/demo/github", tags=["demo"], summary="Send a GitHub-shaped event through the real webhook pipeline")
    async def demo_github(body: DemoGithubRequest, request: Request, _: Caller = Depends(manager)) -> dict[str, Any]:
        s = svc(request)
        event, payload = demo_payload(body.kind, repo=s.settings.github_repo, ticket=body.ticket.upper(),
                                      pr_number=body.pr_number, title=body.title, branch=body.branch,
                                      check_name=body.check_name, run_id=int(datetime.now().timestamp() * 1000) % 10**9)
        return await guard(s.delivery.process_github(event, payload, delivery_id="demo", demo=True))

    @app.get("/demo/delivery", tags=["demo"], summary="State of the scripted delivery-risk demo")
    async def demo_delivery_status(request: Request, _: Caller = Depends(manager)) -> dict[str, Any]:
        return demo_flow.status(svc(request)) | {"steps": list(demo_flow.STEPS)}

    @app.post("/demo/delivery/{step}", tags=["demo"], summary="Run one step: commitment | ci_failure | fix | close")
    async def demo_delivery(step: Literal["commitment", "ci_failure", "fix", "close"], request: Request,
                            c: Caller = Depends(manager)) -> dict[str, Any]:
        return await guard(demo_flow.run_step(svc(request), step, c.name))

    # ------------------------------------------------------------ learning loop
    @app.post("/feedback", tags=["learning"], summary="Rate or correct an answer; corrections are retained and applied")
    async def feedback(body: FeedbackRequest, request: Request, c: Caller = Depends(person)) -> dict[str, Any]:
        s = svc(request)
        rec = s.agent.interaction(body.interaction_id)
        if rec is None:
            raise HTTPException(404, f"not found: {body.interaction_id}")
        if not c.is_manager and c.person_id not in (rec.get("asked_by"), rec.get("person_id")):
            raise Forbidden("you can only give feedback on your own answers")
        return await guard(s.agent.feedback(body.interaction_id, body.helpful, body.correction, caller=c))

    # --------------------------------------------------- meetings: record → actions
    @app.get("/meetings/options", tags=["meetings"], summary="Meeting types, scrum stages and action statuses")
    async def meeting_options() -> dict[str, Any]:
        return {"meeting_types": list(MEETING_TYPES), "scrum_stages": SCRUM_STAGES,
                "action_statuses": list(ITEM_STATUSES), "risk_rules": RULES}

    @app.post("/meetings/transcribe", tags=["meetings"], summary="Speech-to-text only (free Whisper); nothing is retained")
    async def transcribe(request: Request, audio: UploadFile = File(...), language: str | None = Form(None, max_length=10),
                         _: Caller = Depends(person)) -> dict[str, Any]:
        data = await audio.read()
        return await guard(svc(request).meetings.transcribe(data, audio.filename or "meeting.webm", language))

    @app.post("/meetings/record", tags=["meetings"],
              summary="Upload a recording: transcribe → summary + scrum action events → Hindsight retain")
    async def record_meeting(
        request: Request,
        audio: UploadFile = File(..., description="webm/ogg/mp3/m4a/wav recording"),
        title: str = Form(..., min_length=1, max_length=300),
        meeting_type: str = Form("other"),
        occurred_at: datetime | None = Form(None),
        customer: str | None = Form(None, max_length=120),
        participants: str | None = Form(None, max_length=2000, description="Comma-separated names"),
        language: str | None = Form(None, max_length=10, description="ISO code like 'en'; auto-detect when empty"),
        _: Caller = Depends(person),
    ) -> dict[str, Any]:
        data = await audio.read()
        people = [p.strip() for p in (participants or "").split(",") if p.strip()] or None
        return await guard(svc(request).meetings.process_audio(
            data, audio.filename or "meeting.webm", language=language or None, title=title,
            meeting_type=meeting_type, occurred_at=occurred_at, customer=customer or None, participants=people))

    @app.post("/meetings", tags=["meetings"], summary="Same pipeline for a pasted transcript (no audio)")
    async def create_meeting(body: MeetingRequest, request: Request, _: Caller = Depends(person)) -> dict[str, Any]:
        return await guard(svc(request).meetings.process(**body.model_dump()))

    @app.get("/meetings", tags=["meetings"], summary="Saved meetings with their summaries (no transcripts)")
    async def list_meetings(request: Request, _: Caller = Depends(person)) -> list[dict[str, Any]]:
        return [{k: v for k, v in m.items() if k not in ("transcript", "segments")} for m in svc(request).meetings.list()]

    @app.get("/meetings/{meeting_id}", tags=["meetings"])
    async def get_meeting(meeting_id: str, request: Request, _: Caller = Depends(person)) -> dict[str, Any]:
        meeting = svc(request).meetings.get(meeting_id)
        if meeting is None:
            raise HTTPException(404, f"unknown meeting {meeting_id}")
        return meeting

    @app.get("/actions", tags=["meetings"], summary="Board: tickets, commitments and meeting actions from the ledger")
    async def list_actions(request: Request, status: str | None = None, owner: str | None = None,
                           stage: str | None = None, meeting_id: str | None = None, kind: str | None = None,
                           ticket: str | None = None, _: Caller = Depends(person)) -> dict[str, Any]:
        actions = svc(request).delivery.list_items(status=status, owner=owner, stage=stage, meeting_id=meeting_id,
                                                   kind=kind, ticket=ticket)
        return {"count": len(actions), "actions": actions}

    @app.patch("/actions/{action_id}", tags=["meetings"],
               summary="Update status / owner / due / ticket; the change is kept in history and retained in Hindsight")
    async def update_action(action_id: str, body: ActionUpdate, request: Request, c: Caller = Depends(person)) -> dict[str, Any]:
        _editable(request, action_id, c)
        out = await guard(svc(request).delivery.update_item(action_id, **body.model_dump(), actor=c.name))
        return out | {"action": out["item"]}

    # ------------------------------------------------------ memory (raw access)
    @app.post("/memory/recall", tags=["memory"], summary="Raw Hindsight recall (context verification panel)")
    async def recall(body: RecallRequest, request: Request, _: Caller = Depends(manager)) -> dict[str, Any]:
        facts = await guard(svc(request).memory.recall(
            body.query, tags=body.tags, tags_match=body.tags_match, types=body.types,
            budget=body.budget, label="manual recall"))
        return {"count": len(facts), "memories": facts}

    @app.get("/memory/list", tags=["memory"])
    async def list_memories(
        request: Request,
        type: Literal["world", "experience", "observation"] | None = None,
        q: str | None = Query(None, max_length=200),
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        _: Caller = Depends(manager),
    ) -> dict[str, Any]:
        kw: dict[str, Any] = {"limit": limit, "offset": offset}
        if type:
            kw["type"] = type
        if q:
            kw["search_query"] = q
        items = await guard(svc(request).memory.list_memories(**kw))
        return {"count": len(items), "memories": items}

    # ------------------------------------------------------- live inspector
    def _redact(event: dict[str, Any], c: Caller) -> dict[str, Any]:
        """Employees see that memory operations happen, not the queries or memories of others."""
        if c.is_manager:
            return event
        req = event.get("request") or {}
        return {k: event.get(k) for k in ("id", "ts", "op", "status", "duration_ms", "parent_id")} | \
            {"request": {"label": req.get("label") if event.get("op") != "recall" else (req.get("label") or "").split(":")[0]},
             "redacted": True}

    @app.get("/inspector/events", tags=["inspector"], summary="Memory operations log (poll with ?after=<last id>)")
    async def events(request: Request, after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500),
                     op: str | None = Query(None, max_length=20), c: Caller = Depends(person)) -> dict[str, Any]:
        s = svc(request)
        ev = s.inspector.events(after=after, limit=limit, op=op)
        return {"events": [_redact(e, c) for e in ev], "last_id": ev[-1]["id"] if ev else after, "stats": s.inspector.stats()}

    @app.get("/inspector/stream", tags=["inspector"], summary="Server-sent events stream of memory operations")
    async def stream(request: Request, c: Caller = Depends(person)) -> StreamingResponse:
        s = svc(request)

        async def gen():
            yield ": connected\n\n"
            async for event in s.inspector.subscribe():
                if await request.is_disconnected():
                    break
                yield f"id: {event['id']}\nevent: {event['op']}\ndata: {json.dumps(_redact(event, c), default=str)}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.delete("/inspector/events", tags=["inspector"])
    async def clear_events(request: Request, _: Caller = Depends(manager)) -> dict[str, bool]:
        svc(request).inspector.clear()
        return {"cleared": True}

    return app


app = create_app()
