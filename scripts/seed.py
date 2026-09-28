"""Seed the SprintMind memory bank with the synthetic Sprint 14 dataset.

    python -m scripts.seed                 # via the running API (so the Live Inspector shows it)
    python -m scripts.seed --direct        # no API server needed
    python -m scripts.seed --include-demo  # also ingest the Acme Teams sync (skip this for live demos)
    python -m scripts.seed --reset         # delete the bank first
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.seed import load_documents  # noqa: E402


def api_headers() -> dict[str, str]:
    """Identify as a manager: a Bearer token in token mode (SPRINTMIND_TOKEN), else the demo-mode header."""
    import os
    token = os.environ.get("SPRINTMIND_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {"X-SprintMind-User": os.environ.get("SPRINTMIND_USER", "neha")}


async def run_direct(include_demo: bool, reset: bool) -> None:
    from app.config import get_settings
    from app.main import build_services

    s = build_services(get_settings())
    try:
        if reset:
            try:
                await s.memory.reset()
            except Exception as exc:  # noqa: BLE001 - bank may not exist yet
                print(f"reset skipped: {exc}")
            s.registry.clear()
            s.delivery.ledger.reset()
        print("setup:", await s.memory.setup())
        for doc in load_documents(include_demo):
            t0 = time.perf_counter()
            r = await s.ingestor.ingest(**doc)
            err = f"  (extraction error: {r['extraction_error']})" if r.get("extraction_error") else ""
            print(f"  {doc['occurred_at']:%a %d %b} {doc['source_type']:<11} {doc['title'][:55]:<55} "
                  f"{r['facts_retained']:>3} facts  {time.perf_counter() - t0:5.1f}s{err}")
    finally:
        await s.memory.store.close()


def run_api(base: str, include_demo: bool, reset: bool) -> None:
    import httpx

    with httpx.Client(base_url=base, timeout=600, headers=api_headers()) as c:
        if reset:
            print("reset:", c.post("/admin/reset").status_code)
        print("setup:", c.post("/admin/setup").json())
        for doc in load_documents(include_demo):
            t0 = time.perf_counter()
            payload = {**doc, "occurred_at": doc["occurred_at"].isoformat()}
            r = c.post("/ingest", json=payload)
            r.raise_for_status()
            body = r.json()
            print(f"  {doc['occurred_at']:%a %d %b} {doc['source_type']:<11} {doc['title'][:55]:<55} "
                  f"{body['facts_retained']:>3} facts  {time.perf_counter() - t0:5.1f}s")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--direct", action="store_true")
    ap.add_argument("--include-demo", action="store_true")
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    if a.direct:
        asyncio.run(run_direct(a.include_demo, a.reset))
    else:
        run_api(a.api, a.include_demo, a.reset)
