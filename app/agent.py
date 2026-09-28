"""SprintMind agent: the query side of the system.

  Employee portal  -> recall() scoped to the person + team SOPs, then Groq writes a cited answer
  Manager radar    -> reflect() over the whole bank, with a JSON schema for the dashboard
  SOP vault        -> recall() restricted to source:sop
  Baseline         -> the same question to Groq with NO memory (demo "before")
  Learning loop    -> every Q&A and every user correction is retained back into Hindsight
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

from .llm import LLM
from .memory import MemoryService
from .team import Team

ANSWER_SYSTEM = """You are SprintMind, a scrum copilot for the {team}. Today is {today}.
Answer ONLY from the numbered memories provided. Rules:
- Cite memories inline like [1], [3] for every task status, owner, deadline or customer request.
- If memories conflict, trust the most recent one and say the status changed.
- If the memories do not contain the answer, say exactly what is unknown. Never invent status.
- Be concise and practical: lead with the answer, then bullets. Call out blockers explicitly.
{persona}"""

BRIEFING_SYSTEM = """You are SprintMind. Build a personal "what's next" briefing for {name} ({role}) from
the numbered memories. Today is {today}. Use ONLY the memories; cite memory numbers in "sources".
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
    "how risky each is, and each person's current workload. Use the latest status of every ticket."
)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%A %d %B %Y")


def _number_facts(facts: list[dict[str, Any]]) -> str:
    lines = []
    for i, f in enumerate(facts, 1):
        when = (f.get("occurred_start") or f.get("mentioned_at") or "")[:10]
        lines.append(f"[{i}] ({when or 'undated'}; {f.get('type')}) {f['text']}")
    return "\n".join(lines) or "(no memories found)"


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


class SprintMindAgent:
    def __init__(self, memory: MemoryService, llm: LLM | None, team: Team, sprint: str, learn: bool = True) -> None:
        self.memory = memory
        self.llm = llm
        self.team = team
        self.sprint = sprint
        self.learn = learn
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

    # ------------------------------------------------------------ employee view
    async def ask(self, question: str, person_id: str | None = None, *, budget: str = "mid") -> dict[str, Any]:
        """Employee question: personal-scoped recall + team-wide recall, then a cited answer."""
        t0 = time.perf_counter()
        member = self.team.member(person_id) if person_id else None
        who = member["name"] if member else None
        q = f"{question} (asked by {who})" if who else question

        recalls = [self.memory.recall(q, budget=budget, label="team-wide")]
        if person_id:
            recalls.insert(0, self.memory.recall(
                q, tags=[f"person:{person_id}"], tags_match="any_strict", budget=budget, label=f"personal:{person_id}"))
        results = await asyncio.gather(*recalls)
        facts = _dedupe(*results)

        persona = (f"The person asking is {member['name']} ({member['role']}). 'I', 'me', 'my' refer to them."
                   if member else "")
        system = ANSWER_SYSTEM.format(team=self.team.name, today=_today(), persona=persona)
        user = f"Memories:\n{_number_facts(facts)}\n\nQuestion: {question}"
        answer = await self._require_llm().complete(system, user, label="answer")

        iid = self._remember_interaction({"question": question, "person_id": person_id, "answer": answer, "facts": facts})
        await self._learn(
            f"{who or 'A team member'} asked SprintMind: \"{question}\". SprintMind answered: {answer[:600]}",
            tags=["source:interaction"] + ([f"person:{person_id}"] if person_id else []),
            context="employee_question", label="learn: Q&A",
        )
        return {
            "interaction_id": iid,
            "answer": answer,
            "memories": facts,
            "memory_count": len(facts),
            "latency_ms": round((time.perf_counter() - t0) * 1000),
        }

    async def briefing(self, person_id: str) -> dict[str, Any]:
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
        facts = _dedupe(mine, blockers, commitments, sops, limit=30)
        system = BRIEFING_SYSTEM.format(name=name, first=name.split()[0], role=member["role"], today=_today())
        data = await self._require_llm().complete_json(system, f"Memories:\n{_number_facts(facts)}", label="briefing")
        return {"person": member, "briefing": data, "memories": facts}

    # ------------------------------------------------------------- manager view
    async def radar(self, budget: str = "mid") -> dict[str, Any]:
        """Structured sprint health report synthesised by Hindsight reflect()."""
        t0 = time.perf_counter()
        res = await self.memory.reflect(
            RADAR_QUERY.format(sprint=self.sprint, team=self.team.name),
            context=f"Today is {_today()}. Team roster:\n{self.team.roster_text()}",
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

    async def manager_ask(self, question: str, budget: str = "mid") -> dict[str, Any]:
        """Free-form manager question ("Who is working on SSO?") answered by reflect()."""
        t0 = time.perf_counter()
        res = await self.memory.reflect(
            question,
            context=f"Asked by the engineering manager. Today is {_today()}. Roster:\n{self.team.roster_text()}",
            budget=budget, label="manager question",
        )
        iid = self._remember_interaction({"question": question, "person_id": "manager", "answer": res["text"],
                                          "facts": res["facts"]})
        await self._learn(
            f"The engineering manager asked SprintMind: \"{question}\". SprintMind answered: {(res['text'] or '')[:600]}",
            tags=["source:interaction", "role:manager"], context="manager_question", label="learn: manager Q&A",
        )
        return {"interaction_id": iid, "answer": res["text"], "based_on": res["facts"],
                "mental_models_used": res["mental_models"], "latency_ms": round((time.perf_counter() - t0) * 1000)}

    # ----------------------------------------------------------------- SOP vault
    async def sop_ask(self, question: str) -> dict[str, Any]:
        facts = await self.memory.recall(question, tags=["source:sop"], tags_match="any_strict", label="sop vault")
        system = ANSWER_SYSTEM.format(team=self.team.name, today=_today(),
                                      persona="Answer as the team's SOP guide: give the exact steps in order.")
        answer = await self._require_llm().complete(
            system, f"Memories:\n{_number_facts(facts)}\n\nQuestion: {question}", label="sop answer")
        return {"answer": answer, "memories": facts}

    # ------------------------------------------------------------- demo: before
    async def baseline(self, question: str) -> dict[str, Any]:
        """The same question to the same LLM with no memory - the "before" half of the demo."""
        t0 = time.perf_counter()
        answer = await self._require_llm().complete(
            "You are a helpful assistant for a software team.", question, label="baseline (no memory)")
        return {"answer": answer, "memories": [], "latency_ms": round((time.perf_counter() - t0) * 1000)}

    async def compare(self, question: str, person_id: str | None = None) -> dict[str, Any]:
        without, with_memory = await asyncio.gather(self.baseline(question), self.ask(question, person_id))
        return {"question": question, "without_memory": without, "with_memory": with_memory}

    # ---------------------------------------------------------- learning loop
    async def feedback(self, interaction_id: str, helpful: bool, correction: str | None, person_id: str | None) -> dict[str, Any]:
        """Store user feedback/corrections so the next answer is better."""
        rec = self._interactions.get(interaction_id)
        if rec is None:
            raise KeyError(interaction_id)
        who = self.team.member(person_id)["name"] if person_id and self.team.member(person_id) else "A team member"
        if correction:
            text = (f"CORRECTION from {who}: regarding \"{rec['question']}\", the correct information is: "
                    f"{correction}. This supersedes earlier statements.")
        else:
            text = (f"{who} rated SprintMind's answer to \"{rec['question']}\" as "
                    f"{'helpful' if helpful else 'not helpful'}.")
        tags = ["source:feedback"] + ([f"person:{person_id}"] if person_id else [])
        if rec.get("person_id") and rec["person_id"] not in ("manager", person_id):
            tags.append(f"person:{rec['person_id']}")
        res = await self.memory.retain(
            [{"content": text, "context": "user_feedback", "timestamp": datetime.now(timezone.utc),
              "tags": tags + [f"sprint:{self.sprint}"]}],
            label="learn: feedback",
        )
        return {"stored": True, "memory": text, "retain": res}
