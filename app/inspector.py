"""Live Memory Inspector: an in-process event log of every retain / recall / reflect / LLM call.

The dashboard polls `GET /inspector/events?after=<id>` or subscribes to the SSE stream at
`GET /inspector/stream` so judges can watch memory operations happen in real time.
"""
from __future__ import annotations

import asyncio
import itertools
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator


class Inspector:
    def __init__(self, max_events: int = 500) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._ids = itertools.count(1)
        self._subscribers: set[asyncio.Queue] = set()

    # ---- recording ---------------------------------------------------------
    def emit(self, op: str, status: str, **fields: Any) -> dict[str, Any]:
        event = {
            "id": next(self._ids),
            "ts": datetime.now(timezone.utc).isoformat(),
            "op": op,  # retain | recall | reflect | llm | ingest | setup
            "status": status,  # started | ok | error
            **fields,
        }
        self._events.append(event)
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
        return event

    @asynccontextmanager
    async def track(self, op: str, **request: Any) -> AsyncIterator[dict[str, Any]]:
        """Wrap an operation: emits `started`, then `ok`/`error` with duration.

        The body can put response details into the yielded dict; they are attached to the
        final event.
        """
        started = self.emit(op, "started", request=request)
        result: dict[str, Any] = {}
        t0 = time.perf_counter()
        try:
            yield result
        except Exception as exc:  # noqa: BLE001 - recorded then re-raised
            self.emit(
                op,
                "error",
                parent_id=started["id"],
                request=request,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=round((time.perf_counter() - t0) * 1000),
            )
            raise
        self.emit(
            op,
            "ok",
            parent_id=started["id"],
            request=request,
            response=result,
            duration_ms=round((time.perf_counter() - t0) * 1000),
        )

    # ---- reading -----------------------------------------------------------
    def events(self, after: int = 0, limit: int = 200, op: str | None = None) -> list[dict[str, Any]]:
        out = [e for e in self._events if e["id"] > after and (op is None or e["op"] == op)]
        return out[-limit:]

    def stats(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for e in self._events:
            if e["status"] == "ok":
                counts[e["op"]] = counts.get(e["op"], 0) + 1
        return {"completed_by_op": counts, "buffered_events": len(self._events)}

    def clear(self) -> None:
        self._events.clear()

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subscribers.discard(q)
