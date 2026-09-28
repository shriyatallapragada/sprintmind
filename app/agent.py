"""SprintMind agent: the query side of the system.

  Employee portal  -> recall() (personal + team + corrections) from Hindsight, plus the ledger's latest
                      verified state, then Groq writes an answer that cites both
  Manager radar    -> reflect() over the whole bank, grounded with the current delivery state
  SOP vault        -> recall() restricted to source:sop
  Baseline         -> the same question to Groq with NO memory (demo "before")
  Learning loop    -> every Q&A and every correction is retained back into Hindsight; corrections are
                      also applied to the ledger as authoritative statements

Temporal rule: Hindsight memories are historical statements, true as of their date. The ledger's
CURRENT STATE entries (GitHub events, approvals, dashboard edits, the newest standups) are authoritative
for what is true now. Answers must cite evidence, and citation numbers are checked after generation.
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from .auth import ANONYMOUS, Caller, visible_to
from .llm import LLM
from .memory import MemoryService
from .team import Team

ANSWER_SYSTEM = """You are SprintMind, the delivery memory of the {team}. Today is {today}.
You get numbered evidence of two kinds:
- CURRENT STATE entries: the latest verified state from SprintMind's delivery ledger (GitHub PR/CI events,
  approvals, dashboard updates, the newest standups and meetings). They are authoritative for what is true now.
- Memories recalled from Hindsight: statements as of the date shown. Newer evidence supersedes older statements.
Rules:
- Cite evidence inline like [1] or [2][5] for every status, owner, date, approval, risk or customer request.
  Only cite numbers that exist.
- If a memory disagrees with CURRENT STATE or with a newer memory, give the current value and, when useful, what
  was said earlier and when (e.g. "planned for Friday on 25 Sep [4], now due 7 Oct [1]").
- Entries marked CORRECTION were supplied by team members and override older statements on the same topic.
- Never invent owners, dates, approvals, risks or statuses. If the evidence does not answer the question, say
  "Not recorded in SprintMind" and name what is missing.
- Lead with the answer, then short bullets. Call out blockers and risks explicitly.
{persona}"""

BRIEFING_SYSTEM = """You are SprintMind. Build a personal "what's next" briefing for {name} ({role}) from
the numbered evidence. Today is {today}. CURRENT STATE entries are authoritative for status, owner and due date;
older memories are historical. Use ONLY the evidence; cite evidence numbers in "sources". Do not invent anything.
Return ONLY JSON:
{{
  "headline": "one sentence: the single most important thing for {first} today",
  "top_priority": {{"task": "", "ticket": null, "why": "", "sources": [1]}},
  "my_tasks": [{{"ticket": null, "title": "", "status": "todo|in_progress|in_review|blocked|done", "due": null, "sources": [1]}}],
  "blockers": [{{"task": "", "waiting_on": "", "who_can_unblock": null, "sources": [1]}}],
  "customer_commitments": [{{"customer": "", "commitment": "", "due": null, "sources": [1]}}],
  "sops_to_follow": [{{"sop": "", "why": "", "sources": [1]}}]
}}
Omit nothing from the schema; use empty lists when there is nothing. Prefer the most recent status."""

RADAR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "3-4 sentence sprint health summary"},
        "health": {"type": "string", "enum": ["on_track", "at_risk", "off_track"]},
        "completed": {"type": "array", "items": {"type": "object", "properties": {
            "ticket": {"type": "string"}, "title": {"type": "string"}, "owner": {"type": "string"}}}},
        "in_progress": {"type": "array", "items": {"type": "object", "properties": {
            "ticket": {"type": "string"}, "title": {"type": "string"}, "owner": {"type": "string"},
            "status": {"type": "string"}, "due": {"type": "string"}}}},
        "blocked": {"type": "array", "items": {"type": "object", "properties": {
            "ticket": {"type": "string"}, "title": {"type": "string"}, "owner": {"type": "string"},
            "waiting_on": {"type": "string"}, "since": {"type": "string"}}}},
        "customer_commitments": {"type": "array", "items": {"type": "object", "properties": {
            "customer": {"type": "string"}, "commitment": {"type": "string"}, "owner": {"type": "string"},
            "due": {"type": "string"}, "risk": {"type": "string", "enum": ["low", "medium", "high"]}}}},
        "workload": {"type": "array", "items": {"type": "object", "properties": {
            "person": {"type": "string"}, "active_items": {"type": "integer"},
            "load": {"type": "string", "enum": ["light", "balanced", "overloaded"]}, "note": {"type": "string"}}}},
        "recommendations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "health", "completed", "in_progress", "blocked", "customer_commitments", "workload"],
}

RADAR_QUERY = (
    "Give a sprint {sprint} progress report for the {team}: which tickets are done, which are in "
    "progress or in review, which are blocked and on what, which customer commitments are open and "
    "how risky each is, and each person's current workload. Use the latest status of every ticket; "
    "engineering events from GitHub and recorded approvals override older meeting statements."
)

# Groq's free tier counts prompt + max reply against 8k tokens/minute. Keep prompts within this budget
# (about 4 characters per token); historical memories are trimmed first, current state last.
PROMPT_TOKEN_BUDGET = 4200
MEMORY_CHARS = 700

_CITE_RE = re.compile(r"\[\s*(\d+(?:\s*[,–-]\s*\d+)*)\s*\]")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%A %d %B %Y")


def _cite_numbers(group: str) -> list[int]:
    out = []
    for part in re.split(r"\s*,\s*", group):
        m = re.match(r"^(\d+)\s*[–-]\s*(\d+)$", part)
        if m and int(m.group(2)) - int(m.group(1)) < 20:
            out += list(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.isdigit():
            out.append(int(part))
    return out


def check_citations(answer: str, n: int) -> tuple[str, dict[str, Any]]:
    """Drop citation numbers that point at no evidence and report which evidence was cited."""
    cited: set[int] = set()
    invalid: set[int] = set()

    def fix(m: re.Match[str]) -> str:
        nums = _cite_numbers(m.group(1))
        keep = [x for x in nums if 1 <= x <= n]
        cited.update(keep)
        invalid.update(x for x in nums if not 1 <= x <= n)
        return "".join(f"[{x}]" for x in keep)

    out = _CITE_RE.sub(fix, answer or "")
    return out, {"cited": sorted(cited), "invalid_removed": sorted(invalid), "has_citations": bool(cited)}


def _is_correction(f: dict[str, Any]) -> bool:
    return "kind:correction" in (f.get("tags") or []) or (f.get("text") or "").startswith("CORRECTION")


def _number_facts(facts: list[dict[str, Any]]) -> str:
    lines = []
    for i, f in enumerate(facts, 1):
        if f.get("type") == "current_state":
            lines.append(f"[{i}] (verified {_today()}) {f['text']}")
            continue
        when = (f.get("occurred_start") or f.get("mentioned_at") or "")[:10]
        tag = "CORRECTION; " if _is_correction(f) else ""
        text = f["text"] if len(f["text"]) <= MEMORY_CHARS else f["text"][:MEMORY_CHARS] + " …"
        lines.append(f"[{i}] ({tag}{when or 'undated'}; {f.get('type')}) {text}")
    return "\n".join(lines) or "(no evidence found)"


def fit_evidence(state: list[dict[str, Any]], memories: list[dict[str, Any]], fixed_chars: int) -> tuple[list, list]:
    """Drop the least relevant historical memories, then state entries, until the prompt fits the budget."""
    state, memories = list(state), list(memories)
    size = lambda: fixed_chars + len(_number_facts(state + memories))  # noqa: E731
    while size() / 4 > PROMPT_TOKEN_BUDGET and (memories or len(state) > 3):
        (memories if memories else state).pop()
    return state, memories


def _dedupe(*lists: list[dict[str, Any]], limit: int = 24) -> list[dict[str, Any]]:
    seen, out = set(), []
    for lst in lists:
        for f in lst:
            key = f.get("id") or f.get("text")
            if key in seen:
                continue
            seen.add(key)
            out.append(f)
    return out[:limit]


def _chronological(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(facts, key=lambda f: (f.get("occurred_start") or f.get("mentioned_at") or "9999"))


class SprintMindAgent:
    def __init__(self, memory: MemoryService, llm: LLM | None, team: Team, sprint: str, learn: bool = True,
                 delivery: Any = None, extract: Callable[..., Awaitable[dict[str, Any]]] | None = None) -> None:
        self.memory = memory
        self.llm = llm
        self.team = team
        self.sprint = sprint
        self.learn = learn
        self.delivery = delivery
        self.extract = extract
        self._interactions: OrderedDict[str, dict[str, Any]] = OrderedDict()

    # ------------------------------------------------------------------ helpers
    def _require_llm(self) -> LLM:
        if self.llm is None:
            raise RuntimeError("LLM is not configured (set GROQ_API_KEY)")
        return self.llm

    def _remember_interaction(self, record: dict[str, Any]) -> str:
        iid = uuid.uuid4().hex[:12]
        self._interactions[iid] = record
        while len(self._interactions) > 500:
            self._interactions.popitem(last=False)
        return iid

    def interaction(self, iid: str) -> dict[str, Any] | None:
        return self._interactions.get(iid)

    async def _learn(self, text: str, tags: list[str], context: str, label: str) -> None:
        """Retain an interaction so future answers improve (the agent's own experience)."""
        if not self.learn:
            return
        try:
            await self.memory.retain(
                [{"content": text, "context": context, "timestamp": datetime.now(timezone.utc),
                  "tags": tags + [f"sprint:{self.sprint}"]}],
                label=label,
            )
        except Exception:  # noqa: BLE001 - learning must never break answering
            pass

    async def _evidence(self, question: str, person_id: str | None, caller: Caller, *, budget: str,
                        state_limit: int = 10) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Current state from the ledger + historical memories from Hindsight, filtered by visibility."""
        member = self.team.member(person_id) if person_id else None
        q = f"{question} (asked by {member['name']})" if member else question
        recalls = [
            self.memory.recall(q, budget=budget, label="team-wide"),
            self.memory.recall(question, tags=["kind:correction"], tags_match="any_strict", budget="low",
                               label="corrections"),
        ]
        if person_id:
            recalls.insert(0, self.memory.recall(q, tags=[f"person:{person_id}"], tags_match="any_strict",
                                                 budget=budget, label=f"personal:{person_id}"))
        results = await asyncio.gather(*recalls)
        corrections = [f for f in results[-1] if _is_correction(f)][:4]
        memories = [f for f in _dedupe(corrections, *results[:-1], limit=22) if visible_to(f, caller)]
        state = self.delivery.state_facts(question, person_id, limit=state_limit) if self.delivery else []
        return state, memories  # relevance order; callers trim, then present chronologically

    async def _cited_answer(self, question: str, person_id: str | None, caller: Caller, persona: str,
                            *, budget: str, label: str) -> dict[str, Any]:
        t0 = time.perf_counter()
        state, ranked = await self._evidence(question, person_id, caller, budget=budget)
        system = ANSWER_SYSTEM.format(team=self.team.name, today=_today(), persona=persona)
        state, kept = fit_evidence(state, ranked, len(system) + len(question) + 40)
        memories = _chronological(kept)
        facts = state + memories
        raw = await self._require_llm().complete(system, f"Evidence:\n{_number_facts(facts)}\n\nQuestion: {question}",
                                                 label=label)
        answer, citations = check_citations(raw, len(facts))
        return {"answer": answer, "memories": facts, "memory_count": len(memories), "state_count": len(state),
                "citations": citations, "latency_ms": round((time.perf_counter() - t0) * 1000)}

    # ------------------------------------------------------------ employee view
    async def ask(self, question: str, person_id: str | None = None, *, budget: str = "mid",
                  caller: Caller = ANONYMOUS) -> dict[str, Any]:
        """Employee question: current state + personal/team/correction recall, then a cited answer."""
        member = self.team.member(person_id) if person_id else None
        persona = (f"The person asking is {member['name']} ({member['role']}). 'I', 'me', 'my' refer to them."
                   if member else "")
        out = await self._cited_answer(question, person_id, caller, persona, budget=budget, label="answer")
        who = member["name"] if member else (caller.name or "A team member")
        iid = self._remember_interaction({"question": question, "person_id": person_id, "answer": out["answer"],
                                          "asked_by": caller.person_id})
        await self._learn(
            f"{who} asked SprintMind: \"{question}\". SprintMind answered: {out['answer'][:600]}",
            tags=["source:interaction"] + ([f"person:{person_id}"] if person_id else [])
            + ([f"person:{caller.person_id}"] if caller.person_id and caller.person_id != person_id else []),
            context="employee_question", label="learn: Q&A",
        )
        return {"interaction_id": iid, **out}

    async def briefing(self, person_id: str, caller: Caller = ANONYMOUS) -> dict[str, Any]:
        """Personal "What's next?" briefing card for the employee portal."""
        member = self.team.member(person_id)
        if not member:
            raise KeyError(person_id)
        name = member["name"]
        scoped = {"tags": [f"person:{person_id}"], "tags_match": "any_strict"}
        mine, blockers, commitments, sops = await asyncio.gather(
            self.memory.recall(f"tasks and tickets owned by {name} and their latest status in sprint {self.sprint}",
                               label="briefing:tasks", **scoped),
            self.memory.recall(f"what is blocking {name}", label="briefing:blockers", **scoped),
            self.memory.recall(f"customer commitments and deliverables assigned to {name}",
                               label="briefing:commitments", **scoped),
            self.memory.recall(f"SOP steps relevant to {name}'s work as {member['role']}",
                               tags=["source:sop"], tags_match="any_strict", budget="low", label="briefing:sops"),
        )
        state = self.delivery.state_facts(f"work owned by {name}", person_id, limit=10) if self.delivery else []
        ranked = [f for f in _dedupe(mine, blockers, commitments, sops, limit=24) if visible_to(f, caller)]
        system = BRIEFING_SYSTEM.format(name=name, first=name.split()[0], role=member["role"], today=_today())
        state, kept = fit_evidence(state, ranked, len(system) + 40)
        facts = state + _chronological(kept)
        data = await self._require_llm().complete_json(system, f"Evidence:\n{_number_facts(facts)}", label="briefing")
        return {"person": member, "briefing": data, "memories": facts}

    # ------------------------------------------------------------- manager view
    def _state_context(self) -> str:
        if not self.delivery:
            return ""
        d = self.delivery.dashboard()
        lines = [f"- RISK ({r['severity']}): {r['title']}: {r['explanation']}" for r in d["risks"][:12]]
        lines += [f"- {self.delivery.state_line(self.delivery.ledger.get_item(c['id']))}" for c in d["commitments"][:10]]
        lines += [f"- ENGINEERING: {e['title']} ({e['at'][:16]})" for e in d["engineering"][:8]]
        return "Current verified delivery state (authoritative over older memories):\n" + "\n".join(lines)

    async def radar(self, budget: str = "mid") -> dict[str, Any]:
        """Structured sprint health narrative synthesised by Hindsight reflect()."""
        t0 = time.perf_counter()
        res = await self.memory.reflect(
            RADAR_QUERY.format(sprint=self.sprint, team=self.team.name),
            context=f"Today is {_today()}. Team roster:\n{self.team.roster_text()}\n\n{self._state_context()}",
            response_schema=RADAR_SCHEMA, budget=budget, label="sprint radar",
        )
        report = res["structured"]
        if not isinstance(report, dict) and self.llm is not None:
            # Server couldn't produce structured output: convert its prose answer ourselves.
            try:
                report = await self.llm.complete_json(
                    "Convert this sprint report into JSON matching the schema. Use only its content.\n"
                    f"Schema: {RADAR_SCHEMA}",
                    res["text"] or "", label="radar:structure",
                )
            except Exception:  # noqa: BLE001
                report = None
        return {
            "report": report,
            "narrative": res["text"],
            "based_on": res["facts"],
            "mental_models_used": res["mental_models"],
            "latency_ms": round((time.perf_counter() - t0) * 1000),
        }

    async def manager_ask(self, question: str, budget: str = "mid", caller: Caller = ANONYMOUS) -> dict[str, Any]:
        """Free-form manager question ("Who is working on SSO?"), answered with citations."""
        out = await self._cited_answer(question, None, caller,
                                       "The person asking is the engineering manager; answer for the whole team.",
                                       budget=budget, label="manager answer")
        iid = self._remember_interaction({"question": question, "person_id": "manager", "answer": out["answer"],
                                          "asked_by": caller.person_id})
        await self._learn(
            f"The engineering manager asked SprintMind: \"{question}\". SprintMind answered: {out['answer'][:600]}",
            tags=["source:interaction", "role:manager"], context="manager_question", label="learn: manager Q&A",
        )
        return {"interaction_id": iid, **out, "based_on": out["memories"]}

    # ----------------------------------------------------------------- SOP vault
    async def sop_ask(self, question: str) -> dict[str, Any]:
        facts = await self.memory.recall(question, tags=["source:sop"], tags_match="any_strict", label="sop vault")
        system = ANSWER_SYSTEM.format(team=self.team.name, today=_today(),
                                      persona="Answer as the team's SOP guide: give the exact steps in order.")
        raw = await self._require_llm().complete(
            system, f"Evidence:\n{_number_facts(facts)}\n\nQuestion: {question}", label="sop answer")
        answer, citations = check_citations(raw, len(facts))
        return {"answer": answer, "memories": facts, "citations": citations}

    # ------------------------------------------------------------- demo: before
    async def baseline(self, question: str) -> dict[str, Any]:
        """The same question to the same LLM with no memory - the "before" half of the demo."""
        t0 = time.perf_counter()
        answer = await self._require_llm().complete(
            "You are a helpful assistant for a software team.", question, label="baseline (no memory)")
        return {"answer": answer, "memories": [], "latency_ms": round((time.perf_counter() - t0) * 1000)}

    async def compare(self, question: str, person_id: str | None = None, caller: Caller = ANONYMOUS) -> dict[str, Any]:
        without, with_memory = await asyncio.gather(self.baseline(question), self.ask(question, person_id, caller=caller))
        return {"question": question, "without_memory": without, "with_memory": with_memory}

    # ---------------------------------------------------------- learning loop
    async def feedback(self, interaction_id: str, helpful: bool, correction: str | None,
                       caller: Caller = ANONYMOUS) -> dict[str, Any]:
        """Store ratings and corrections. Corrections are shared, recalled first, and applied to the ledger."""
        rec = self._interactions.get(interaction_id)
        if rec is None:
            raise KeyError(interaction_id)
        who = caller.name or "A team member"
        tags = ["source:feedback", f"sprint:{self.sprint}"] + ([f"person:{caller.person_id}"] if caller.person_id else [])
        if rec.get("person_id") and rec["person_id"] not in ("manager", caller.person_id):
            tags.append(f"person:{rec['person_id']}")
        applied: list[dict[str, Any]] = []
        if correction:
            text = (f"CORRECTION from {who} on {datetime.now(timezone.utc).date().isoformat()}: regarding "
                    f"\"{rec['question']}\", the correct information is: {correction}. This supersedes earlier statements.")
            tags.append("kind:correction")
            if self.delivery:
                tickets = self.delivery.ticket_re.findall(correction + " " + rec["question"])
                tags += [f"ticket:{k.upper()}-{int(n)}" for k, n in tickets]
            applied = await self._apply_correction(correction, rec, caller)
        else:
            text = (f"{who} rated SprintMind's answer to \"{rec['question']}\" as "
                    f"{'helpful' if helpful else 'not helpful'}.")
            tags.append("kind:rating")
        res = await self.memory.retain(
            [{"content": text, "context": "user_feedback", "timestamp": datetime.now(timezone.utc), "tags": sorted(set(tags))}],
            label="learn: correction" if correction else "learn: rating",
        )
        return {"stored": True, "memory": text, "retain": res, "applied_to_state": applied}

    async def _apply_correction(self, correction: str, rec: dict[str, Any], caller: Caller) -> list[dict[str, Any]]:
        """Turn a correction into structured statements (only what it states) and apply them as authoritative."""
        if not (self.delivery and self.extract and self.llm):
            return []
        try:
            data = await self.extract("note", f"Correction to: {rec['question']}", correction, datetime.now(timezone.utc))
        except Exception:  # noqa: BLE001 - the correction is still retained as a memory
            return []
        statements = [s for s in data.get("items", []) if s.get("ticket") or s.get("kind") in ("commitment", "decision")]
        if not statements:
            return []
        ids = await self.delivery.apply_correction(statements, actor=caller.name, text=correction)
        return [{"statement": s.get("title") or s.get("detail"), "item_id": i} for s, i in zip(statements, ids)]
