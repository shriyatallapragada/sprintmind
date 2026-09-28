"""Delivery ledger: the unified, append-only record of what happened and the latest verified state.

Everything SprintMind learns arrives here as an *event*: a meeting statement, a standup line, a Jira
digest entry, a dashboard edit, a GitHub PR / CI event, a customer approval, a correction, a risk
transition. Events are deduplicated by a deterministic key, timestamped with when they happened, and
never deleted. They update *items* (tickets, customer commitments, meeting actions) field by field
using source-aware rules, and every proposed change, including the ones that were not applied
because they were stale or less authoritative, is kept in item_history.

Hindsight holds the narrative memory (what was said, by whom, when). The ledger holds the current
operational state and the evidence trail that ties both together (events carry the Hindsight
document id they were retained under).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

ITEM_STATUSES = ("todo", "in_progress", "in_review", "blocked", "done", "dropped")
OPEN_STATUSES = {"todo", "in_progress", "in_review", "blocked"}
ITEM_KINDS = ("task", "commitment", "follow_up", "blocker", "risk")
# Higher = more authoritative about *current* operational state. A meeting statement is evidence of
# intent; a merged PR or a failed CI run is evidence of fact.
SOURCE_AUTHORITY = {
    "github": 5,
    "approval": 4, "dashboard": 4, "feedback": 4,
    "task_update": 3,
    "standup": 2,
    "meeting": 1, "retro": 1, "incident": 1, "note": 1, "sop": 1, "demo": 1, "system": 1,
    "interaction": 0,
}
# Values from verified sources (GitHub, approvals, dashboard edits, corrections) are protected: a less
# authoritative statement made within CONFLICT_WINDOW cannot overwrite them and becomes a conflict instead.
# Between reported sources (meetings, standups, Jira digests) the newest statement wins.
VERIFIED_AUTHORITY = 4
CONFLICT_WINDOW = timedelta(days=3)
TRACKED_FIELDS = ("status", "owner", "due", "priority", "blocked_on", "ticket", "customer")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  dedup_key TEXT UNIQUE NOT NULL,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  received_at TEXT NOT NULL,
  actor TEXT,
  title TEXT NOT NULL,
  tickets TEXT NOT NULL DEFAULT '[]',
  item_ids TEXT NOT NULL DEFAULT '[]',
  source_ref TEXT,
  url TEXT,
  memory_document_id TEXT,
  payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_occurred ON events(occurred_at);
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  detail TEXT,
  ticket TEXT,
  ticket_inferred INTEGER NOT NULL DEFAULT 0,
  customer TEXT,
  owner_id TEXT,
  owner TEXT,
  due TEXT,
  status TEXT NOT NULL,
  priority TEXT,
  scrum_stage TEXT,
  blocked_on TEXT,
  meeting_id TEXT,
  source_event_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_activity_at TEXT NOT NULL,
  engineering TEXT NOT NULL DEFAULT '{}',
  field_meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS item_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT NOT NULL,
  field TEXT NOT NULL,
  old TEXT,
  new TEXT,
  at TEXT NOT NULL,
  event_id TEXT NOT NULL,
  source TEXT NOT NULL,
  by TEXT,
  note TEXT,
  applied INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS dependencies (
  id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  description TEXT NOT NULL,
  party TEXT,
  depends_on_item_id TEXT,
  status TEXT NOT NULL,
  opened_event_id TEXT NOT NULL,
  opened_at TEXT NOT NULL,
  resolved_event_id TEXT,
  resolved_at TEXT,
  resolution TEXT
);
CREATE TABLE IF NOT EXISTS conflicts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT NOT NULL,
  field TEXT NOT NULL,
  current_value TEXT,
  current_source TEXT,
  current_event_id TEXT,
  current_at TEXT,
  claimed_value TEXT,
  claimed_source TEXT NOT NULL,
  claimed_event_id TEXT NOT NULL,
  claimed_at TEXT NOT NULL,
  status TEXT NOT NULL,
  resolution TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS risks (
  id TEXT PRIMARY KEY,
  key TEXT NOT NULL,
  item_id TEXT NOT NULL,
  rule TEXT NOT NULL,
  kind TEXT NOT NULL,
  severity TEXT NOT NULL,
  status TEXT NOT NULL,
  title TEXT NOT NULL,
  explanation TEXT NOT NULL,
  recommended_action TEXT NOT NULL,
  evidence TEXT NOT NULL,
  first_detected_at TEXT NOT NULL,
  last_changed_at TEXT NOT NULL,
  resolved_at TEXT,
  resolved_reason TEXT,
  llm_explanation TEXT,
  history TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS risks_key ON risks(key, status);
"""

_STOP = {"with", "from", "that", "this", "their", "have", "will", "into", "about", "after", "before", "should",
         "would", "could", "team", "task", "work", "make", "need", "needs", "also", "they", "them", "then", "than",
         "when", "what", "which", "where", "done", "todo", "update", "status", "customer", "ticket", "sprint"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def parse_dt(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def keywords(text: str | None) -> set[str]:
    """Crude stems ("limits", "limiting" -> "limit") for deterministic, explainable matching."""
    return {w[:5] for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) >= 4 and w not in _STOP}


def similarity(a: str | None, b: str | None) -> float:
    ka, kb = keywords(a), keywords(b)
    return len(ka & kb) / len(ka | kb) if ka and kb else 0.0


def authority(source: str | None) -> int:
    return SOURCE_AUTHORITY.get(source or "", 1)


def _j(v: Any) -> str:
    return json.dumps(v, default=str)


class Ledger:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL" if path != ":memory:" else "PRAGMA journal_mode=MEMORY")
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            try:
                yield self.db
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    def close(self) -> None:
        self.db.close()

    def reset(self) -> None:
        with self.tx() as db:
            for t in ("events", "items", "item_history", "dependencies", "conflicts", "risks"):
                db.execute(f"DELETE FROM {t}")

    # ------------------------------------------------------------------ events
    @staticmethod
    def _event(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        e = dict(row)
        for k in ("tickets", "item_ids", "payload"):
            e[k] = json.loads(e[k])
        return e

    def add_event(self, *, dedup_key: str, source: str, kind: str, occurred_at: datetime | str, title: str,
                  actor: str | None = None, tickets: list[str] | None = None, item_ids: list[str] | None = None,
                  source_ref: str | None = None, url: str | None = None, memory_document_id: str | None = None,
                  payload: dict[str, Any] | None = None) -> tuple[dict[str, Any], bool]:
        """Insert unless an event with the same dedup_key exists. Returns (event, created)."""
        with self.tx() as db:
            existing = db.execute("SELECT * FROM events WHERE dedup_key = ?", (dedup_key,)).fetchone()
            if existing:
                return self._event(existing), False
            eid = "evt_" + hashlib.sha1(dedup_key.encode()).hexdigest()[:16]
            occurred = iso(parse_dt(occurred_at))
            db.execute(
                "INSERT INTO events (id, dedup_key, source, kind, occurred_at, received_at, actor, title, tickets, item_ids,"
                " source_ref, url, memory_document_id, payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (eid, dedup_key, source, kind, occurred, iso(utcnow()), actor, title[:500],
                 _j(sorted(set(tickets or []))), _j(list(dict.fromkeys(item_ids or []))), source_ref, url,
                 memory_document_id, _j(payload or {})))
            return self.get_event(eid), True

    def get_event(self, event_id: str | None) -> dict[str, Any] | None:
        if not event_id:
            return None
        return self._event(self.db.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone())

    def link_event(self, event_id: str, item_ids: list[str]) -> None:
        with self.tx() as db:
            row = db.execute("SELECT item_ids FROM events WHERE id = ?", (event_id,)).fetchone()
            if row is None:
                return
            ids = list(dict.fromkeys(json.loads(row["item_ids"]) + [i for i in item_ids if i]))
            db.execute("UPDATE events SET item_ids = ? WHERE id = ?", (_j(ids), event_id))

    def events(self, *, source: str | None = None, kind_prefix: str | None = None, item_id: str | None = None,
               since: datetime | None = None, limit: int = 50) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM events WHERE 1=1", []
        if source:
            sql += " AND source = ?"; args.append(source)
        if kind_prefix:
            sql += " AND kind LIKE ?"; args.append(kind_prefix + "%")
        if item_id:
            sql += " AND item_ids LIKE ?"; args.append(f'%"{item_id}"%')
        if since:
            sql += " AND occurred_at >= ?"; args.append(iso(since))
        sql += " ORDER BY occurred_at DESC, received_at DESC LIMIT ?"
        args.append(limit)
        return [self._event(r) for r in self.db.execute(sql, args).fetchall()]

    # ------------------------------------------------------------------- items
    @staticmethod
    def _item(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        it = dict(row)
        it["engineering"] = json.loads(it["engineering"])
        it["field_meta"] = json.loads(it["field_meta"])
        it["ticket_inferred"] = bool(it["ticket_inferred"])
        return it

    def get_item(self, item_id: str | None) -> dict[str, Any] | None:
        if not item_id:
            return None
        return self._item(self.db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())

    def items(self, *, kind: str | None = None, ticket: str | None = None, owner_id: str | None = None,
              customer: str | None = None, open_only: bool = False) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM items WHERE 1=1", []
        for col, val in (("kind", kind), ("ticket", ticket), ("owner_id", owner_id), ("customer", customer)):
            if val is not None:
                sql += f" AND {col} = ?"; args.append(val)
        if open_only:
            sql += " AND status IN ('todo','in_progress','in_review','blocked')"
        sql += " ORDER BY created_at"
        return [self._item(r) for r in self.db.execute(sql, args).fetchall()]

    def create_item(self, *, item_id: str, kind: str, title: str, event: dict[str, Any], status: str = "todo",
                    by: str | None = None, **fields: Any) -> dict[str, Any]:
        at = event["occurred_at"]
        meta = {f: {"at": at, "source": event["source"], "event_id": event["id"]}
                for f in TRACKED_FIELDS if fields.get(f) is not None}
        meta["status"] = {"at": at, "source": event["source"], "event_id": event["id"]}
        cols = {"id": item_id, "kind": kind, "title": title[:300], "status": status, "source_event_id": event["id"],
                "created_at": at, "updated_at": at, "last_activity_at": at, "field_meta": _j(meta), "engineering": "{}"}
        for k in ("detail", "ticket", "customer", "owner_id", "owner", "due", "priority", "scrum_stage", "blocked_on",
                  "meeting_id"):
            if fields.get(k) is not None:
                cols[k] = fields[k]
        if fields.get("ticket_inferred"):
            cols["ticket_inferred"] = 1
        with self.tx() as db:
            db.execute(f"INSERT INTO items ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))
            for f in ("status",) + tuple(f for f in TRACKED_FIELDS if f != "status" and fields.get(f) is not None):  # noqa: B007
                db.execute("INSERT INTO item_history (item_id, field, old, new, at, event_id, source, by, note, applied)"
                           " VALUES (?,?,?,?,?,?,?,?,?,1)",
                           (item_id, f, None, str(cols.get(f)), at, event["id"], event["source"], by, "created"))
        self.link_event(event["id"], [item_id])
        return self.get_item(item_id)

    def apply(self, item_id: str, changes: dict[str, Any], event: dict[str, Any], *, by: str | None = None,
              note: str | None = None, force: bool = False) -> dict[str, Any]:
        """Apply field changes from one event with source-aware rules.

        - an older statement never overwrites a newer value ("stale", kept in history as not applied)
        - a less authoritative source cannot overwrite a more authoritative value set within
          CONFLICT_WINDOW; the disagreement is recorded as an open conflict instead
        - otherwise the newer value wins and the previous one stays in history
        `force` (dashboard edits by a person) skips the authority check but never the history.
        """
        item = self.get_item(item_id)
        if item is None:
            raise KeyError(item_id)
        at = parse_dt(event["occurred_at"])
        src = event["source"]
        result: dict[str, Any] = {"applied": [], "skipped": [], "conflicts": []}
        meta = item["field_meta"]
        updates: dict[str, Any] = {}
        with self.tx() as db:
            for field, value in changes.items():
                if field not in TRACKED_FIELDS and field not in ("owner_id", "detail", "scrum_stage", "title"):
                    continue  # includes private hints such as _title_rank
                if field == "owner_id":
                    continue  # moves together with "owner"
                current = item.get(field)
                if field == "title":
                    rank = changes.get("_title_rank", 0)
                    if value and rank > (meta.get("title") or {}).get("rank", 0):
                        meta["title"] = {"rank": rank, "at": iso(at), "source": src, "event_id": event["id"]}
                        if str(value) != str(current):
                            updates["title"] = str(value)[:300]
                            db.execute("INSERT INTO item_history (item_id, field, old, new, at, event_id, source, by, note, applied)"
                                       " VALUES (?,?,?,?,?,?,?,?,?,1)", (item_id, "title", current, str(value), iso(at),
                                                                        event["id"], src, by, "better ticket title"))
                    continue
                if value is None or value == "" or str(value) == str(current):
                    if value is not None and str(value) == str(current) and field in TRACKED_FIELDS:
                        m = meta.get(field)
                        if m is None or parse_dt(m["at"]) <= at:  # confirmation keeps the evidence fresh
                            meta[field] = {"at": iso(at), "source": src, "event_id": event["id"]}
                    continue
                if field in ("detail", "scrum_stage"):
                    if not current:
                        updates[field] = value
                    continue
                m = meta.get(field)
                reason = None
                if m and parse_dt(m["at"]) > at:
                    reason = f"older than the current value (set by {m['source']} on {m['at'][:10]})"
                elif (m and not force and authority(m["source"]) >= VERIFIED_AUTHORITY
                      and authority(src) < authority(m["source"]) and at - parse_dt(m["at"]) < CONFLICT_WINDOW):
                    reason = f"{src} is less authoritative than {m['source']} (set {m['at'][:10]})"
                    db.execute(
                        "INSERT INTO conflicts (item_id, field, current_value, current_source, current_event_id, current_at,"
                        " claimed_value, claimed_source, claimed_event_id, claimed_at, status, created_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,'open',?)",
                        (item_id, field, str(current), m["source"], m["event_id"], m["at"], str(value), src,
                         event["id"], iso(at), iso(utcnow())))
                    result["conflicts"].append({"field": field, "current": current, "claimed": value, "source": src})
                if reason:
                    db.execute("INSERT INTO item_history (item_id, field, old, new, at, event_id, source, by, note, applied)"
                               " VALUES (?,?,?,?,?,?,?,?,?,0)",
                               (item_id, field, str(current) if current is not None else None, str(value), iso(at),
                                event["id"], src, by, reason))
                    result["skipped"].append({"field": field, "value": value, "reason": reason})
                    continue
                updates[field] = value
                if field == "owner":
                    updates["owner_id"] = changes.get("owner_id")
                meta[field] = {"at": iso(at), "source": src, "event_id": event["id"]}
                db.execute("INSERT INTO item_history (item_id, field, old, new, at, event_id, source, by, note, applied)"
                           " VALUES (?,?,?,?,?,?,?,?,?,1)",
                           (item_id, field, str(current) if current is not None else None, str(value), iso(at),
                            event["id"], src, by, note))
                result["applied"].append(field)
                # a newer, at-least-as-authoritative value settles earlier disagreements on this field
                db.execute("UPDATE conflicts SET status='resolved', resolution=? WHERE item_id=? AND field=? AND status='open'",
                           (f"superseded by {src} on {iso(at)[:10]}", item_id, field))
            last = max(parse_dt(item["last_activity_at"]), at)
            updates.update(field_meta=_j(meta), updated_at=iso(utcnow()), last_activity_at=iso(last))
            db.execute(f"UPDATE items SET {', '.join(f'{k} = ?' for k in updates)} WHERE id = ?",
                       [*updates.values(), item_id])
        self.link_event(event["id"], [item_id])
        return result

    def record_not_applied(self, item_id: str, field: str, value: Any, event: dict[str, Any], note: str,
                           by: str | None = None) -> None:
        """Keep a statement that was deliberately not applied, with the reason, in the item's history."""
        item = self.get_item(item_id)
        with self.tx() as db:
            db.execute("INSERT INTO item_history (item_id, field, old, new, at, event_id, source, by, note, applied)"
                       " VALUES (?,?,?,?,?,?,?,?,?,0)", (item_id, field, str(item.get(field)) if item else None, str(value),
                                                         event["occurred_at"], event["id"], event["source"], by, note))
        self.link_event(event["id"], [item_id])

    def touch(self, item_id: str, event: dict[str, Any]) -> None:
        """Record activity on an item (e.g. a PR was opened) without changing tracked fields."""
        item = self.get_item(item_id)
        if item is None:
            return
        last = max(parse_dt(item["last_activity_at"]), parse_dt(event["occurred_at"]))
        with self.tx() as db:
            db.execute("UPDATE items SET last_activity_at = ?, updated_at = ? WHERE id = ?", (iso(last), iso(utcnow()), item_id))
        self.link_event(event["id"], [item_id])

    def set_engineering(self, item_id: str, engineering: dict[str, Any]) -> None:
        with self.tx() as db:
            db.execute("UPDATE items SET engineering = ? WHERE id = ?", (_j(engineering), item_id))

    def set_ticket_link(self, item_id: str, ticket: str, inferred: bool) -> None:
        with self.tx() as db:
            db.execute("UPDATE items SET ticket = ?, ticket_inferred = ? WHERE id = ?", (ticket, 1 if inferred else 0, item_id))

    def history(self, item_id: str | None = None, *, fields: tuple[str, ...] | None = None,
                limit: int = 200) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM item_history WHERE 1=1", []
        if item_id:
            sql += " AND item_id = ?"; args.append(item_id)
        if fields:
            sql += f" AND field IN ({','.join('?' * len(fields))})"; args.extend(fields)
        sql += " ORDER BY at DESC, id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) | {"applied": bool(r["applied"])} for r in self.db.execute(sql, args).fetchall()]

    # ------------------------------------------------------------ dependencies
    def add_dependency(self, *, item_id: str, kind: str, description: str, event: dict[str, Any],
                       party: str | None = None, depends_on_item_id: str | None = None) -> tuple[dict[str, Any], bool]:
        for d in self.dependencies(item_id, status="open"):
            same_target = depends_on_item_id and d["depends_on_item_id"] == depends_on_item_id
            if same_target or (d["kind"] == kind and similarity(d["description"], description) >= 0.3):
                return d, False
        did = "dep_" + hashlib.sha1(f"{item_id}:{event['id']}:{description}".encode()).hexdigest()[:12]
        with self.tx() as db:
            db.execute("INSERT OR IGNORE INTO dependencies (id, item_id, kind, description, party, depends_on_item_id, status,"
                       " opened_event_id, opened_at) VALUES (?,?,?,?,?,?,'open',?,?)",
                       (did, item_id, kind, description[:400], party, depends_on_item_id, event["id"], event["occurred_at"]))
        self.link_event(event["id"], [item_id])
        return self.get_dependency(did), True

    def get_dependency(self, dep_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM dependencies WHERE id = ?", (dep_id,)).fetchone()
        return dict(row) if row else None

    def dependencies(self, item_id: str | None = None, *, status: str | None = None,
                     kind: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM dependencies WHERE 1=1", []
        for col, val in (("item_id", item_id), ("status", status), ("kind", kind)):
            if val is not None:
                sql += f" AND {col} = ?"; args.append(val)
        return [dict(r) for r in self.db.execute(sql + " ORDER BY opened_at", args).fetchall()]

    def resolve_dependency(self, dep_id: str, event: dict[str, Any], resolution: str) -> dict[str, Any] | None:
        with self.tx() as db:
            db.execute("UPDATE dependencies SET status='resolved', resolved_event_id=?, resolved_at=?, resolution=?"
                       " WHERE id=? AND status='open'", (event["id"], event["occurred_at"], resolution[:400], dep_id))
        dep = self.get_dependency(dep_id)
        if dep:
            self.link_event(event["id"], [dep["item_id"]])
        return dep

    # ---------------------------------------------------------------- conflicts
    def conflicts(self, item_id: str | None = None, *, status: str | None = "open") -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM conflicts WHERE 1=1", []
        if item_id:
            sql += " AND item_id = ?"; args.append(item_id)
        if status:
            sql += " AND status = ?"; args.append(status)
        return [dict(r) for r in self.db.execute(sql + " ORDER BY claimed_at DESC", args).fetchall()]

    def resolve_conflict(self, conflict_id: int, resolution: str) -> None:
        with self.tx() as db:
            db.execute("UPDATE conflicts SET status='resolved', resolution=? WHERE id=?", (resolution[:400], conflict_id))

    # -------------------------------------------------------------------- risks
    @staticmethod
    def _risk(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        r = dict(row)
        r["evidence"] = json.loads(r["evidence"])
        r["history"] = json.loads(r["history"])
        return r

    def get_risk(self, risk_id: str) -> dict[str, Any] | None:
        return self._risk(self.db.execute("SELECT * FROM risks WHERE id = ?", (risk_id,)).fetchone())

    def risks(self, *, status: str | None = None, item_id: str | None = None, kind: str | None = None,
              limit: int = 500) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM risks WHERE 1=1", []
        for col, val in (("status", status), ("item_id", item_id), ("kind", kind)):
            if val is not None:
                sql += f" AND {col} = ?"; args.append(val)
        sql += " ORDER BY last_changed_at DESC LIMIT ?"
        args.append(limit)
        return [self._risk(r) for r in self.db.execute(sql, args).fetchall()]

    def sync_risks(self, findings: list[dict[str, Any]], now: datetime, trigger: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Open new risks, update changed ones, resolve the ones whose conditions no longer hold."""
        changes: list[dict[str, Any]] = []
        trigger_ref = {"event_id": trigger["id"], "title": trigger["title"]} if trigger else None
        rank = {"high": 0, "medium": 1, "low": 2, "info": 3}
        with self.tx() as db:
            open_rows = {r["key"]: self._risk(r) for r in db.execute("SELECT * FROM risks WHERE status='open'").fetchall()}
            seen = set()
            for f in findings:
                seen.add(f["key"])
                cur = open_rows.get(f["key"])
                entry = {"at": iso(now), "severity": f["severity"], "explanation": f["explanation"], "trigger": trigger_ref}
                if cur is None:
                    n = db.execute("SELECT COUNT(*) FROM risks WHERE key = ?", (f["key"],)).fetchone()[0]
                    rid = "rsk_" + hashlib.sha1(f["key"].encode()).hexdigest()[:10] + (f"_{n + 1}" if n else "")
                    db.execute("INSERT INTO risks (id, key, item_id, rule, kind, severity, status, title, explanation,"
                               " recommended_action, evidence, first_detected_at, last_changed_at, history)"
                               " VALUES (?,?,?,?,?,?,'open',?,?,?,?,?,?,?)",
                               (rid, f["key"], f["item_id"], f["rule"], f["kind"], f["severity"], f["title"],
                                f["explanation"], f["recommended_action"], _j(f["evidence"]), iso(now), iso(now),
                                _j([entry | {"change": "opened"}])))
                    changes.append({"change": "opened", "risk_id": rid, **f})
                elif (cur["severity"], cur["explanation"], cur["title"]) != (f["severity"], f["explanation"], f["title"]):
                    change = ("escalated" if rank[f["severity"]] < rank[cur["severity"]] else
                              "deescalated" if rank[f["severity"]] > rank[cur["severity"]] else "updated")
                    hist = cur["history"] + [entry | {"change": change}]
                    db.execute("UPDATE risks SET severity=?, title=?, explanation=?, recommended_action=?, evidence=?,"
                               " last_changed_at=?, history=?, llm_explanation=NULL WHERE id=?",
                               (f["severity"], f["title"], f["explanation"], f["recommended_action"], _j(f["evidence"]),
                                iso(now), _j(hist[-30:]), cur["id"]))
                    if change != "updated":
                        changes.append({"change": change, "risk_id": cur["id"], **f})
                else:
                    db.execute("UPDATE risks SET evidence=? WHERE id=?", (_j(f["evidence"]), cur["id"]))
            for key, cur in open_rows.items():
                if key in seen:
                    continue
                reason = f"Conditions no longer hold after: {trigger['title']}" if trigger else "Conditions no longer hold"
                hist = cur["history"] + [{"at": iso(now), "change": "resolved", "reason": reason, "trigger": trigger_ref}]
                db.execute("UPDATE risks SET status='resolved', resolved_at=?, resolved_reason=?, last_changed_at=?,"
                           " history=? WHERE id=?", (iso(now), reason, iso(now), _j(hist[-30:]), cur["id"]))
                changes.append({"change": "resolved", "risk_id": cur["id"], "item_id": cur["item_id"], "rule": cur["rule"],
                                "kind": cur["kind"], "severity": cur["severity"], "title": cur["title"],
                                "explanation": reason, "recommended_action": "", "evidence": cur["evidence"]})
        return changes

    def set_risk_explanation(self, risk_id: str, text: str) -> None:
        with self.tx() as db:
            db.execute("UPDATE risks SET llm_explanation = ? WHERE id = ?", (text, risk_id))
