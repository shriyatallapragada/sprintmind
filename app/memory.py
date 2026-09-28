"""Memory layer: a thin, instrumented wrapper around Hindsight retain / recall / reflect.

Everything SprintMind knows lives in one Hindsight memory bank per team. Memories are
scoped with tags so the same bank can answer both personal and team-wide questions:

    source:<standup|meeting|sop|task_update|interaction|feedback|...>
    person:<slug>          owner / participant (e.g. person:priya)
    sprint:<n>             sprint number
    customer:<slug>        customer the memory relates to (e.g. customer:acme)
    kind:<task|commitment|blocker|decision|sop_step|status_update>

Two backends implement the same interface:
  * HindsightStore - the real thing (Hindsight Cloud or self-hosted)
  * LocalStore     - an in-process keyword store for offline UI work and tests
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from .inspector import Inspector

# ----------------------------------------------------------------------------
# Bank configuration: shapes what Hindsight extracts on retain and how it reasons on reflect.
# ----------------------------------------------------------------------------
BANK_MISSION = (
    "I am SprintMind, the scrum and delivery memory of a software engineering team. "
    "I track who owns which task, the status of every ticket across the sprint, blockers and "
    "what they are waiting on, commitments made to customers in meetings (with owner and due "
    "date), and the team's SOPs. I care about how task status evolves over time, and I always "
    "prefer the most recent update when statuses conflict."
)
RETAIN_MISSION = (
    "Extract concrete, attributable delivery facts: ticket IDs, task titles, owners, status "
    "changes (todo / in progress / in review / blocked / done), blockers and what unblocks "
    "them, due dates, story points, customer requests and commitments, decisions, and SOP "
    "rules/steps. Keep people's names and ticket IDs exactly as written. Ignore small talk."
)
OBSERVATIONS_MISSION = (
    "Consolidate the current state of each task and each person's workload, recurring blockers, "
    "customer expectations, and how the team actually follows its SOPs."
)
DIRECTIVES = [
    ("cite-sources", "When stating a task status, owner, or customer commitment, say where it came "
     "from (e.g. which standup or meeting and its date)."),
    ("no-invented-status", "Never invent a task status, owner, or deadline. If memory does not "
     "contain it, say it is unknown."),
    ("flag-blockers", "Always call out blocked work explicitly, including what it is waiting on "
     "and who can unblock it."),
    ("latest-wins", "If two memories disagree about a task, prefer the most recent one and "
     "mention that the status changed."),
]
MENTAL_MODELS = [
    ("sprint-status-board", "Sprint status board",
     "For every ticket in the current sprint: ID, title, owner, and latest status. Group by done, "
     "in progress, in review, blocked, not started."),
    ("customer-commitments", "Open customer commitments",
     "Which commitments have been made to customers, who owns each one, and when is it due?"),
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def fact_to_dict(r: Any) -> dict[str, Any]:
    """Normalise a Hindsight RecallResult / ReflectFact (or a dict) to plain JSON."""
    get = r.get if isinstance(r, dict) else (lambda k, d=None: getattr(r, k, d))
    scores = get("scores")
    if scores is not None and not isinstance(scores, dict):
        scores = scores.model_dump() if hasattr(scores, "model_dump") else dict(scores)

    def iso(v: Any) -> Any:
        return v.isoformat() if isinstance(v, datetime) else v

    return {
        "id": get("id"),
        "text": get("text"),
        "type": get("type") or get("fact_type"),
        "context": get("context"),
        "tags": get("tags") or [],
        "metadata": get("metadata") or {},
        "entities": [
            e if isinstance(e, str) else (e.get("name") if isinstance(e, dict) else getattr(e, "canonical_name", None) or str(e))
            for e in (get("entities") or [])
        ],
        "occurred_start": iso(get("occurred_start")),
        "mentioned_at": iso(get("mentioned_at")),
        "document_id": get("document_id"),
        "score": (scores or {}).get("final") if scores else get("score"),
    }


# ----------------------------------------------------------------------------
# Backends
# ----------------------------------------------------------------------------
class MemoryStore(Protocol):
    async def setup_bank(self, bank_id: str, name: str) -> dict[str, Any]: ...
    async def retain(self, bank_id: str, items: list[dict[str, Any]], document_id: str | None) -> dict[str, Any]: ...
    async def recall(self, bank_id: str, query: str, **kw: Any) -> list[dict[str, Any]]: ...
    async def reflect(self, bank_id: str, query: str, **kw: Any) -> dict[str, Any]: ...
    async def list_memories(self, bank_id: str, **kw: Any) -> list[dict[str, Any]]: ...
    async def delete_bank(self, bank_id: str) -> None: ...
    async def health(self) -> dict[str, Any]: ...
    async def close(self) -> None: ...


class HindsightStore:
    def __init__(self, base_url: str, api_key: str | None, timeout: float, retain_async: bool) -> None:
        from hindsight_client import Hindsight

        self.client = Hindsight(base_url=base_url, api_key=api_key, timeout=timeout)
        self.retain_async = retain_async

    async def setup_bank(self, bank_id: str, name: str) -> dict[str, Any]:
        c = self.client
        await c.acreate_bank(
            bank_id=bank_id,
            name=name,
            mission=BANK_MISSION,
            retain_mission=RETAIN_MISSION,
            observations_mission=OBSERVATIONS_MISSION,
            enable_observations=True,
            # Skeptical + literal: a status tracker should not speculate.
            disposition_skepticism=4,
            disposition_literalism=4,
            disposition_empathy=2,
        )
        report: dict[str, Any] = {"bank_id": bank_id, "directives": [], "mental_models": []}

        try:
            existing = await c.alist_directives(bank_id=bank_id)
            existing_names = {getattr(d, "name", None) for d in (getattr(existing, "items", None) or [])}
        except Exception:  # noqa: BLE001 - fall through; duplicates are rejected server-side at worst
            existing_names = set()
        for name_, content in DIRECTIVES:
            if name_ in existing_names:
                continue
            try:
                await c.acreate_directive(bank_id=bank_id, name=name_, content=content, priority=10)
                report["directives"].append(name_)
            except Exception as exc:  # noqa: BLE001 - directives are best-effort
                report["directives"].append(f"{name_}: failed ({type(exc).__name__})")

        for mm_id, mm_name, query in MENTAL_MODELS:
            try:
                await c.acreate_mental_model(
                    bank_id=bank_id, id=mm_id, name=mm_name, source_query=query,
                    tags=None, trigger={"refresh_after_consolidation": True},
                )
                report["mental_models"].append(mm_id)
            except Exception as exc:  # noqa: BLE001 - already exists / not supported
                report["mental_models"].append(f"{mm_id}: {type(exc).__name__}")
        return report

    async def retain(self, bank_id: str, items: list[dict[str, Any]], document_id: str | None) -> dict[str, Any]:
        resp = await self.client.aretain_batch(
            bank_id=bank_id, items=items, document_id=document_id, retain_async=self.retain_async
        )
        return {
            "success": getattr(resp, "success", True),
            "items_count": getattr(resp, "items_count", len(items)),
            "async": getattr(resp, "var_async", self.retain_async),
            "operation_id": getattr(resp, "operation_id", None),
        }

    async def recall(self, bank_id: str, query: str, **kw: Any) -> list[dict[str, Any]]:
        resp = await self.client.arecall(bank_id=bank_id, query=query, **kw)
        return [fact_to_dict(r) for r in resp.results or []]

    async def reflect(self, bank_id: str, query: str, **kw: Any) -> dict[str, Any]:
        resp = await self.client.areflect(bank_id=bank_id, query=query, include_facts=True, **kw)
        based_on = getattr(resp, "based_on", None)
        facts = [fact_to_dict(f) for f in (getattr(based_on, "memories", None) or [])] if based_on else []
        mms = []
        if based_on and getattr(based_on, "mental_models", None):
            mms = [getattr(m, "name", None) or getattr(m, "id", None) for m in based_on.mental_models]
        return {
            "text": resp.text,
            "structured": getattr(resp, "structured_output", None),
            "structured_error": getattr(resp, "structured_output_error", None),
            "facts": facts,
            "mental_models": mms,
        }

    async def list_memories(self, bank_id: str, **kw: Any) -> list[dict[str, Any]]:
        resp = await self.client.alist_memories(bank_id=bank_id, **kw)
        items = getattr(resp, "items", None) or []
        return [fact_to_dict(i.model_dump() if hasattr(i, "model_dump") else i) for i in items]

    async def delete_bank(self, bank_id: str) -> None:
        await self.client.adelete_bank(bank_id=bank_id)

    async def health(self) -> dict[str, Any]:
        v = await self.client.aget_version()
        return {"backend": "hindsight", "api_version": getattr(v, "api_version", None)}

    async def close(self) -> None:
        await self.client.aclose()


class LocalStore:
    """Offline stand-in: keyword-overlap recall and a canned reflect. Not for the real demo."""

    def __init__(self) -> None:
        self.banks: dict[str, list[dict[str, Any]]] = {}

    async def setup_bank(self, bank_id: str, name: str) -> dict[str, Any]:
        self.banks.setdefault(bank_id, [])
        return {"bank_id": bank_id, "directives": [d[0] for d in DIRECTIVES], "mental_models": []}

    async def retain(self, bank_id: str, items: list[dict[str, Any]], document_id: str | None) -> dict[str, Any]:
        bank = self.banks.setdefault(bank_id, [])
        if document_id:  # same document_id replaces, like Hindsight upserts
            bank[:] = [m for m in bank if m["document_id"] != document_id]
        for it in items:
            ts = it.get("timestamp")
            bank.append({
                "id": uuid.uuid4().hex,
                "text": it["content"],
                "type": "experience" if "source:interaction" in (it.get("tags") or []) else "world",
                "context": it.get("context"),
                "tags": it.get("tags") or [],
                "metadata": it.get("metadata") or {},
                "occurred_start": ts.isoformat() if isinstance(ts, datetime) else ts,
                "mentioned_at": _now().isoformat(),
                "document_id": document_id,
            })
        return {"success": True, "items_count": len(items), "async": False, "operation_id": None}

    @staticmethod
    def _tag_ok(tags: list[str], want: list[str] | None, match: str) -> bool:
        if not want:
            return True
        if not tags:
            return match in ("any", "all")
        if match.startswith("all"):
            return all(t in tags for t in want)
        return any(t in tags for t in want)

    async def recall(self, bank_id: str, query: str, **kw: Any) -> list[dict[str, Any]]:
        words = {w for w in re.findall(r"[a-z0-9-]+", query.lower()) if len(w) > 2}
        scored = []
        for m in self.banks.get(bank_id, []):
            if not self._tag_ok(m["tags"], kw.get("tags"), kw.get("tags_match", "any")):
                continue
            hay = (m["text"] + " " + " ".join(m["tags"])).lower()
            s = sum(1 for w in words if w in hay)
            if s:
                scored.append((s, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [fact_to_dict({**m, "score": s / max(len(words), 1)}) for s, m in scored[:15]]

    async def reflect(self, bank_id: str, query: str, **kw: Any) -> dict[str, Any]:
        facts = await self.recall(bank_id, query, tags=kw.get("tags"), tags_match=kw.get("tags_match", "any"))
        text = "Based on memory:\n" + "\n".join(f"- {f['text']}" for f in facts[:8]) if facts else "No relevant memories."
        return {"text": text, "structured": None, "structured_error": None, "facts": facts, "mental_models": []}

    async def list_memories(self, bank_id: str, **kw: Any) -> list[dict[str, Any]]:
        limit, offset = kw.get("limit", 100), kw.get("offset", 0)
        q = (kw.get("search_query") or "").lower()
        items = [m for m in self.banks.get(bank_id, []) if q in m["text"].lower()]
        return [fact_to_dict(m) for m in items[offset: offset + limit]]

    async def delete_bank(self, bank_id: str) -> None:
        self.banks.pop(bank_id, None)

    async def health(self) -> dict[str, Any]:
        return {"backend": "local", "banks": {k: len(v) for k, v in self.banks.items()}}

    async def close(self) -> None:
        return None


# ----------------------------------------------------------------------------
# Instrumented service used by the rest of the app
# ----------------------------------------------------------------------------
class MemoryService:
    def __init__(self, store: MemoryStore, inspector: Inspector, bank_id: str, bank_name: str) -> None:
        self.store = store
        self.inspector = inspector
        self.bank_id = bank_id
        self.bank_name = bank_name

    async def setup(self) -> dict[str, Any]:
        async with self.inspector.track("setup", bank_id=self.bank_id) as out:
            report = await self.store.setup_bank(self.bank_id, self.bank_name)
            out.update(report)
        return report

    async def retain(
        self,
        items: list[dict[str, Any]],
        document_id: str | None = None,
        label: str | None = None,
    ) -> dict[str, Any]:
        """items: [{content, context, timestamp, tags, metadata}]"""
        preview = [{"content": i["content"][:160], "tags": i.get("tags", [])} for i in items[:25]]
        async with self.inspector.track(
            "retain", bank_id=self.bank_id, document_id=document_id, label=label,
            items=len(items), preview=preview,
        ) as out:
            res = await self.store.retain(self.bank_id, items, document_id)
            out.update(res)
        return res

    async def recall(
        self,
        query: str,
        *,
        tags: list[str] | None = None,
        tags_match: str = "any",
        types: list[str] | None = None,
        budget: str = "mid",
        max_tokens: int = 3000,
        label: str | None = None,
    ) -> list[dict[str, Any]]:
        async with self.inspector.track(
            "recall", bank_id=self.bank_id, query=query, tags=tags, tags_match=tags_match,
            types=types, budget=budget, label=label,
        ) as out:
            facts = await self.store.recall(
                self.bank_id, query, tags=tags, tags_match=tags_match, types=types,
                budget=budget, max_tokens=max_tokens, query_timestamp=_now().isoformat(),
            )
            out["count"] = len(facts)
            out["facts"] = [{"id": f["id"], "text": f["text"], "type": f["type"], "score": f["score"]} for f in facts]
        return facts

    async def reflect(
        self,
        query: str,
        *,
        context: str | None = None,
        tags: list[str] | None = None,
        tags_match: str = "any",
        response_schema: dict[str, Any] | None = None,
        budget: str = "mid",
        label: str | None = None,
    ) -> dict[str, Any]:
        async with self.inspector.track(
            "reflect", bank_id=self.bank_id, query=query, tags=tags, budget=budget,
            structured=response_schema is not None, label=label,
        ) as out:
            kw: dict[str, Any] = {"budget": budget, "context": context}
            if tags:
                kw.update(tags=tags, tags_match=tags_match)
            if response_schema:
                kw["response_schema"] = response_schema
            res = await self.store.reflect(self.bank_id, query, **kw)
            out.update({
                "text_preview": (res["text"] or "")[:400],
                "based_on_facts": len(res["facts"]),
                "mental_models": res["mental_models"],
                "facts": [{"id": f["id"], "text": f["text"], "type": f["type"]} for f in res["facts"][:20]],
            })
        return res

    async def list_memories(self, **kw: Any) -> list[dict[str, Any]]:
        return await self.store.list_memories(self.bank_id, **kw)

    async def reset(self) -> None:
        async with self.inspector.track("setup", bank_id=self.bank_id, action="delete_bank"):
            await self.store.delete_bank(self.bank_id)

    async def health(self) -> dict[str, Any]:
        return await self.store.health()
