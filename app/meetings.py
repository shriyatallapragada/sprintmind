"""Recorded meetings -> transcript -> summary + scrum action events -> Hindsight.

    audio ──► Whisper (free: Groq or local) ──► timestamped transcript
                                                   │
                           LLM: summary, decisions, action events (owner / due / scrum stage)
                                                   │
          Ingestor: retain(summary + transcript) and retain(one tagged fact per action event)
                                                   │
          Every action becomes a ledger item (see delivery.py), next to tickets and customer
          commitments. MeetingStore (data/meetings/*.json) keeps the transcript and summary; the
          action board reads the ledger, so a status change, a PR merge or an approval moves the same
          record and is retained in Hindsight as a new, timestamped memory.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .delivery import DeliveryService
from .ingest import Ingestor
from .ledger import ITEM_STATUSES
from .llm import LLM
from .memory import slug
from .team import Team
from .transcribe import Transcriber, TranscriptionError

# What kind of meeting was recorded -> source_type used for memory tags.
MEETING_TYPES = {
    "standup": "standup",
    "sprint_planning": "meeting",
    "customer_sync": "meeting",
    "sprint_review": "meeting",
    "retro": "retro",
    "other": "meeting",
}
# Where each action event lands in the scrum cycle.
SCRUM_STAGES = {
    "sprint_backlog": "work to do in the current sprint",
    "product_backlog": "new scope for a future sprint",
    "impediment": "a blocker to raise and clear at standup",
    "sprint_review": "something to demo or hand over at sprint review / to the customer",
    "retro": "a process improvement for the retrospective",
    "follow_up": "a small admin follow-up (send an email, confirm scope, share a document)",
}
ACTION_STATUSES = ITEM_STATUSES
ACTION_KINDS = {"task", "commitment", "blocker", "risk", "follow_up"}

MEETING_SYSTEM = """You turn a scrum team's meeting transcript into a summary and action events.
The transcript comes from speech-to-text: it may have NO speaker labels and some misheard words.
Return ONLY a JSON object:
{{
  "summary": "3-5 sentences: purpose, main outcomes, what changed",
  "decisions": ["each decision that was agreed, one sentence each"],
  "open_questions": ["anything left unresolved"],
  "items": [
    {{
      "kind": "task | commitment | blocker | risk | follow_up | decision",
      "scrum_stage": "{stages} | null for decisions",
      "ticket": "ticket id like NW-231, or null",
      "title": "short imperative action title",
      "owner": "full name exactly as in the roster, or null",
      "status": "todo | in_progress | blocked | done",
      "due": "YYYY-MM-DD or null",
      "priority": "P0 | P1 | P2 | P3 | null",
      "customer": "customer name or null",
      "blocked_on": "what it waits on, or null",
      "detail": "one precise sentence with the specifics (numbers, limits, versions)"
    }}
  ]
}}
Scrum stages:
{stage_help}
Rules:
- One item per distinct action. Keep numbers, limits and ticket ids exactly as spoken.
- 'commitment' = promised to a customer. 'blocker' = work that cannot proceed (status blocked, stage impediment).
- Owner only when the words make it clear (named person volunteers, is asked, or is named in a recap). Otherwise null.
- Resolve relative dates ("Friday", "next sync") with the calendar provided. Do not invent dates; a sprint's end is
  not an item's due date unless the meeting says so.
- status is 'todo' unless the meeting says work has already started, is blocked, or is finished.
- Fix obvious speech-to-text errors in names using the roster, but never invent facts."""


class AudioTooLarge(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _calendar(day: datetime, days: int = 14) -> str:
    """LLMs are unreliable at weekday arithmetic, so hand them the next two weeks."""
    return ", ".join(f"{d:%a} {d:%Y-%m-%d}" for d in (day + timedelta(days=i) for i in range(days)))


def _chunks(text: str, max_chars: int) -> list[str]:
    """Split on line boundaries so a long meeting fits the model's per-request token limits."""
    out, cur = [], ""
    for line in text.splitlines(keepends=True):
        if cur and len(cur) + len(line) > max_chars:
            out.append(cur)
            cur = ""
        cur += line
    return out + [cur] if cur.strip() else out


class MeetingStore:
    """One JSON file per meeting. Small, inspectable, and survives restarts."""

    def __init__(self, directory: str) -> None:
        self.dir = Path(directory)
        self._lock = threading.Lock()

    def _path(self, meeting_id: str) -> Path:
        return self.dir / f"{slug(meeting_id)}.json"

    def save(self, meeting: dict[str, Any]) -> None:
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._path(meeting["id"]).write_text(json.dumps(meeting, indent=2, default=str))

    def get(self, meeting_id: str) -> dict[str, Any] | None:
        p = self._path(meeting_id)
        return json.loads(p.read_text()) if p.exists() else None

    def list(self) -> list[dict[str, Any]]:
        if not self.dir.exists():
            return []
        meetings = []
        for p in self.dir.glob("*.json"):
            try:
                meetings.append(json.loads(p.read_text()))
            except json.JSONDecodeError:
                continue
        return sorted(meetings, key=lambda m: m.get("occurred_at") or "", reverse=True)

    def save_audio(self, meeting_id: str, audio: bytes, filename: str) -> str:
        audio_dir = self.dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        path = audio_dir / f"{slug(meeting_id)}{Path(filename).suffix or '.webm'}"
        path.write_bytes(audio)
        return str(path)

    def clear(self) -> None:
        with self._lock:
            if not self.dir.exists():
                return
            for p in self.dir.glob("*.json"):
                p.unlink()
            for p in (self.dir / "audio").glob("*") if (self.dir / "audio").exists() else []:
                p.unlink()


class MeetingService:
    def __init__(self, ingestor: Ingestor, llm: LLM | None, transcriber: Transcriber | None, team: Team,
                 store: MeetingStore, *, max_audio_mb: float, keep_audio: bool, chunk_chars: int = 10000,
                 delivery: DeliveryService) -> None:
        self.ingestor = ingestor
        self.delivery = delivery
        self.memory = ingestor.memory
        self.llm = llm
        self.transcriber = transcriber
        self.team = team
        self.store = store
        self.max_audio_mb = max_audio_mb
        self.keep_audio = keep_audio
        self.chunk_chars = chunk_chars

    # ------------------------------------------------------------ transcription
    def _vocabulary_prompt(self) -> str:
        """Whisper spells names and ticket ids far better when primed with them."""
        people = ", ".join(m["name"] for m in self.team.members)
        customers = ", ".join(c["name"] for c in self.team.customers)
        return f"{self.team.name} scrum meeting. People: {people}. Customers: {customers}. Tickets like NW-231."

    async def transcribe(self, audio: bytes, filename: str, language: str | None = None) -> dict[str, Any]:
        if self.transcriber is None:
            raise TranscriptionError("no transcriber configured (set TRANSCRIBER=groq or local)")
        if not audio:
            raise ValueError("audio file is empty")
        size_mb = len(audio) / 1_048_576
        if self.transcriber.engine == "groq-whisper" and size_mb > self.max_audio_mb:
            raise AudioTooLarge(f"audio is {size_mb:.1f} MB; Groq's free tier accepts up to {self.max_audio_mb:.0f} MB. "
                                "Record at a lower bitrate, split the file, or use TRANSCRIBER=local.")
        return await self.transcriber.transcribe(audio, filename, language=language, prompt=self._vocabulary_prompt())

    # --------------------------------------------------------------- summarising
    async def summarise(self, transcript: str, title: str, meeting_type: str, occurred_at: datetime) -> dict[str, Any]:
        if self.llm is None:
            return {"summary": title, "decisions": [], "open_questions": [], "items": [], "error": "no LLM configured"}
        system = MEETING_SYSTEM.format(stages=" | ".join(SCRUM_STAGES),
                                       stage_help="\n".join(f"- {k}: {v}" for k, v in SCRUM_STAGES.items()))
        parts = _chunks(transcript, self.chunk_chars)
        merged: dict[str, Any] = {"summary": "", "decisions": [], "open_questions": [], "items": []}
        summaries = []
        try:
            for i, part in enumerate(parts, 1):
                label = "meeting:summarise" + (f" {i}/{len(parts)}" if len(parts) > 1 else "")
                user = (f"Team roster:\n{self.team.roster_text()}\n\nMeeting: {title} ({meeting_type.replace('_', ' ')})\n"
                        f"Meeting date: {occurred_at.date().isoformat()} ({occurred_at.strftime('%A')})\n"
                        f"Calendar: {_calendar(occurred_at)}\n"
                        + (f"This is part {i} of {len(parts)} of the transcript.\n" if len(parts) > 1 else "")
                        + f"\n---\n{part}\n---")
                data = await self.llm.complete_json(system, user, label=label)
                summaries.append(data.get("summary") or "")
                merged["decisions"] += [d for d in data.get("decisions") or [] if isinstance(d, str)]
                merged["open_questions"] += [q for q in data.get("open_questions") or [] if isinstance(q, str)]
                merged["items"] += [it for it in data.get("items") or []
                                    if isinstance(it, dict) and (it.get("title") or it.get("detail"))]
        except Exception as exc:  # noqa: BLE001 - the transcript is still retained
            return {"summary": title, "decisions": [], "open_questions": [], "items": [], "error": str(exc)}

        merged["summary"] = summaries[0] if len(summaries) == 1 else await self._condense(summaries, title)
        return merged

    async def _condense(self, summaries: list[str], title: str) -> str:
        joined = "\n".join(f"Part {i}: {s}" for i, s in enumerate(summaries, 1))
        try:
            return await self.llm.complete(  # type: ignore[union-attr]
                "Merge these partial meeting summaries into one 3-5 sentence summary. Use only their content.",
                f"Meeting: {title}\n\n{joined}", label="meeting:condense")
        except Exception:  # noqa: BLE001
            return " ".join(summaries)

    # ----------------------------------------------------------------- pipeline
    @staticmethod
    def _normalise_actions(items: list[dict[str, Any]]) -> None:
        """Fill scrum defaults in place; the ledger turns each action into an item with a stable id."""
        for it in items:
            if it.get("kind") == "decision":
                it["scrum_stage"] = None
                continue
            if it.get("kind") not in ACTION_KINDS:
                it["kind"] = "task"
            if it.get("scrum_stage") not in SCRUM_STAGES:
                it["scrum_stage"] = "impediment" if it["kind"] == "blocker" else "sprint_backlog"
            if it.get("status") not in ACTION_STATUSES:
                it["status"] = "blocked" if it["kind"] == "blocker" else "todo"

    async def process(
        self,
        *,
        transcript: str,
        title: str,
        meeting_type: str = "other",
        occurred_at: datetime | None = None,
        customer: str | None = None,
        participants: list[str] | None = None,
        transcription: dict[str, Any] | None = None,
        audio: bytes | None = None,
        audio_filename: str | None = None,
        extraction: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Summarise a transcript, turn it into action events, retain everything, save the meeting.

        `extraction` ({summary, decisions, open_questions, items}) skips the LLM summary, e.g. for a
        structured import or the scripted demo; everything downstream is the same pipeline.
        """
        if meeting_type not in MEETING_TYPES:
            raise ValueError(f"meeting_type must be one of {sorted(MEETING_TYPES)}")
        if not transcript.strip():
            raise ValueError("transcript is empty (nothing was said, or the recording had no audio)")
        occurred_at = occurred_at or _now()
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        source_type = MEETING_TYPES[meeting_type]
        meeting_id = f"{source_type}-{occurred_at.strftime('%Y-%m-%d-%H%M')}-{slug(title)[:50]}"

        result = (await self.summarise(transcript, title, meeting_type, occurred_at) if extraction is None else
                  {"summary": extraction.get("summary") or title, "decisions": extraction.get("decisions") or [],
                   "open_questions": extraction.get("open_questions") or [], "items": list(extraction.get("items") or [])})
        self._normalise_actions(result["items"])
        actions = [it for it in result["items"] if it.get("kind") != "decision"]

        # Summary first, so Hindsight (and anyone reading the raw memory) sees the outcome before the noise.
        content = "\n".join([
            f"SUMMARY: {result['summary']}",
            *(["DECISIONS:", *(f"- {d}" for d in result["decisions"])] if result["decisions"] else []),
            *(["ACTION EVENTS:", *(f"- [{a['scrum_stage']}] {a.get('title')} (owner: {a.get('owner') or 'unassigned'}"
                                   f"{', due ' + a['due'] if a.get('due') else ''})" for a in actions)] if actions else []),
            "", "TRANSCRIPT:", transcript,
        ])
        ingest = await self.ingestor.ingest(
            source_type=source_type, title=title, content=content, occurred_at=occurred_at, customer=customer,
            participants=participants, document_id=meeting_id, meeting_id=meeting_id,
            extraction={"summary": result["summary"], "items": result["items"], "error": result.get("error")},
        )
        action_ids = list(dict.fromkeys(i for i, it in zip(ingest["item_ids"], result["items"])
                                        if i and it.get("kind") != "decision"))

        meeting = {
            "id": meeting_id,
            "title": title,
            "meeting_type": meeting_type,
            "source_type": source_type,
            "occurred_at": occurred_at.isoformat(),
            "customer": ingest["customer"],
            "people": ingest["people"],
            "summary": result["summary"],
            "decisions": result["decisions"],
            "open_questions": result["open_questions"],
            "action_ids": action_ids,
            "transcript": transcript,
            "transcription": {k: v for k, v in (transcription or {}).items() if k not in ("segments", "text")} or None,
            "segments": (transcription or {}).get("segments") or [],
            "facts_retained": ingest["facts_retained"],
            "extraction_error": result.get("error"),
            "audio_file": self.store.save_audio(meeting_id, audio, audio_filename or "meeting.webm")
            if audio and self.keep_audio else None,
            "created_at": _now().isoformat(),
        }
        self.store.save(meeting)
        return self.hydrate(meeting)

    async def process_audio(self, audio: bytes, filename: str, *, language: str | None = None,
                            **meta: Any) -> dict[str, Any]:
        transcription = await self.transcribe(audio, filename, language)
        return await self.process(transcript=transcription["text"], transcription=transcription,
                                  audio=audio, audio_filename=filename, **meta)

    # ----------------------------------------------------------- action board
    def hydrate(self, meeting: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
        """Attach the current state of the meeting's actions from the ledger."""
        if "action_ids" not in meeting and meeting.get("actions"):
            self._import_legacy(meeting)
        actions = []
        for aid in meeting.get("action_ids", []):
            try:
                actions.append(self.delivery.item_view(aid, full=full))
            except KeyError:
                continue
        return {**meeting, "actions": actions}

    def _import_legacy(self, meeting: dict[str, Any]) -> None:
        """Meetings saved before the ledger kept their actions inline; move them into the ledger once."""
        ledger = self.delivery.ledger
        ev, _ = ledger.add_event(dedup_key=f"legacy:{meeting['id']}", source=meeting.get("source_type", "meeting"),
                                 kind="document.ingested", occurred_at=meeting["occurred_at"], title=meeting["title"],
                                 source_ref=meeting["id"], payload={"document_title": meeting["title"], "legacy": True})
        ids = []
        for a in meeting.get("actions", []):
            if not ledger.get_item(a["id"]):
                ledger.create_item(item_id=a["id"], kind=a.get("kind") or "task", title=a.get("title") or "action",
                                   event=ev, status=a.get("status") or "todo", meeting_id=meeting["id"],
                                   **{k: a.get(k) for k in ("detail", "ticket", "customer", "owner_id", "owner", "due",
                                                            "priority", "scrum_stage", "blocked_on")})
            ids.append(a["id"])
        meeting["action_ids"] = ids
        meeting.pop("actions", None)
        self.store.save(meeting)

    def get(self, meeting_id: str) -> dict[str, Any] | None:
        m = self.store.get(meeting_id)
        return self.hydrate(m) if m else None

    def list(self) -> list[dict[str, Any]]:
        return [self.hydrate(m) for m in self.store.list()]
