"""Run the 3-step judge demo end to end against a running API and print the results.

    python -m scripts.demo_flow            # assumes `python -m scripts.seed` was run first
"""
from __future__ import annotations

import argparse
import textwrap

import httpx

from scripts.seed import api_headers

QUESTION = "What were the key deliverables agreed with Acme Corp in yesterday's Teams sync, and who owns each one?"


def show(title: str, text: str) -> None:
    print(f"\n=== {title} ===")
    print(textwrap.indent((text or "").strip(), "  "))


def main(base: str) -> None:
    with httpx.Client(base_url=base, timeout=600, headers=api_headers()) as c:
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

        dash = c.get("/manager/dashboard").json()
        show("STEP 3c  Delivery radar (rule-based, evidence-backed)", f"{dash['summary']}")
        for r in dash["risks"]:
            print(f"  {r['severity'].upper():<6} {r['title']}: {r['explanation']}\n         next: {r['recommended_action']}")

        # Step 4 - engineering reality: a commitment, a failing CI run, the alert, the fix
        for step in ("commitment", "ci_failure", "fix", "close"):
            out = c.post(f"/demo/delivery/{step}").json()
            risks = [r for r in c.get("/manager/dashboard").json()["risks"] if r["item_id"] == out["commitment"]["id"]]
            show(f"STEP 4  delivery demo: {step}",
                 f"commitment status: {out['commitment']['status']}; open risks: " + (", ".join(f"{r['severity']} {r['rule']}" for r in risks) or "none"))

        ev = c.get("/inspector/events").json()
        print("\nInspector:", ev["stats"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    main(ap.parse_args().api)
