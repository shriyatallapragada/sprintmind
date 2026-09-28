"""Loads the synthetic Sprint 14 dataset, with dates relative to today so "yesterday" stays true."""
from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))
SEED_DIR = Path(__file__).resolve().parent.parent / "data" / "seed"
TIME_OF_DAY = {"standup": time(10, 0), "meeting": time(16, 0), "task_update": time(19, 0),
               "incident": time(18, 0), "sop": time(12, 0)}


def business_days_ago(n: int, today: datetime | None = None) -> datetime:
    day = (today or datetime.now(IST)).date()
    while n > 0:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            n -= 1
    return datetime.combine(day, time(0, 0), IST)


def load_documents(include_demo: bool = False, today: datetime | None = None) -> list[dict[str, Any]]:
    manifest = json.loads((SEED_DIR / "manifest.json").read_text())
    entries = manifest["documents"] + (manifest["demo"] if include_demo else [])
    docs = []
    for e in entries:
        day = business_days_ago(e["business_days_ago"], today)
        occurred = datetime.combine(day.date(), TIME_OF_DAY.get(e["source_type"], time(12, 0)), IST)
        docs.append({
            "source_type": e["source_type"],
            "title": e["title"],
            "content": (SEED_DIR / e["file"]).read_text(),
            "occurred_at": occurred,
            "customer": e.get("customer"),
            "participants": e.get("participants"),
        })
    return sorted(docs, key=lambda d: d["occurred_at"])


def demo_transcript() -> dict[str, Any]:
    manifest = json.loads((SEED_DIR / "manifest.json").read_text())
    e = manifest["demo"][0]
    return {
        "source_type": e["source_type"],
        "title": e["title"],
        "customer": e.get("customer"),
        "participants": e.get("participants"),
        "occurred_at": datetime.combine(business_days_ago(e["business_days_ago"]).date(), time(16, 0), IST),
        "content": (SEED_DIR / e["file"]).read_text(),
    }
