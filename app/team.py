"""Team roster: maps free-text names in transcripts ("Priya", "P. Raman") to stable person ids."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .memory import slug


class Team:
    def __init__(self, name: str, members: list[dict[str, Any]], customers: list[dict[str, Any]]) -> None:
        self.name = name
        self.members = members
        self.customers = customers
        self._alias: dict[str, str] = {}
        for m in members:
            for a in [m["name"], m["name"].split()[0], *m.get("aliases", []), m["id"]]:
                self._alias[a.lower()] = m["id"]
        self._cust: dict[str, str] = {}
        for c in customers:
            for a in [c["name"], c["name"].split()[0], *c.get("aliases", []), c["id"]]:
                self._cust[a.lower()] = c["id"]

    @classmethod
    def load(cls, path: str, fallback_name: str) -> "Team":
        p = Path(path)
        if not p.exists():
            return cls(fallback_name, [], [])
        data = json.loads(p.read_text())
        return cls(data.get("team", fallback_name), data.get("members", []), data.get("customers", []))

    def resolve_person(self, name: str | None) -> str | None:
        if not name:
            return None
        key = name.strip().lower()
        if key in self._alias:
            return self._alias[key]
        first = key.split()[0] if key.split() else key
        return self._alias.get(first)

    def resolve_customer(self, name: str | None) -> str | None:
        if not name:
            return None
        key = name.strip().lower()
        return self._cust.get(key) or self._cust.get(key.split()[0]) or slug(name)

    def member(self, person_id: str) -> dict[str, Any] | None:
        return next((m for m in self.members if m["id"] == person_id), None)

    def is_manager(self, person_id: str | None) -> bool:
        m = self.member(person_id) if person_id else None
        return bool(m and m.get("access") == "manager")

    def roster_text(self) -> str:
        return "\n".join(f"- {m['name']} ({m['role']})" for m in self.members)
