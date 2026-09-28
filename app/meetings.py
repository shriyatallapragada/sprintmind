"""Recorded meetings -> transcript -> summary + scrum action events -> Hindsight.

    audio ──► Whisper (free: Groq or local) ──► timestamped transcript
                                                   │
                           LLM: summary, decisions, action events (owner / due / scrum stage)
                                                   │
          Ingestor: retain(summary + transcript) and retain(one tagged fact per action event)
                                                   │
          MeetingStore (data/meetings/*.json): the action board. Every status change on an
          action is retained again, so Hindsight sees the action move todo → in progress → done
          and the manager radar / employee briefing pick it up without anyone re-typing it.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .ingest import Ingestor
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
ACTION_STATUSES = ("todo", "in_progress", "blocked", "done", "dropped")
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
- Resolve relative dates ("Friday", "next sync") with the calendar provided. Do not invent dates.
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
                 store: MeetingStore, *, max_audio_mb: float, keep_audio: bool, chunk_chars: int = 10000) -> None:
        self.ingestor = ingestor
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
    def _normalise_actions(self, items: list[dict[str, Any]], meeting_id: str) -> list[dict[str, Any]]:
        prefix = hashlib.sha1(meeting_id.encode()).hexdigest()[:6]
        actions, n = [], 0
        for it in items:
            if it.get("kind") == "decision":
                it["scrum_stage"] = None
                continue
            n += 1
            if it.get("kind") not in ACTION_KINDS:
                it["kind"] = "task"
            if it.get("scrum_stage") not in SCRUM_STAGES:
                it["scrum_stage"] = "impediment" if it["kind"] == "blocker" else "sprint_backlog"
            if it.get("status") not in ACTION_STATUSES:
                it["status"] = "blocked" if it["kind"] == "blocker" else "todo"
            it["action_id"] = f"{prefix}-{n:02d}"
            owner_id = self.team.resolve_person(it.get("owner"))
            actions.append({
                "id": it["action_id"],
                "meeting_id": meeting_id,
                **{k: it.get(k) for k in ("kind", "scrum_stage", "ticket", "title", "owner", "status", "due",
                                          "priority", "customer", "blocked_on", "detail")},
                "owner_id": owner_id,
                "history": [{"status": it["status"], "at": _now().isoformat(), "by": "meeting", "note": None}],
            })
        return actions

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
    ) -> dict[str, Any]:
        """Summarise a transcript, turn it into action events, retain everything, save the meeting."""
        if meeting_type not in MEETING_TYPES:
            raise ValueError(f"meeting_type must be one of {sorted(MEETING_TYPES)}")
        if not transcript.strip():
            raise ValueError("transcript is empty (nothing was said, or the recording had no audio)")
        occurred_at = occurred_at or _now()
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        source_type = MEETING_TYPES[meeting_type]
        meeting_id = f"{source_type}-{occurred_at.strftime('%Y-%m-%d-%H%M')}-{slug(title)[:50]}"

        result = await self.summarise(transcript, title, meeting_type, occurred_at)
        actions = self._normalise_actions(result["items"], meeting_id)

        # Summary first, so Hindsight (and anyone reading the raw memory) sees the outcome before the noise.
        content = "\n".join([
            f"SUMMARY: {result['summary']}",
            *(["DECISIONS:", *(f"- {d}" for d in result["decisions"])] if result["decisions"] else []),
            *(["ACTION EVENTS:", *(f"- [{a['scrum_stage']}] {a['title']} (owner: {a['owner'] or 'unassigned'}"
                                   f"{', due ' + a['due'] if a['due'] else ''})" for a in actions)] if actions else []),
            "", "TRANSCRIPT:", transcript,
        ])
        ingest = await self.ingestor.ingest(
            source_type=source_type, title=title, content=content, occurred_at=occurred_at, customer=customer,
            participants=participants, document_id=meeting_id,
            extraction={"summary": result["summary"], "items": result["items"], "error": result.get("error")},
        )

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
            "actions": actions,
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
        return meeting

    async def process_audio(self, audio: bytes, filename: str, *, language: str | None = None,
                            **meta: Any) -> dict[str, Any]:
        transcription = await self.transcribe(audio, filename, language)
        return await self.process(transcript=transcription["text"], transcription=transcription,
                                  audio=audio, audio_filename=filename, **meta)

    # ----------------------------------------------------------- action board
    def list_actions(self, *, status: str | None = None, owner: str | None = None, stage: str | None = None,
                     meeting_id: str | None = None) -> list[dict[str, Any]]:
        out = []
        for m in self.store.list():
            if meeting_id and m["id"] != meeting_id:
                continue
            for a in m.get("actions", []):
                if status and a["status"] != status:
                    continue
                if owner and a.get("owner_id") != owner:
                    continue
                if stage and a["scrum_stage"] != stage:
                    continue
                out.append({**a, "meeting_title": m["title"], "meeting_at": m["occurred_at"]})
        return out

    def _find_action(self, action_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        for m in self.store.list():
            for a in m.get("actions", []):
                if a["id"] == action_id:
                    return m, a
        raise KeyError(action_id)

    async def update_action(self, action_id: str, *, status: str | None = None, owner: str | None = None,
                            due: str | None = None, note: str | None = None,
                            person_id: str | None = None) -> dict[str, Any]:
        """Move an action through the sprint and retain the change so memory tracks its evolution."""
        if status and status not in ACTION_STATUSES:
            raise ValueError(f"status must be one of {ACTION_STATUSES}")
        meeting, action = self._find_action(action_id)
        if owner:
            owner_id = self.team.resolve_person(owner)
            if not owner_id:
                raise ValueError(f"unknown owner {owner!r}")
            action["owner_id"], action["owner"] = owner_id, self.team.member(owner_id)["name"]
        if due:
            action["due"] = due
        if status:
            action["status"] = status
        by = (self.team.member(person_id) or {}).get("name") if person_id else None
        action["history"].append({"status": action["status"], "at": _now().isoformat(), "by": by or "dashboard",
                                  "note": note})
        self.store.save(meeting)

        when = _now()
        item = {**action, "kind": "status_update", "blocked_on": note if action["status"] == "blocked" else None}
        text = self.ingestor.fact_sentence(
            item, f"action {action_id} from {meeting['title']}, updated {when.strftime('%a %d %b %Y')}")
        if note:
            text += f" Update note{' from ' + by if by else ''}: {note}"
        tags = {"source:task_update", f"sprint:{self.ingestor.sprint}", "kind:status_update",
                f"scrum:{action['scrum_stage']}", f"action:{action_id}"}
        if action.get("owner_id"):
            tags.add(f"person:{action['owner_id']}")
        if action.get("ticket"):
            tags.add(f"ticket:{action['ticket'].upper()}")
        if meeting.get("customer"):
            tags.add(f"customer:{meeting['customer']}")
        if action["status"] == "blocked":
            tags.add("status:blocked")
        retain = await self.memory.retain(
            [{"content": text, "context": "task_update:action_status", "timestamp": when, "tags": sorted(tags),
              "metadata": {"action_id": action_id, "meeting_id": meeting["id"], "status": action["status"]}}],
            label=f"action {action_id} → {action['status']}",
        )
        return {"action": {**action, "meeting_title": meeting["title"]}, "memory": text, "retain": retain}
