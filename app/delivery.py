"""Delivery intelligence: connects commitments, engineering activity and Hindsight memory.

Every writer goes through this service, so the same pipeline runs whether a fact came from a
meeting transcript, a standup, a Jira digest, a dashboard edit, a customer approval, a correction or
a GitHub webhook (real or demo):

    source -> ledger event (deduplicated, timestamped, traceable)
           -> item updates with source-aware conflict rules (history kept)
           -> dependencies / approvals opened or resolved
           -> Hindsight retain (so recall / reflect see engineering events and risk changes too)
           -> deterministic risk re-evaluation (RiskEngine), transitions logged as events
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .github import EngineeringEvent, find_tickets, normalise, ticket_regex
from .ledger import (DATE_RE, ITEM_STATUSES, OPEN_STATUSES, VERIFIED_AUTHORITY, Ledger, authority, iso, keywords, parse_dt, similarity,
                     utcnow)
from .llm import LLM
from .memory import MemoryService, slug
from .risk import SEVERITY_RANK, RiskEngine, business_days_between
from .team import Team

APPROVED_RE = re.compile(r"\b(approved|approves|signed[- ]off|sign[- ]off (?:received|given)|"
                         r"approval (?:is |was |has been )?(?:granted|given|received|confirmed))\b", re.I)
NEGATION_RE = re.compile(r"\b(not|no|pending|awaiting|waiting|needs?|requires?|required|until|unless|once|if|"
                         r"request(?:ed|ing)?|asked|proposed)\b", re.I)
APPROVAL_NEED_RE = re.compile(r"\b(approv\w*|sign[- ]?off|written confirmation)\b", re.I)
ACTION_KINDS = {"task", "commitment", "blocker", "risk", "follow_up"}
# Which statement names a ticket best: a planned task beats a commitment, a status line or a blocker note.
TITLE_RANK = {"task": 3, "commitment": 2, "github": 2, "status_update": 1, "blocker": 1}
# Standups and Jira activity name whoever did a piece of work (UI, API, QA...), not the ticket's owner.
# Once a ticket has an owner, only an assignment (a meeting such as planning, a dashboard edit, a correction) changes it.
OWNER_SOURCES = {"meeting", "dashboard", "feedback"}

EXPLAIN_SYSTEM = """You explain one delivery risk to an engineering manager.
Use ONLY the numbered evidence. Cite every fact inline like [E1] or [M2]. Do not add owners, dates,
approvals or statuses that are not in the evidence. If the evidence does not say why something happened,
say that it is not recorded. 3-5 sentences, then one line starting "Next step:" repeating the recommended action."""


def _clean_ticket(value: Any) -> str | None:
    if not value:
        return None
    m = re.match(r"^\s*([A-Za-z][A-Za-z0-9]{1,9})[-_ ]?(\d{1,6})\s*$", str(value))
    return f"{m.group(1).upper()}-{int(m.group(2))}" if m else None


class DeliveryService:
    def __init__(self, ledger: Ledger, memory: MemoryService, team: Team, *, sprint: str, ticket_prefixes: str = "NW",
                 repo: str = "northwind/platform", tz: str = "Asia/Kolkata", ticket_url_template: str | None = None,
                 llm: LLM | None = None, clock: Callable[[], datetime] = utcnow) -> None:
        self.ledger = ledger
        self.memory = memory
        self.team = team
        self.sprint = sprint
        self.ticket_re = ticket_regex(ticket_prefixes)
        self.repo = repo
        self.tz = tz
        self.ticket_url_template = ticket_url_template
        self.llm = llm
        self.clock = clock

    def now(self) -> datetime:
        return self.clock()

    # ---------------------------------------------------------------- helpers
    def _person(self, name: str | None) -> tuple[str | None, str | None]:
        if not name:
            return None, None
        pid = self.team.resolve_person(name)
        member = self.team.member(pid) if pid else None
        return (pid, member["name"]) if member else (None, str(name).strip()[:120])

    def _customer(self, name: str | None) -> str | None:
        return self.team.resolve_customer(name) if name else None

    def customer_name(self, cid: str | None) -> str | None:
        if not cid:
            return None
        return next((c["name"] for c in self.team.customers if c["id"] == cid), cid)

    def _fields(self, it: dict[str, Any], customer: str | None) -> dict[str, Any]:
        owner_id, owner = self._person(it.get("owner"))
        status = it.get("status") if it.get("status") in ITEM_STATUSES else None
        if it.get("kind") == "blocker":
            status = "blocked"
        due = it.get("due") if it.get("due") and DATE_RE.match(str(it["due"])) else None
        return {k: v for k, v in {
            "status": status, "owner": owner, "owner_id": owner_id, "due": due,
            "priority": it.get("priority") if it.get("priority") in ("P0", "P1", "P2", "P3") else None,
            "blocked_on": it.get("blocked_on") if status == "blocked" else None,
            "customer": customer or self._customer(it.get("customer")),
            "detail": it.get("detail"),
        }.items() if v}

    def _ticket_item(self, ticket: str, event: dict[str, Any], title: str | None = None,
                     fields: dict[str, Any] | None = None) -> tuple[dict[str, Any], bool]:
        item = self.ledger.get_item(f"tkt-{ticket}")
        if item:
            return item, False
        f = {k: v for k, v in (fields or {}).items() if v is not None}
        status = f.pop("status", None) or "todo"
        stage = f.pop("scrum_stage", None) or "sprint_backlog"
        return self.ledger.create_item(item_id=f"tkt-{ticket}", kind="task", title=title or ticket, event=event,
                                       status=status, ticket=ticket, scrum_stage=stage, **f), True

    def _match(self, kinds: tuple[str, ...], text: str, *, owner_id: str | None = None, customer: str | None = None,
               ticket: str | None = None, threshold: float = 0.5) -> dict[str, Any] | None:
        best, best_score = None, threshold
        for it in self.ledger.items():
            if it["kind"] not in kinds or it["id"].startswith("tkt-") or it["status"] not in OPEN_STATUSES:
                continue
            if customer and it.get("customer") and it["customer"] != customer:
                continue
            if ticket and it.get("ticket") and it["ticket"] != ticket:
                continue
            if owner_id and it.get("owner_id") and it["owner_id"] != owner_id:
                continue
            score = similarity(text, f"{it['title']} {it.get('detail') or ''}")
            if ticket and it.get("ticket") == ticket:
                score += 0.2
            if score >= best_score:
                best, best_score = it, score
        return best

    def _infer_ticket(self, item: dict[str, Any], event: dict[str, Any]) -> None:
        """Link a commitment to the ticket whose title shares the most keywords (>= 2), marked as inferred."""
        text = keywords(f"{item['title']} {item.get('detail') or ''}")
        best, best_overlap = None, 1
        for t in self.ledger.items(kind="task"):
            if not t["id"].startswith("tkt-"):
                continue
            overlap = len(text & keywords(f"{t['title']} {t.get('detail') or ''}"))
            if overlap > best_overlap:
                best, best_overlap = t, overlap
        if best:
            self.ledger.apply(item["id"], {"ticket": best["ticket"]}, event, by="SprintMind",
                              note=f"inferred link: shares {best_overlap} keywords with {best['ticket']} '{best['title']}'")
            self.ledger.set_ticket_link(item["id"], best["ticket"], inferred=True)

    @staticmethod
    def is_approval(text: str) -> bool:
        return bool(APPROVED_RE.search(text or "")) and not NEGATION_RE.search(text or "")

    def _dependency_kind(self, text: str) -> tuple[str, str | None]:
        other = find_tickets(self.ticket_re, text)
        if other:
            return "item", f"tkt-{other[0]}"
        return ("approval" if APPROVAL_NEED_RE.search(text) else "external"), None

    def _party(self, text: str, fallback: str | None) -> str | None:
        for c in self.team.customers:
            for alias in [c["name"], *c.get("aliases", [])]:
                if alias.lower() in (text or "").lower():
                    return c["name"]
        return self.customer_name(fallback)

    def _open_dependency(self, item: dict[str, Any], description: str, event: dict[str, Any]) -> dict[str, Any] | None:
        kind, target = self._dependency_kind(description)
        if target == item["id"]:
            kind, target = ("approval" if APPROVAL_NEED_RE.search(description) else "external"), None
        if kind == "item" and not self.ledger.get_item(target):
            kind, target = "external", None
        dep, _ = self.ledger.add_dependency(item_id=item["id"], kind=kind, description=description, event=event,
                                            party=self._party(description, item.get("customer")) if kind == "approval" else None,
                                            depends_on_item_id=target)
        return dep

    def _unblock_if_clear(self, item_id: str, event: dict[str, Any], why: str) -> None:
        item = self.ledger.get_item(item_id)
        if item and item["status"] == "blocked" and not self.ledger.dependencies(item_id, status="open"):
            self.ledger.apply(item_id, {"status": "todo"}, event, by="SprintMind",
                              note=f"unblocked: {why}. Set to 'todo' until progress is reported.", force=True)

    async def _retain(self, items: list[dict[str, Any]], label: str, document_id: str | None = None) -> None:
        if not items:
            return
        for it in items:
            it.setdefault("timestamp", self.now())
            it["tags"] = sorted(set(it.get("tags", []) + [f"sprint:{self.sprint}"]))
        try:
            await self.memory.retain(items, document_id=document_id, label=label)
        except Exception:  # noqa: BLE001 - the ledger is the system of record; memory is best-effort here
            pass

    def _item_tags(self, item: dict[str, Any]) -> list[str]:
        tags = [f"item:{item['id']}"]
        if item.get("ticket"):
            tags.append(f"ticket:{item['ticket']}")
        if item.get("owner_id"):
            tags.append(f"person:{item['owner_id']}")
        if item.get("customer"):
            tags.append(f"customer:{item['customer']}")
        return tags

    # ------------------------------------------------------ documents / meetings
    async def record_extraction(self, *, source_type: str, title: str, document_id: str, occurred_at: datetime,
                                items: list[dict[str, Any]], customer: str | None = None, meeting_id: str | None = None,
                                recompute: bool = True) -> list[str | None]:
        """Turn extracted statements into ledger events and item updates. Returns an item id per statement."""
        doc_event, _ = self.ledger.add_event(dedup_key=f"doc:{document_id}", source=source_type, kind="document.ingested",
                                             occurred_at=occurred_at, title=f"{source_type.replace('_', ' ')}: {title}",
                                             source_ref=meeting_id or document_id, memory_document_id=document_id,
                                             payload={"document_title": title, "statements": len(items)})
        ids: list[str | None] = []
        for it in items:
            kind = (it.get("kind") or "fact").lower()
            ticket = _clean_ticket(it.get("ticket"))
            label = it.get("title") or it.get("detail") or ""
            ev, created = self.ledger.add_event(
                dedup_key=f"doc:{document_id}:{kind}:{ticket or ''}:{slug(label)[:60]}", source=source_type,
                kind=f"statement.{kind}", occurred_at=occurred_at, title=f"{title}: {label}"[:300], actor=it.get("owner"),
                tickets=[ticket] if ticket else [], source_ref=meeting_id or document_id,
                memory_document_id=f"{document_id}::facts", payload={"statement": it, "document_title": title})
            if not created:
                ids.append(ev["item_ids"][0] if ev["item_ids"] else None)
                continue
            ids.append(await self._apply_statement(it, kind, ticket, ev, customer, meeting_id))
        if recompute:
            await self.recompute(trigger=doc_event)
        return ids

    async def _apply_statement(self, it: dict[str, Any], kind: str, ticket: str | None, ev: dict[str, Any],
                               customer: str | None, meeting_id: str | None) -> str | None:
        text = " ".join(x for x in (it.get("title"), it.get("detail")) if x)
        cust = customer or self._customer(it.get("customer"))
        if kind in ("decision", "status_update", "commitment", "fact") and self.is_approval(text):
            await self._approval_from_statement(text, ticket, cust, ev)
        if kind in ("decision", "sop_step", "risk", "fact"):
            return None
        if kind == "commitment" and not cust:
            kind = "task"  # a "commitment" nobody outside the team was promised is sprint work, not a customer commitment
        fields = self._fields(it, cust)
        item = None
        if ticket and kind in ("task", "status_update", "blocker"):
            item, created = self._ticket_item(ticket, ev, title=it.get("title"),
                                              fields=fields | {"scrum_stage": it.get("scrum_stage")})
            if created:
                self.ledger.apply(item["id"], {"title": it.get("title"), "_title_rank": TITLE_RANK.get(kind, 0)}, ev)
            else:
                if item.get("owner") and (kind in ("status_update", "blocker") or ev["source"] not in OWNER_SOURCES):
                    fields.pop("owner", None), fields.pop("owner_id", None)
                verified = authority((item["field_meta"].get("status") or {}).get("source")) >= VERIFIED_AUTHORITY
                if fields.get("status") == "done" and not verified and authority(ev["source"]) < authority("task_update") and \
                        similarity(it.get("title"), item["title"]) < 0.2:
                    # "NW-244: wireframes reviewed, done" finishes a sub-step, not the ticket
                    fields.pop("status")
                    self.ledger.record_not_applied(item["id"], "status", "done", ev,
                                                   f"sub-step reported done ('{it.get('title')}'); ticket status unchanged")
                self.ledger.apply(item["id"], fields | {"title": it.get("title"), "_title_rank": TITLE_RANK.get(kind, 0)}, ev)
        elif kind == "commitment":
            item = self._match(("commitment",), text, customer=cust, ticket=ticket, threshold=0.45)
            if item:
                self.ledger.apply(item["id"], fields | ({"ticket": ticket} if ticket else {}), ev)
            else:
                iid = "cmt-" + hashlib.sha1(f"{ev['id']}".encode()).hexdigest()[:10]
                status = fields.pop("status", None) or "todo"
                item = self.ledger.create_item(item_id=iid, kind="commitment", title=it.get("title") or text, event=ev,
                                               status=status, ticket=ticket, meeting_id=meeting_id,
                                               scrum_stage=it.get("scrum_stage") or "sprint_review", **fields)
                if not ticket:
                    self._infer_ticket(item, ev)
        elif kind in ("task", "follow_up", "blocker"):
            item = self._match(("task", "follow_up", "blocker"), text, owner_id=fields.get("owner_id"), customer=cust,
                               threshold=0.6)
            if item:
                self.ledger.apply(item["id"], fields, ev)
            else:
                iid = "act-" + hashlib.sha1(f"{ev['id']}".encode()).hexdigest()[:10]
                status = fields.pop("status", None) or ("blocked" if kind == "blocker" else "todo")
                item = self.ledger.create_item(item_id=iid, kind=kind, title=it.get("title") or text, event=ev, status=status,
                                               meeting_id=meeting_id, priority=fields.pop("priority", None),
                                               scrum_stage=it.get("scrum_stage") or ("impediment" if kind == "blocker" else "sprint_backlog"),
                                               **{k: v for k, v in fields.items() if k != "priority"})
        elif kind == "status_update":
            item = self._match(("task", "follow_up", "blocker", "commitment"), text, owner_id=fields.get("owner_id"),
                               threshold=0.5)
            if item:
                self.ledger.apply(item["id"], fields, ev)
        if item is None:
            return None
        item = self.ledger.get_item(item["id"])
        if item["status"] == "blocked" and (it.get("blocked_on") or item.get("blocked_on")):
            self._open_dependency(item, it.get("blocked_on") or item["blocked_on"], ev)
        elif fields.get("status") in ("in_progress", "in_review", "done") and item["status"] == fields["status"]:
            for d in self.ledger.dependencies(item["id"], status="open", kind="external"):
                self.ledger.resolve_dependency(d["id"], ev, f"{ev['source']} reports status '{fields['status']}'")
        return item["id"]

    async def _approval_from_statement(self, text: str, ticket: str | None, customer: str | None,
                                       ev: dict[str, Any]) -> list[dict[str, Any]]:
        resolved = []
        words = keywords(text)
        for d in self.ledger.dependencies(status="open", kind="approval"):
            item = self.ledger.get_item(d["item_id"])
            if item is None:
                continue
            same_ticket = ticket and item.get("ticket") == ticket
            same_customer = customer and (item.get("customer") == customer or
                                          (d.get("party") or "").lower() == (self.customer_name(customer) or "").lower())
            overlap = words & keywords(d["description"] + " " + item["title"])
            if same_ticket or (same_customer and len(overlap) >= 1):
                resolved.append(await self._grant_approval(d, ev, f"'{text[:160]}'", ev.get("actor")))
        return resolved

    async def _grant_approval(self, dep: dict[str, Any], source_event: dict[str, Any], evidence: str,
                              actor: str | None) -> dict[str, Any]:
        item = self.ledger.get_item(dep["item_id"])
        appr, _ = self.ledger.add_event(
            dedup_key=f"approval:{dep['id']}:{source_event['id']}", source="approval", kind="approval.granted",
            occurred_at=source_event["occurred_at"], title=f"Approval recorded for {item.get('ticket') or item['title']}: {dep['description'][:120]}",
            actor=actor, tickets=[item["ticket"]] if item.get("ticket") else [], item_ids=[item["id"]],
            source_ref=source_event.get("source_ref"), payload={"dependency_id": dep["id"], "evidence": evidence,
                                                               "derived_from": source_event["id"]})
        self.ledger.resolve_dependency(dep["id"], appr, f"approved: {evidence} ({source_event['source']}, {source_event['occurred_at'][:10]})")
        self._unblock_if_clear(item["id"], appr, f"approval recorded ({evidence[:80]})")
        await self._retain([{"content": f"[approval] {appr['title']}. Evidence: {evidence} from {source_event['source']} "
                                        f"on {source_event['occurred_at'][:10]}.",
                             "context": "approval", "timestamp": parse_dt(source_event["occurred_at"]),
                             "tags": ["source:approval", "kind:approval"] + self._item_tags(item)}],
                           label=f"approval: {item.get('ticket') or item['title'][:40]}")
        return self.ledger.get_dependency(dep["id"])

    # ------------------------------------------------------------ people edits
    async def create_commitment(self, *, title: str, customer: str | None, owner: str | None, due: str | None,
                                ticket: str | None, detail: str | None, actor: str | None) -> dict[str, Any]:
        ev, _ = self.ledger.add_event(dedup_key=f"dash:{uuid.uuid4()}", source="dashboard", kind="commitment.created",
                                      occurred_at=self.now(), title=f"Commitment recorded: {title}", actor=actor,
                                      tickets=[_clean_ticket(ticket)] if _clean_ticket(ticket) else [],
                                      payload={"title": title, "customer": customer, "owner": owner, "due": due})
        ids = await self.record_extraction(
            source_type="dashboard", title=f"Commitment recorded by {actor or 'dashboard'}", document_id=f"commit-{ev['id']}",
            occurred_at=self.now(), items=[{"kind": "commitment", "title": title, "detail": detail, "owner": owner,
                                            "due": due, "ticket": ticket, "customer": customer, "status": "todo"}])
        item = self.ledger.get_item(ids[0]) if ids and ids[0] else None
        if item is None:
            raise ValueError("commitment could not be recorded")
        await self._retain([{"content": self.state_line(item, prefix="[commitment recorded]"), "context": "commitment",
                             "tags": ["source:dashboard", "kind:commitment"] + self._item_tags(item)}],
                           label=f"commitment: {title[:40]}")
        return self.item_view(item)

    async def update_item(self, item_id: str, *, status: str | None = None, owner: str | None = None,
                          due: str | None = None, ticket: str | None = None, note: str | None = None,
                          actor: str | None = None) -> dict[str, Any]:
        item = self.ledger.get_item(item_id)
        if item is None:
            raise KeyError(item_id)
        changes: dict[str, Any] = {}
        if status:
            if status not in ITEM_STATUSES:
                raise ValueError(f"status must be one of {ITEM_STATUSES}")
            changes["status"] = status
        if owner:
            oid = self.team.resolve_person(owner)
            if not oid:
                raise ValueError(f"unknown owner {owner!r}")
            changes.update(owner=self.team.member(oid)["name"], owner_id=oid)
        if due:
            if not DATE_RE.match(due):
                raise ValueError("due must be YYYY-MM-DD")
            changes["due"] = due
        if ticket:
            t = _clean_ticket(ticket)
            if not t:
                raise ValueError("ticket must look like NW-231")
            changes["ticket"] = t
        if not changes and not note:
            raise ValueError("nothing to update")
        ev, _ = self.ledger.add_event(dedup_key=f"dash:{uuid.uuid4()}", source="dashboard", kind="item.updated",
                                      occurred_at=self.now(), title=f"{actor or 'Dashboard'} updated '{item['title'][:80]}'",
                                      actor=actor, tickets=[item["ticket"]] if item.get("ticket") else [],
                                      item_ids=[item_id], payload={"changes": changes, "note": note})
        result = self.ledger.apply(item_id, changes, ev, by=actor, note=note, force=True)
        if ticket:
            self.ledger.set_ticket_link(item_id, changes["ticket"], inferred=False)
        if status in ("in_progress", "in_review", "done") and item["status"] == "blocked":
            for d in self.ledger.dependencies(item_id, status="open", kind="external"):
                self.ledger.resolve_dependency(d["id"], ev, f"{actor or 'dashboard'} set status '{status}'")
        item = self.ledger.get_item(item_id)
        text = self.state_line(item, prefix="[status update]")
        if note:
            text += f" Update note{' from ' + actor if actor else ''}: {note}"
        await self._retain([{"content": text, "context": "task_update:dashboard", "timestamp": self.now(),
                             "tags": ["source:task_update", "kind:status_update"] + self._item_tags(item)}],
                           label=f"{item.get('ticket') or item_id} → {item['status']}")
        risk_changes = await self.recompute(trigger=ev)
        return {"item": self.item_view(item), "event": ev, "memory": text, "result": result, "risk_changes": risk_changes}

    async def add_dependency(self, item_id: str, *, description: str, kind: str | None, party: str | None,
                             depends_on: str | None, actor: str | None) -> dict[str, Any]:
        item = self.ledger.get_item(item_id)
        if item is None:
            raise KeyError(item_id)
        ev, _ = self.ledger.add_event(dedup_key=f"dash:{uuid.uuid4()}", source="dashboard", kind="dependency.opened",
                                      occurred_at=self.now(), title=f"Dependency recorded for '{item['title'][:80]}': {description[:120]}",
                                      actor=actor, item_ids=[item_id], tickets=[item["ticket"]] if item.get("ticket") else [])
        target = f"tkt-{_clean_ticket(depends_on)}" if depends_on and _clean_ticket(depends_on) else None
        if target and not self.ledger.get_item(target):
            raise ValueError(f"unknown ticket {depends_on}")
        k = kind or ("item" if target else self._dependency_kind(description)[0])
        dep, _ = self.ledger.add_dependency(item_id=item_id, kind=k, description=description, event=ev,
                                            party=party or (self._party(description, item.get("customer")) if k == "approval" else None),
                                            depends_on_item_id=target)
        await self.recompute(trigger=ev)
        return dep

    async def resolve_dependency(self, dep_id: str, *, note: str | None, actor: str | None) -> dict[str, Any]:
        dep = self.ledger.get_dependency(dep_id)
        if dep is None:
            raise KeyError(dep_id)
        if dep["status"] != "open":
            raise ValueError("dependency is already resolved")
        ev, _ = self.ledger.add_event(dedup_key=f"dash:{uuid.uuid4()}", source="dashboard", kind="dependency.resolve",
                                      occurred_at=self.now(), title=f"{actor or 'Dashboard'} resolved: {dep['description'][:120]}",
                                      actor=actor, item_ids=[dep["item_id"]], payload={"note": note})
        if dep["kind"] == "approval":
            out = await self._grant_approval(dep, ev, note or "approval confirmed in SprintMind", actor)
        else:
            out = self.ledger.resolve_dependency(dep_id, ev, note or f"resolved by {actor or 'dashboard'}")
            self._unblock_if_clear(dep["item_id"], ev, f"dependency resolved by {actor or 'dashboard'}")
        await self.recompute(trigger=ev)
        return out

    async def apply_correction(self, statements: list[dict[str, Any]], *, actor: str | None, text: str) -> list[str | None]:
        """Corrections from people are authoritative ("feedback" source) and go through the same pipeline."""
        return await self.record_extraction(source_type="feedback", title=f"Correction from {actor or 'a team member'}",
                                            document_id=f"correction-{hashlib.sha1((text + iso(self.now())).encode()).hexdigest()[:10]}",
                                            occurred_at=self.now(), items=statements)

    # ------------------------------------------------------------------ GitHub
    def _tickets_for_prs(self, numbers: list[int]) -> list[str]:
        found = []
        for it in self.ledger.items(kind="task"):
            prs = (it["engineering"].get("prs") or {})
            if it.get("ticket") and any(str(n) in prs for n in numbers):
                found.append(it["ticket"])
        return found

    async def process_github(self, event_name: str, payload: dict[str, Any], delivery_id: str | None = None,
                             *, demo: bool = False) -> dict[str, Any]:
        norm = normalise(event_name, payload, self.ticket_re)
        if norm is None:
            return {"status": "ignored", "event": event_name, "action": payload.get("action")}
        tickets = norm.tickets or self._tickets_for_prs(norm.pr_numbers)
        pr = norm.pr_numbers[0] if norm.pr_numbers else None
        ev, created = self.ledger.add_event(
            dedup_key=norm.dedup_key, source="github", kind=norm.kind, occurred_at=norm.occurred_at, title=norm.title,
            actor=norm.actor, tickets=tickets, url=norm.url, source_ref=f"{norm.repo}#{pr}" if pr else norm.repo,
            payload={"delivery_id": delivery_id, "demo": demo, "repo": norm.repo, "pr_numbers": norm.pr_numbers,
                     "branch": norm.branch, "check": norm.check_name, "conclusion": norm.conclusion, "sha": norm.head_sha})
        if not created:
            return {"status": "duplicate", "event": ev}
        touched = [self._apply_engineering(t, norm, ev) for t in tickets]
        for item in touched:
            for c in self.ledger.items(kind="commitment", ticket=item["ticket"]):
                self.ledger.touch(c["id"], ev)
        items_text = "; ".join(f"{i['ticket']} '{i['title']}' now {i['status']}" for i in touched) or "no ticket linked"
        tags = ["source:github", f"kind:{norm.kind.split('.')[1]}"]
        for i in touched:
            tags += self._item_tags(i)
        await self._retain([{"content": f"[engineering event, authoritative] {norm.title} at {iso(norm.occurred_at)[:16]} UTC "
                                        f"(branch {norm.branch or 'n/a'}, repo {norm.repo}). Linked: {items_text}.",
                             "context": "github", "timestamp": norm.occurred_at, "tags": tags}],
                           label=f"github: {norm.title[:50]}")
        risk_changes = await self.recompute(trigger=ev)
        return {"status": "processed", "event": ev, "tickets": tickets, "items": [self.item_view(i["id"]) for i in touched],
                "risk_changes": risk_changes}

    def _apply_engineering(self, ticket: str, norm: EngineeringEvent, ev: dict[str, Any]) -> dict[str, Any]:
        pr_title = re.sub(rf"^\s*{re.escape(ticket)}\s*[:\-]?\s*", "", (norm.title.split(": ", 1) + [""])[1], flags=re.I)
        item, _ = self._ticket_item(ticket, ev, title=pr_title or ticket,
                                    fields={"status": "in_review" if norm.kind == "github.pr_opened" else None})
        eng = item["engineering"]
        prs = eng.setdefault("prs", {})
        at = iso(norm.occurred_at)
        for n in norm.pr_numbers:
            pr = prs.setdefault(str(n), {"number": n})
            if norm.kind.startswith("github.pr_"):
                pr.update(state={"github.pr_opened": "open", "github.pr_merged": "merged", "github.pr_closed": "closed"}[norm.kind],
                          url=norm.url, branch=norm.branch, at=at, event_id=ev["id"], title=norm.title)
        if norm.kind in ("github.ci_failed", "github.ci_passed"):
            eng["ci"] = {"state": "failing" if norm.kind == "github.ci_failed" else "passing", "name": norm.check_name,
                         "pr": norm.pr_numbers[0] if norm.pr_numbers else None, "branch": norm.branch, "at": at,
                         "event_id": ev["id"], "url": norm.url, "ticket": ticket, "conclusion": norm.conclusion}
        self.ledger.set_engineering(item["id"], eng)
        if norm.kind == "github.pr_opened" and item["status"] in ("todo", "in_progress"):
            self.ledger.apply(item["id"], {"status": "in_review"}, ev, by=norm.actor, note=f"PR #{norm.pr_numbers[0]} opened")
        elif norm.kind == "github.pr_merged" and item["status"] != "done":
            self.ledger.apply(item["id"], {"status": "done"}, ev, by=norm.actor, note=f"PR #{norm.pr_numbers[0]} merged")
        else:
            self.ledger.touch(item["id"], ev)
        return self.ledger.get_item(item["id"])

    # ------------------------------------------------------------------- risks
    def _auto_resolve_item_dependencies(self, trigger: dict[str, Any] | None) -> None:
        for d in self.ledger.dependencies(status="open", kind="item"):
            other = self.ledger.get_item(d["depends_on_item_id"])
            if other and other["status"] == "done":
                ev = trigger or self.ledger.get_event((other["field_meta"].get("status") or {}).get("event_id"))
                if ev:
                    self.ledger.resolve_dependency(d["id"], ev, f"{other.get('ticket') or other['title']} is done")
                    self._unblock_if_clear(d["item_id"], ev, f"{other.get('ticket') or other['title']} is done")

    async def recompute(self, trigger: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self._auto_resolve_item_dependencies(trigger)
        now = self.now()
        findings = RiskEngine(self.ledger, now, self.tz).evaluate()
        changes = self.ledger.sync_risks(findings, now, trigger)
        memories = []
        for c in changes:
            item = self.ledger.get_item(c["item_id"]) or {"title": c["item_id"], "id": c["item_id"]}
            self.ledger.add_event(dedup_key=f"risk:{c['risk_id']}:{c['change']}:{iso(now)}", source="system",
                                  kind=f"risk.{c['change']}", occurred_at=now, title=f"Risk {c['change']}: {c['title']}",
                                  tickets=[item["ticket"]] if item.get("ticket") else [], item_ids=[c["item_id"]],
                                  payload={"risk_id": c["risk_id"], "rule": c["rule"], "severity": c["severity"],
                                           "explanation": c["explanation"], "trigger": trigger["id"] if trigger else None})
            if c["kind"] == "risk":
                memories.append({"content": f"[delivery risk {c['change']}] {c['severity'].upper()}: {c['title']} for "
                                            f"'{item['title']}'. {c['explanation']}"
                                            + (f" Recommended: {c['recommended_action']}" if c.get("recommended_action") else ""),
                                 "context": "risk", "timestamp": now,
                                 "tags": ["source:risk", f"kind:risk_{c['change']}"] + self._item_tags(item)})
        await self._retain(memories, label=f"{len(memories)} risk change(s)")
        return changes

    async def explain_risk(self, risk_id: str, *, force: bool = False) -> dict[str, Any]:
        risk = self.ledger.get_risk(risk_id)
        if risk is None:
            raise KeyError(risk_id)
        item = self.ledger.get_item(risk["item_id"]) or {}
        if risk.get("llm_explanation") and not force:
            return {"risk_id": risk_id, "explanation": risk["llm_explanation"], "cached": True}
        if self.llm is None:
            raise RuntimeError("LLM is not configured (set GROQ_API_KEY); the rule-based explanation is still shown")
        evidence = [f"[E{i}] ({e['at'][:16]}, {e['source']}) {e['title']} (why it matters: {e['why']})"
                    for i, e in enumerate(risk["evidence"], 1)]
        tags = [f"ticket:{item['ticket']}"] if item.get("ticket") else None
        mems = await self.memory.recall(f"{item.get('title', '')} {item.get('ticket') or ''} status blockers deadline",
                                        tags=tags, tags_match="any", budget="low", label=f"explain:{risk['rule']}")
        mem_lines = [f"[M{i}] ({(m.get('occurred_start') or '')[:10]}) {m['text'][:400]}" for i, m in enumerate(mems[:8], 1)]
        user = (f"Risk ({risk['severity']}): {risk['title']}\nRule: {risk['rule']}\nRule-based explanation: {risk['explanation']}\n"
                f"Recommended action: {risk['recommended_action']}\nItem: {self.state_line(item) if item else ''}\n\n"
                f"Evidence:\n" + "\n".join(evidence) + "\n\nRelated memories from Hindsight:\n" + ("\n".join(mem_lines) or "(none)"))
        text = await self.llm.complete(EXPLAIN_SYSTEM, user, label=f"explain risk {risk['rule']}")
        valid = {f"E{i}" for i in range(1, len(evidence) + 1)} | {f"M{i}" for i in range(1, len(mem_lines) + 1)}
        text = re.sub(r"\[\s*([EM]\d+)\s*\]", lambda m: f"[{m.group(1)}]" if m.group(1) in valid else "", text)
        self.ledger.set_risk_explanation(risk_id, text)
        return {"risk_id": risk_id, "explanation": text, "cached": False, "memories": mems[:8]}

    # -------------------------------------------------------------------- views
    def ticket_url(self, ticket: str | None) -> str | None:
        return self.ticket_url_template.format(ticket=ticket) if ticket and self.ticket_url_template else None

    def _source(self, event: dict[str, Any] | None) -> dict[str, Any] | None:
        if not event:
            return None
        return {"event_id": event["id"], "source": event["source"], "kind": event["kind"], "title": event["title"],
                "at": event["occurred_at"], "url": event["url"], "source_ref": event["source_ref"],
                "document_title": event["payload"].get("document_title")}

    def item_view(self, item: dict[str, Any] | str, *, full: bool = True,
                  risks_by_item: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
        if isinstance(item, str):
            item = self.ledger.get_item(item)
            if item is None:
                raise KeyError(item)
        src = self.ledger.get_event(item["source_event_id"])
        open_risks = (risks_by_item.get(item["id"], []) if risks_by_item is not None
                      else self.ledger.risks(status="open", item_id=item["id"]))
        view = {k: v for k, v in item.items() if k != "field_meta"}
        view.update(
            customer_name=self.customer_name(item.get("customer")), source=self._source(src),
            meeting_title=(src or {}).get("payload", {}).get("document_title") or (src or {}).get("title"),
            meeting_at=(src or {}).get("occurred_at"), ticket_url=self.ticket_url(item.get("ticket")),
            evidence={f: m for f, m in item["field_meta"].items()},
            risks=[{"id": r["id"], "severity": r["severity"], "title": r["title"], "kind": r["kind"], "rule": r["rule"]}
                   for r in sorted(open_risks, key=lambda r: SEVERITY_RANK[r["severity"]])],
            open_dependencies=len(self.ledger.dependencies(item["id"], status="open")),
        )
        if full:
            view.update(history=self.ledger.history(item["id"]), dependencies=self.ledger.dependencies(item["id"]),
                        conflicts=self.ledger.conflicts(item["id"], status=None),
                        events=[self._source(e) for e in self.ledger.events(item_id=item["id"], limit=30)])
        return view

    def list_items(self, *, status: str | None = None, owner: str | None = None, stage: str | None = None,
                   meeting_id: str | None = None, kind: str | None = None, ticket: str | None = None) -> list[dict[str, Any]]:
        risks: dict[str, list[dict[str, Any]]] = {}
        for r in self.ledger.risks(status="open"):
            risks.setdefault(r["item_id"], []).append(r)
        out = []
        for it in self.ledger.items(kind=kind, ticket=_clean_ticket(ticket) if ticket else None):
            if (status and it["status"] != status) or (owner and it.get("owner_id") != owner) or \
                    (stage and it.get("scrum_stage") != stage) or (meeting_id and it.get("meeting_id") != meeting_id):
                continue
            out.append(self.item_view(it, full=False, risks_by_item=risks))
        return out

    def state_line(self, item: dict[str, Any], prefix: str = "CURRENT STATE") -> str:
        meta = item.get("field_meta") or {}
        kind = "customer commitment" if item["kind"] == "commitment" else "ticket" if item["id"].startswith("tkt-") else item["kind"].replace("_", " ")
        st = meta.get("status") or {}
        parts = [f"{prefix}: {kind} {item.get('ticket') or ''} \"{item['title']}\"".replace("  ", " "),
                 f"status {item['status']}" + (f" (set by {st['source']} on {st['at'][:10]})" if st else ""),
                 f"owner {item.get('owner') or 'not recorded'}", f"due {item.get('due') or 'not recorded'}"]
        if item.get("customer"):
            parts.append(f"customer {self.customer_name(item['customer'])}")
        if item.get("ticket") and item.get("ticket_inferred"):
            parts.append(f"linked to {item['ticket']} by keyword inference")
        for h in self.ledger.history(item["id"], fields=("due", "owner", "status"), limit=12):
            if h["applied"] and h["old"] not in (None, "None") and h["field"] in ("due", "owner"):
                parts.append(f"{h['field']} changed from {h['old']} to {h['new']} on {h['at'][:10]} ({h['source']})")
        deps = self.ledger.dependencies(item["id"])
        for d in deps:
            parts.append(f"dependency ({d['kind']}) '{d['description'][:120]}' "
                         + (f"OPEN since {d['opened_at'][:10]}" if d["status"] == "open" else f"resolved {str(d['resolved_at'])[:10]}: {d['resolution'][:120]}"))
        eng = item.get("engineering") or {}
        for n, pr in (eng.get("prs") or {}).items():
            if pr.get("state"):
                parts.append(f"PR #{n} {pr['state']} ({str(pr.get('at'))[:10]}, GitHub)")
        if eng.get("ci"):
            parts.append(f"CI '{eng['ci'].get('name')}' {eng['ci']['state']} ({eng['ci']['at'][:16]}, GitHub)")
        for r in self.ledger.risks(status="open", item_id=item["id"]):
            parts.append(f"open {r['kind'].replace('_', ' ')} ({r['severity']}): {r['title']}")
        stale = [h for h in self.ledger.history(item["id"], limit=40) if not h["applied"]][:2]
        for h in stale:
            parts.append(f"not applied: {h['source']} on {h['at'][:10]} said {h['field']}='{h['new']}' ({h['note']})")
        return "; ".join(parts) + "."

    def _relevant_items(self, question: str, person_id: str | None, limit: int = 10) -> list[dict[str, Any]]:
        items = self.ledger.items()
        chosen: dict[str, dict[str, Any]] = {}
        for t in find_tickets(self.ticket_re, question):
            for it in items:
                if it.get("ticket") == t:
                    chosen[it["id"]] = it
        q = question.lower()
        for c in self.team.customers:
            if any(a.lower() in q for a in [c["name"], *c.get("aliases", [])]):
                for it in items:
                    if it.get("customer") == c["id"] and it["status"] in OPEN_STATUSES:
                        chosen.setdefault(it["id"], it)
        if person_id:
            for it in items:
                if it.get("owner_id") == person_id and it["status"] in OPEN_STATUSES:
                    chosen.setdefault(it["id"], it)
        if re.search(r"\b(risk|block|behind|late|slip|approval|at risk|overdue)", q):
            for r in self.ledger.risks(status="open", kind="risk"):
                it = self.ledger.get_item(r["item_id"])
                if it:
                    chosen.setdefault(it["id"], it)
        scored = sorted(((len(keywords(question) & keywords(f"{it['title']} {it.get('detail') or ''}")), it)
                         for it in items if it["id"] not in chosen), key=lambda x: -x[0])
        for score, it in scored[:6]:
            if score >= 1 and len(chosen) < limit:
                chosen[it["id"]] = it
        return list(chosen.values())[:limit]

    def state_facts(self, question: str, person_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        """Latest verified state for the items a question is about, in the same shape as recalled memories."""
        out = []
        for it in self._relevant_items(question, person_id, limit):
            st = it["field_meta"].get("status") or {}
            out.append({"id": f"state:{it['id']}", "text": self.state_line(it), "type": "current_state",
                        "context": "ledger", "tags": self._item_tags(it) + [f"status:{it['status']}"],
                        "metadata": {"item_id": it["id"]}, "entities": [], "occurred_start": iso(self.now()),
                        "mentioned_at": st.get("at"), "document_id": None, "score": None})
        return out

    # --------------------------------------------------------------- dashboard
    def dashboard(self) -> dict[str, Any]:
        now = self.now()
        today = now.astimezone(ZoneInfo(self.tz)).date()
        items = {i["id"]: i for i in self.ledger.items()}
        open_risks = self.ledger.risks(status="open")
        risks_by_item: dict[str, list[dict[str, Any]]] = {}
        for r in open_risks:
            risks_by_item.setdefault(r["item_id"], []).append(r)

        def risk_view(r: dict[str, Any]) -> dict[str, Any]:
            it = items.get(r["item_id"]) or {}
            return {**r, "item": {k: it.get(k) for k in ("id", "kind", "title", "ticket", "owner", "owner_id", "due", "status",
                                                          "customer", "ticket_inferred")}
                    | {"customer_name": self.customer_name(it.get("customer")), "ticket_url": self.ticket_url(it.get("ticket"))}}

        real = sorted((r for r in open_risks if r["kind"] == "risk"),
                      key=lambda r: (SEVERITY_RANK[r["severity"]], (items.get(r["item_id"]) or {}).get("due") or "9999"))
        commitments = [self.item_view(i, full=False, risks_by_item=risks_by_item) for i in items.values()
                       if i["kind"] == "commitment" and i["status"] in OPEN_STATUSES]
        commitments.sort(key=lambda c: (min([SEVERITY_RANK[r["severity"]] for r in c["risks"] if r["kind"] == "risk"] or [9]),
                                        c.get("due") or "9999"))
        deps = self.ledger.dependencies(status="open")
        dep_views = []
        for d in deps:
            it = items.get(d["item_id"]) or {}
            dep_views.append(d | {"item": {k: it.get(k) for k in ("id", "title", "ticket", "owner", "due", "status")},
                                  "opened_event": self._source(self.ledger.get_event(d["opened_event_id"])),
                                  "age_business_days": business_days_between(parse_dt(d["opened_at"]).date(), today)})
        blocked = [self.item_view(i, full=False, risks_by_item=risks_by_item) for i in items.values() if i["status"] == "blocked"]
        approvals_granted = self.ledger.events(kind_prefix="approval.", since=now - timedelta(days=21), limit=20)
        decisions = self.ledger.events(kind_prefix="statement.decision", since=now - timedelta(days=21), limit=15)
        engineering = self.ledger.events(source="github", limit=25)
        risk_events = self.ledger.events(kind_prefix="risk.", limit=40)
        hist = [h | {"item": {k: (items.get(h["item_id"]) or {}).get(k) for k in ("id", "title", "ticket", "kind")}}
                for h in self.ledger.history(fields=("due", "owner", "status"), limit=120)
                if h["old"] not in (None, "None") or not h["applied"]]
        workload = {}
        for m in self.team.members:
            mine = [i for i in items.values() if i.get("owner_id") == m["id"] and i["status"] in OPEN_STATUSES]
            due_soon = [i for i in mine if i.get("due") and business_days_between(today, parse_dt(i["due"] + "T12:00:00").date()) <= 3]
            workload[m["id"]] = {"person": m["name"], "person_id": m["id"], "open": len(mine),
                                 "blocked": sum(1 for i in mine if i["status"] == "blocked"),
                                 "in_review": sum(1 for i in mine if i["status"] == "in_review"),
                                 "due_soon": len(due_soon), "commitments": sum(1 for i in mine if i["kind"] == "commitment"),
                                 "high_risks": sum(1 for i in mine for r in risks_by_item.get(i["id"], [])
                                                   if r["kind"] == "risk" and r["severity"] == "high")}
        unassigned = [i for i in items.values() if not i.get("owner") and i["status"] in OPEN_STATUSES]
        by_sev = {s: sum(1 for r in real if r["severity"] == s) for s in ("high", "medium", "low")}
        return {
            "generated_at": iso(now),
            "summary": {"open_risks": len(real), "by_severity": by_sev,
                        "commitments_open": len(commitments),
                        "commitments_at_risk": sum(1 for c in commitments if any(r["kind"] == "risk" for r in c["risks"])),
                        "pending_approvals": sum(1 for d in deps if d["kind"] == "approval"),
                        "open_dependencies": len(deps), "blocked_items": len(blocked),
                        "ci_failing": sum(1 for i in items.values() if (i["engineering"].get("ci") or {}).get("state") == "failing"),
                        "missing_info": sum(1 for r in open_risks if r["kind"] == "missing_info")},
            "risks": [risk_view(r) for r in real],
            "missing_info": [risk_view(r) for r in open_risks if r["kind"] == "missing_info"],
            "commitments": commitments,
            "dependencies": dep_views,
            "blocked": blocked,
            "approvals": {"pending": [d for d in dep_views if d["kind"] == "approval"],
                          "granted": [self._source(e) for e in approvals_granted]},
            "decisions": [self._source(e) for e in decisions],
            "engineering": [self._source(e) | {"tickets": e["tickets"], "payload": {k: e["payload"].get(k) for k in ("branch", "check", "conclusion", "pr_numbers", "demo")}}
                            for e in engineering],
            "unlinked_engineering": [self._source(e) for e in engineering if not e["tickets"]],
            "risk_history": [self._source(e) | {"payload": e["payload"]} for e in risk_events],
            "change_history": hist[:60],
            "conflicts": self.ledger.conflicts(),
            "workload": sorted(workload.values(), key=lambda w: (-w["open"], w["person"])),
            "unassigned": [self.item_view(i, full=False, risks_by_item=risks_by_item) for i in unassigned][:20],
        }
