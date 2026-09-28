"""Deterministic delivery-risk rules over the ledger.

Every finding is derived from recorded facts only: an item's due date, status, open dependencies,
GitHub CI state, last recorded activity and open source conflicts. Each finding carries its evidence
(the ledger events that established those facts), a plain explanation and a recommended next step.
There are no scores: severity is one of high / medium / low, and gaps in the record (no owner, no
due date) are reported separately as kind="missing_info" with severity "info", never as risks.

The LLM is only used afterwards, to explain a finding from its evidence (see DeliveryService.explain_risk).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .ledger import Ledger, OPEN_STATUSES, parse_dt

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2, "info": 3}
RULES = {
    "overdue": "Due date has passed and the item is not done",
    "ci_failing": "The latest CI run on the linked work failed",
    "pending_approval": "Waiting on an approval that has not been recorded",
    "unresolved_blocker": "Blocked, or has an open dependency",
    "dependency_incomplete": "Depends on another item that is not done",
    "deadline_at_risk": "Due within 2 business days without recent progress",
    "stale_progress": "No recorded progress for 3+ business days with a due date approaching",
    "conflicting_evidence": "Sources disagree about this item",
    "missing_owner": "No owner recorded",
    "missing_due": "No due date recorded",
    "missing_blocker_reason": "Marked blocked, but what it waits on is not recorded",
}


def business_days_between(start: date, end: date) -> int:
    """Signed number of business days from start to end (0 on the same day)."""
    if start == end:
        return 0
    sign, a, b = (1, start, end) if end > start else (-1, end, start)
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return sign * n


def _due(item: dict[str, Any]) -> date | None:
    try:
        return date.fromisoformat(item["due"]) if item.get("due") else None
    except ValueError:
        return None


class RiskEngine:
    def __init__(self, ledger: Ledger, now: datetime, tz: str = "Asia/Kolkata") -> None:
        self.ledger = ledger
        self.now = now
        self.today = now.astimezone(ZoneInfo(tz)).date()
        self.tz = ZoneInfo(tz)

    # --------------------------------------------------------------- evidence
    def _evidence(self, event_id: str | None, why: str) -> dict[str, Any] | None:
        e = self.ledger.get_event(event_id)
        if e is None:
            return None
        return {"event_id": e["id"], "why": why, "source": e["source"], "kind": e["kind"], "at": e["occurred_at"],
                "title": e["title"], "url": e["url"], "source_ref": e["source_ref"]}

    def _field_evidence(self, item: dict[str, Any], field: str, why: str) -> dict[str, Any] | None:
        m = item["field_meta"].get(field)
        return self._evidence(m["event_id"], why) if m else None

    def _bdays_since(self, iso_ts: str | None) -> int | None:
        dt = parse_dt(iso_ts)
        return business_days_between(dt.astimezone(self.tz).date(), self.today) if dt else None

    @staticmethod
    def _finding(item: dict[str, Any], rule: str, severity: str, title: str, explanation: str, action: str,
                 evidence: list[dict[str, Any] | None], sub: str = "", kind: str = "risk") -> dict[str, Any]:
        return {"key": f"{item['id']}:{rule}{':' + sub if sub else ''}", "item_id": item["id"], "rule": rule,
                "kind": kind, "severity": severity, "title": title, "explanation": explanation,
                "recommended_action": action, "evidence": [e for e in evidence if e]}

    # ------------------------------------------------------------------ rules
    def evaluate(self) -> list[dict[str, Any]]:
        items = self.ledger.items()
        by_id = {i["id"]: i for i in items}
        tickets = {i["ticket"]: i for i in items if i["ticket"] and i["id"] == f"tkt-{i['ticket']}"}
        commitment_tickets = {i["ticket"] for i in items
                              if i["kind"] == "commitment" and i["ticket"] and i["status"] in OPEN_STATUSES}
        out: list[dict[str, Any]] = []
        for item in items:
            if item["status"] not in OPEN_STATUSES:
                continue
            out += self._rules_for(item, by_id, tickets, commitment_tickets)
        out.sort(key=lambda f: (SEVERITY_RANK[f["severity"]], f["item_id"]))
        return out

    def _rules_for(self, item: dict[str, Any], by_id: dict[str, Any], tickets: dict[str, Any],
                   commitment_tickets: set[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        due = _due(item)
        left = business_days_between(self.today, due) if due else None
        who = item.get("owner") or "the owner"
        label = f"{item['ticket']} " if item.get("ticket") else ""
        is_ticket_item = item["id"] == f"tkt-{item.get('ticket')}"
        linked = tickets.get(item["ticket"]) if item.get("ticket") and not is_ticket_item else None
        # a ticket that backs an open customer commitment reports CI / approvals / blockers through the commitment
        rolled_up = is_ticket_item and item["ticket"] in commitment_tickets
        eng = (linked or item)["engineering"] if linked else item["engineering"]
        deps = [] if rolled_up else self.ledger.dependencies(item["id"], status="open")
        if linked:
            deps += [d | {"via": linked["id"]} for d in self.ledger.dependencies(linked["id"], status="open")]
        due_ev = self._field_evidence(item, "due", f"due date {item.get('due')}")
        status_ev = self._field_evidence(item, "status", f"status '{item['status']}'")

        # overdue
        if due and left is not None and left < 0:
            action = (f"Agree a new date with {item['customer']} and record it; owner: {who}" if item.get("customer")
                      else f"Confirm with {who} what is left and re-plan the due date")
            out.append(self._finding(item, "overdue", "high", f"{label}Past due by {-left} business day(s)",
                                     f"'{item['title']}' was due {item['due']} and its latest recorded status is '{item['status']}'.",
                                     action, [due_ev, status_ev]))

        # failing CI (attach to the commitment when one exists, so one CI failure = one alert)
        ci = eng.get("ci") or {}
        if ci.get("state") == "failing" and not rolled_up:
            sev = "high" if (left is not None and left <= 3) else "medium"
            where = f" on PR #{ci['pr']}" if ci.get("pr") else (f" on {ci['branch']}" if ci.get("branch") else "")
            expl = (f"CI check '{ci.get('name', 'ci')}' failed{where} for {ci.get('ticket') or item.get('ticket')} "
                    f"at {ci.get('at', '')[:16].replace('T', ' ')} UTC")
            if due:
                expl += f"; '{item['title']}' is due {item['due']} ({left} business day(s) left)"
            out.append(self._finding(item, "ci_failing", sev, f"{label}CI failing on linked work", expl + ".",
                                     f"Ask {who} to fix '{ci.get('name', 'the failing check')}' and re-run CI"
                                     + (f" before {item['due']}" if due else ""),
                                     [self._evidence(ci.get("event_id"), "failed CI run"), due_ev]))

        # approvals and other dependencies
        for d in deps:
            ev_open = self._evidence(d["opened_event_id"], "dependency recorded")
            if d["kind"] == "approval":
                sev = "high" if (left is not None and left <= 2) else "medium"
                party = d.get("party") or "the approver"
                out.append(self._finding(
                    item, "pending_approval", sev, f"{label}Waiting on approval from {party}",
                    f"Open since {d['opened_at'][:10]}: {d['description']}. No approval has been recorded yet."
                    + (f" '{item['title']}' is due {item['due']}." if due else ""),
                    f"Chase {party} for a written approval and record it in SprintMind", [ev_open, due_ev], sub=d["id"]))
            elif d["kind"] == "item" and d.get("depends_on_item_id"):
                other = by_id.get(d["depends_on_item_id"])
                if other and other["status"] not in ("done", "dropped"):
                    sev = "high" if (left is not None and left <= 3) else "medium"
                    out.append(self._finding(
                        item, "dependency_incomplete", sev, f"{label}Depends on unfinished {other.get('ticket') or other['title']}",
                        f"'{item['title']}' depends on '{other['title']}', which is '{other['status']}'"
                        + (f" (owner {other['owner']})" if other.get("owner") else "") + ".",
                        f"Agree a date for '{other['title']}' with {other.get('owner') or 'its owner'}",
                        [ev_open, self._field_evidence(other, "status", "dependency status")], sub=d["id"]))

        non_approval_open = [d for d in deps if d["kind"] == "external"]
        linked_blocked = bool(linked and linked["status"] == "blocked")
        if not rolled_up and (item["status"] == "blocked" or non_approval_open or linked_blocked):
            if (item["status"] == "blocked" or linked_blocked) and deps and all(d["kind"] in ("approval", "item") for d in deps):
                pass  # covered by pending_approval / dependency_incomplete
            elif item["status"] == "blocked" and not deps and not item.get("blocked_on"):
                out.append(self._finding(item, "missing_blocker_reason", "info", f"{label}Blocked, reason not recorded",
                                         f"'{item['title']}' is marked blocked but nothing records what it waits on.",
                                         f"Ask {who} what would unblock it", [status_ev], kind="missing_info"))
            else:
                since = self._bdays_since((item["field_meta"].get("status") or {}).get("at")) if item["status"] == "blocked" else None
                sev = "high" if ((since or 0) >= 3 or (left is not None and left <= 3)) else "medium"
                waiting = ("; ".join(d["description"] for d in non_approval_open) or item.get("blocked_on")
                           or (linked or {}).get("blocked_on") or "unspecified")
                if linked_blocked:
                    waiting += f" (via linked ticket {linked['ticket']})"
                out.append(self._finding(
                    item, "unresolved_blocker", sev, f"{label}Unresolved blocker",
                    f"Waiting on: {waiting}." + (f" Blocked for {since} business day(s)." if since else "")
                    + (f" Due {item['due']}." if due else ""),
                    f"Escalate the blocker with {who} and whoever can unblock it",
                    [status_ev] + [self._evidence(d["opened_event_id"], "blocker recorded") for d in non_approval_open]))

        idle = self._bdays_since(item["last_activity_at"])
        # deadline pressure without progress
        if due and left is not None and 0 <= left <= 2:
            reasons, evidence = [], [due_ev]
            if item["status"] == "todo" and not (linked and linked["status"] in ("in_progress", "in_review", "done")):
                reasons.append("work has not started")
                evidence.append(status_ev)
            if idle is not None and idle >= 2:
                reasons.append(f"no recorded progress for {idle} business days")
            if reasons:
                out.append(self._finding(
                    item, "deadline_at_risk", "high" if left <= 1 else "medium",
                    f"{label}Due in {left} business day(s)", f"'{item['title']}' is due {item['due']} and "
                    + " and ".join(reasons) + ".", f"Confirm with {who} today whether {item['due']} still holds",
                    evidence))
        elif due and left is not None and 2 < left <= 7 and idle is not None and idle >= 3:
            out.append(self._finding(
                item, "stale_progress", "medium", f"{label}No progress recorded for {idle} business days",
                f"The last recorded activity on '{item['title']}' was {idle} business days ago; it is due {item['due']}.",
                f"Get a status update from {who}", [due_ev, status_ev]))

        # disagreeing sources: one finding per field, describing the latest claim
        by_field: dict[str, list[dict[str, Any]]] = {}
        for c in self.ledger.conflicts(item["id"]):
            by_field.setdefault(c["field"], []).append(c)
        for field, cs in by_field.items():
            c = max(cs, key=lambda x: x["claimed_at"])
            more = f" ({len(cs)} conflicting statements in total)" if len(cs) > 1 else ""
            out.append(self._finding(
                item, "conflicting_evidence", "medium", f"{label}Sources disagree on {field}",
                f"{c['claimed_source']} ({c['claimed_at'][:10]}) says {field} = '{c['claimed_value']}', but the current "
                f"value '{c['current_value']}' comes from {c['current_source']} ({(c['current_at'] or '')[:10]}), which is "
                f"verified, so SprintMind kept it{more}.",
                f"Confirm the correct {field} with {who} and record it",
                [self._evidence(c["claimed_event_id"], "conflicting claim"), self._evidence(c["current_event_id"], "current value")],
                sub=field))

        # gaps in the record are reported, not guessed
        if item["kind"] == "commitment":
            if not item.get("owner"):
                out.append(self._finding(item, "missing_owner", "info", "Commitment has no owner",
                                         f"No owner is recorded for '{item['title']}'.", "Assign an owner",
                                         [self._evidence(item["source_event_id"], "commitment recorded")], kind="missing_info"))
            if not due:
                out.append(self._finding(item, "missing_due", "info", "Commitment has no due date",
                                         f"No due date is recorded for '{item['title']}'.", "Record the agreed date",
                                         [self._evidence(item["source_event_id"], "commitment recorded")], kind="missing_info"))
        return out
