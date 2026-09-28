"""Who is calling, and what they may see.

Two modes (AUTH_MODE):
  demo   the caller is the team member named in `X-SprintMind-User` (the UI's "Viewing as" switcher).
         Easy for demos, not secure: anyone can claim any identity.
  token  callers send `Authorization: Bearer <token>`; API_TOKENS maps tokens to team members.

Roles come from data/team.json (`"access": "manager"`). Managers see everything. Employees can ask as
themselves, see team delivery data, and edit items they own (or unassigned ones). Q&A logs and answer
ratings are private to the person who produced them; corrections are shared team knowledge.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any

from .team import Team


class Unauthenticated(Exception):
    pass


class Forbidden(Exception):
    pass


@dataclass(frozen=True)
class Caller:
    person_id: str | None
    name: str | None
    role: str  # manager | employee | anonymous

    @property
    def is_manager(self) -> bool:
        return self.role == "manager"

    @property
    def label(self) -> str:
        return self.name or "anonymous"


ANONYMOUS = Caller(None, None, "anonymous")


def parse_tokens(raw: str | None) -> dict[str, str]:
    tokens = {}
    for pair in (raw or "").split(","):
        if "=" in pair:
            pid, tok = pair.split("=", 1)
            if pid.strip() and len(tok.strip()) >= 16:
                tokens[tok.strip()] = pid.strip()
    return tokens


def resolve_caller(headers: Any, mode: str, team: Team, tokens: dict[str, str]) -> Caller:
    if mode == "token":
        auth = headers.get("authorization") or ""
        if not auth:
            return ANONYMOUS
        if not auth.lower().startswith("bearer "):
            raise Unauthenticated("use Authorization: Bearer <token>")
        given = auth[7:].strip()
        pid = next((p for t, p in tokens.items() if hmac.compare_digest(t, given)), None)
        if pid is None:
            raise Unauthenticated("invalid token")
    else:
        pid = (headers.get("x-sprintmind-user") or "").strip()
        if not pid:
            return ANONYMOUS
    member = team.member(pid)
    if member is None:
        raise Unauthenticated(f"unknown user {pid!r}")
    return Caller(pid, member["name"], "manager" if team.is_manager(pid) else "employee")


def require_person(c: Caller) -> None:
    if c.person_id is None:
        raise Unauthenticated("identify yourself (X-SprintMind-User header, or a Bearer token in token mode)")


def require_manager(c: Caller) -> None:
    require_person(c)
    if not c.is_manager:
        raise Forbidden("manager access required")


def require_self_or_manager(c: Caller, person_id: str | None) -> None:
    if person_id is None:
        return
    require_person(c)
    if not c.is_manager and c.person_id != person_id:
        raise Forbidden("you can only ask as yourself; managers can ask on behalf of the team")


def can_edit_item(c: Caller, item: dict[str, Any]) -> bool:
    return c.is_manager or (c.person_id is not None and item.get("owner_id") in (None, c.person_id))


def visible_to(fact: dict[str, Any], c: Caller) -> bool:
    """Private memories: Q&A logs and answer ratings belong to the person who produced them."""
    if c.is_manager:
        return True
    tags = set(fact.get("tags") or [])
    private = "source:interaction" in tags or ("source:feedback" in tags and "kind:correction" not in tags)
    if not private:
        return True
    if "role:manager" in tags:
        return False
    people = {t.split(":", 1)[1] for t in tags if t.startswith("person:")}
    return bool(c.person_id and c.person_id in people)
