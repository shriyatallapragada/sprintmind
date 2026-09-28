"""Ingestion pipeline: standups, meeting transcripts, SOPs and ticket updates -> Hindsight.

For every source document we retain two things:
  1. the raw document (so Hindsight's own extraction keeps the full context and wording), and
  2. atomic, attributable "delivery facts" pulled out by the LLM (task/owner/status/due,
     customer commitments, blockers, decisions, SOP steps), each tagged with the owner,
     sprint, customer and ticket so recall can be scoped per person or per customer.

If the LLM extraction fails, the raw document is still retained - ingestion never loses data.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .llm import LLM
from .memory import MemoryService, slug
from .team import Team

SOURCE_TYPES = {"standup", "meeting", "sop", "task_update", "retro", "incident", "note"}

EXTRACT_SYSTEM = """You extract delivery facts for a scrum team's memory system.
Return ONLY a JSON object with this shape:
{
  "summary": "one-sentence summary of the document",
  "items": [
    {
      "kind": "task | status_update | blocker | commitment | decision | sop_step | risk",
      "ticket": "ticket id like NW-231, or null",
      "title": "short task / commitment / rule title",
      "owner": "person's full name exactly as in the roster, or null",
      "status": "todo | in_progress | in_review | blocked | done | null",
      "due": "YYYY-MM-DD or null",
      "priority": "P0 | P1 | P2 | P3 | null",
      "customer": "customer name or null",
      "blocked_on": "what it is waiting on, or null",
      "detail": "one precise sentence with the specifics (numbers, limits, versions, steps)"
    }
  ]
}
Rules:
- One item per distinct fact. Keep numbers, limits, dates and ticket ids exactly as written.
- 'commitment' = something promised to a customer. 'blocker' = work that cannot proceed.
- Resolve relative dates ("Friday", "tomorrow") against the document date.
- For SOP documents emit one 'sop_step' per rule/step, owner null unless a role is named.
- Do not invent owners, dates or statuses that are not in the text."""


def _fmt_date(dt: datetime) -> str:
    return dt.strftime("%a %d %b %Y")


class Registry:
    """Tiny JSON file listing ingested source documents (for the dashboard's Sources / SOP Vault)."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text())
        except json.JSONDecodeError:
            return []

    def upsert(self, entry: dict[str, Any]) -> None:
        with self._lock:
            docs = [d for d in self._read() if d["document_id"] != entry["document_id"]]
            docs.append(entry)
            docs.sort(key=lambda d: d.get("occurred_at") or "")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(docs, indent=2))

    def list(self, source_type: str | None = None) -> list[dict[str, Any]]:
        docs = self._read()
        return [d for d in docs if source_type is None or d["source_type"] == source_type]

    def get(self, document_id: str) -> dict[str, Any] | None:
        return next((d for d in self._read() if d["document_id"] == document_id), None)

    def clear(self) -> None:
        with self._lock:
            if self.path.exists():
                self.path.unlink()


class Ingestor:
    def __init__(self, memory: MemoryService, llm: LLM | None, team: Team, registry: Registry, sprint: str) -> None:
        self.memory = memory
        self.llm = llm
        self.team = team
        self.registry = registry
        self.sprint = sprint

    async def extract(self, source_type: str, title: str, content: str, occurred_at: datetime) -> dict[str, Any]:
        if self.llm is None:
            return {"summary": title, "items": [], "error": "no LLM configured"}
        user = (
            f"Team roster:\n{self.team.roster_text()}\n\n"
            f"Document type: {source_type}\nTitle: {title}\nDocument date: {occurred_at.date().isoformat()} "
            f"({occurred_at.strftime('%A')})\n\n---\n{content}\n---"
        )
        try:
            data = await self.llm.complete_json(EXTRACT_SYSTEM, user, label=f"extract:{source_type}")
        except Exception as exc:  # noqa: BLE001 - raw document is still retained
            return {"summary": title, "items": [], "error": str(exc)}
        items = [i for i in data.get("items", []) if isinstance(i, dict) and (i.get("title") or i.get("detail"))]
        return {"summary": data.get("summary") or title, "items": items}

    def fact_sentence(self, it: dict[str, Any], source_label: str) -> str:
        kind = (it.get("kind") or "note").replace("_", " ")
        parts = [f"[{kind}]"]
        if it.get("ticket"):
            parts.append(it["ticket"])
        parts.append(f"\"{it.get('title') or ''}\"".strip())
        if it.get("owner"):
            parts.append(f"- owner: {it['owner']}")
        if it.get("status"):
            parts.append(f"- status: {it['status'].replace('_', ' ')}")
        if it.get("priority"):
            parts.append(f"- priority: {it['priority']}")
        if it.get("due"):
            parts.append(f"- due: {it['due']}")
        if it.get("customer"):
            parts.append(f"- customer: {it['customer']}")
        if it.get("blocked_on"):
            parts.append(f"- blocked on: {it['blocked_on']}")
        text = " ".join(parts) + "."
        if it.get("detail"):
            text += f" {it['detail'].rstrip('.')}."
        return f"{text} (source: {source_label})"

    async def ingest(
        self,
        *,
        source_type: str,
        title: str,
        content: str,
        occurred_at: datetime | None = None,
        customer: str | None = None,
        participants: list[str] | None = None,
        sprint: str | None = None,
        document_id: str | None = None,
        extraction: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """`extraction` ({summary, items}) skips the LLM pass when the caller already extracted facts."""
        if source_type not in SOURCE_TYPES:
            raise ValueError(f"source_type must be one of {sorted(SOURCE_TYPES)}")
        occurred_at = occurred_at or datetime.now(timezone.utc)
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        sprint = sprint or self.sprint
        document_id = document_id or f"{source_type}-{occurred_at.date().isoformat()}-{slug(title)[:60]}"
        source_label = f"{title}, {_fmt_date(occurred_at)}"

        if extraction is None:
            extraction = await self.extract(source_type, title, content, occurred_at)
        items = extraction["items"]

        base_tags = [f"source:{source_type}", f"sprint:{sprint}"]
        cust_id = self.team.resolve_customer(customer) if customer else None
        if cust_id:
            base_tags.append(f"customer:{cust_id}")
        people = {p for p in (self.team.resolve_person(n) for n in (participants or [])) if p}
        people |= {p for p in (self.team.resolve_person(i.get("owner")) for i in items) if p}

        # 1) the raw document
        raw = {
            "content": f"{source_type.upper()}: {title} ({_fmt_date(occurred_at)})\n\n{content}",
            "context": source_type,
            "timestamp": occurred_at,
            "tags": base_tags + [f"person:{p}" for p in sorted(people)],
            "metadata": {"title": title, "source_type": source_type, "sprint": sprint},
        }
        raw_res = await self.memory.retain([raw], document_id=document_id, label=f"raw {source_type}: {title}")

        # 2) atomic, tagged delivery facts
        fact_items = []
        for it in items:
            tags = list(base_tags)
            if it.get("kind"):
                tags.append(f"kind:{it['kind']}")
            owner = self.team.resolve_person(it.get("owner"))
            if owner:
                tags.append(f"person:{owner}")
            if it.get("ticket"):
                tags.append(f"ticket:{it['ticket'].upper()}")
            if it.get("customer") and not cust_id:
                tags.append(f"customer:{self.team.resolve_customer(it['customer'])}")
            if it.get("status") == "blocked" or it.get("kind") == "blocker":
                tags.append("status:blocked")
            if it.get("scrum_stage"):
                tags.append(f"scrum:{it['scrum_stage']}")
            entities = []
            if it.get("owner"):
                entities.append({"text": it["owner"], "type": "person"})
            if it.get("ticket"):
                entities.append({"text": it["ticket"].upper(), "type": "ticket"})
            if it.get("customer"):
                entities.append({"text": it["customer"], "type": "organization"})
            fact_items.append({
                "content": self.fact_sentence(it, source_label),
                "entities": entities or None,
                "context": f"{source_type}:{it.get('kind', 'fact')}",
                "timestamp": occurred_at,
                "tags": sorted(set(tags)),
                "metadata": {k: str(v) for k, v in {
                    "document_id": document_id, "kind": it.get("kind"), "ticket": it.get("ticket"),
                    "owner": owner, "status": it.get("status"), "due": it.get("due"),
                    "action_id": it.get("action_id"), "scrum_stage": it.get("scrum_stage"),
                }.items() if v},
            })
        facts_res = None
        if fact_items:
            facts_res = await self.memory.retain(
                fact_items, document_id=f"{document_id}::facts", label=f"{len(fact_items)} facts from {title}"
            )

        entry = {
            "document_id": document_id,
            "title": title,
            "source_type": source_type,
            "occurred_at": occurred_at.isoformat(),
            "sprint": sprint,
            "customer": cust_id,
            "people": sorted(people),
            "summary": extraction["summary"],
            "facts_retained": len(fact_items),
            "extraction_error": extraction.get("error"),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        }
        if source_type == "sop":
            entry["content"] = content
        self.registry.upsert(entry)

        return {
            **entry,
            "items": items,
            "retain": {"raw": raw_res, "facts": facts_res},
        }
