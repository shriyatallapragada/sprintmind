"""Run the 3-step judge demo end to end against a running API and print the results.

    python -m scripts.demo_flow            # assumes `python -m scripts.seed` was run first
"""
from __future__ import annotations

import argparse
import textwrap

import httpx

QUESTION = "What were the key deliverables agreed with Acme Corp in yesterday's Teams sync, and who owns each one?"


def show(title: str, text: str) -> None:
    print(f"\n=== {title} ===")
    print(textwrap.indent((text or "").strip(), "  "))


def main(base: str) -> None:
    with httpx.Client(base_url=base, timeout=600) as c:
        print("health:", c.get("/health").json())

        # Step 1 - the frustration: no memory
        show("STEP 1  Plain LLM, no memory", c.post("/demo/baseline", json={"question": QUESTION}).json()["answer"])

        # Step 2 - the learning event: retain the meeting
        t = c.get("/demo/transcript").json()
        r = c.post("/ingest", json=t).json()
        show("STEP 2  retain() the Acme Teams sync",
             f"{r['facts_retained']} facts retained for {', '.join(r['people'])}\n" +
             "\n".join(f"- [{i.get('kind')}] {i.get('title')} -> {i.get('owner')} (due {i.get('due')})" for i in r["items"]))

        # Step 3 - the copilot: recall + reflect
        a = c.post("/employee/ask", json={"question": QUESTION}).json()
        show(f"STEP 3a  Same question, with memory ({a['memory_count']} memories recalled)", a["answer"])

        a = c.post("/employee/ask", json={"person_id": "priya",
                                          "question": "What is my highest priority task today and what SOP should I follow?"}).json()
        show("STEP 3b  Developer view (Priya)", a["answer"])

        radar = c.get("/manager/radar").json()
        rep = radar["report"] or {}
        show("STEP 3c  Manager radar (reflect)", f"health: {rep.get('health')}\n{rep.get('summary') or radar['narrative']}")
        for b in rep.get("blocked", []):
            print(f"  BLOCKED {b.get('ticket')} {b.get('title')} ({b.get('owner')}) waiting on {b.get('waiting_on')}")

        ev = c.get("/inspector/events").json()
        print("\nInspector:", ev["stats"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    main(ap.parse_args().api)
