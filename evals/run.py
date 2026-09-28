"""Run the SprintMind evaluation suite.

    python -m evals.run                      # deterministic checks only (no keys needed)
    python -m evals.run --llm                # + answer checks with the Groq model from .env
    python -m evals.run --llm --backend hindsight   # answers recalled from Hindsight Cloud (temporary banks)
    python -m evals.run --only ci-recovery,overdue --json evals/results/latest.json

Every case runs in a fresh app through the real HTTP API. Documents are ingested with their gold
extraction (so state is deterministic); only answers use the LLM. Metrics are computed from what the
system actually returned; nothing is estimated.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import sys
import tempfile
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.github import demo_payload
from app.llm import GroqLLM
from app.main import build_services, create_app
from app.memory import HindsightStore, LocalStore

from .cases import CASES

SECRET = "eval-webhook-secret-0123456789"
TZ = ZoneInfo("Asia/Kolkata")
ABSTAIN = ["not recorded", "no record", "not in sprintmind", "don't have", "do not have", "unknown", "no information",
           "isn't recorded", "is not recorded", "not specified", "no owner", "no due date", "not available", "not mentioned",
           "doesn't mention", "does not mention", "no evidence", "not documented", "isn't documented", "not provided"]


# ------------------------------------------------------------------- dates
def _shift_bdays(day: date, n: int) -> date:
    step = 1 if n >= 0 else -1
    while n:
        day += timedelta(days=step)
        if day.weekday() < 5:
            n -= step
    return day


def today() -> date:
    return datetime.now(TZ).date()


def resolve(value: Any) -> Any:
    """'{+2}' -> ISO date 2 business days from today (team timezone)."""
    if isinstance(value, str):
        m = re.fullmatch(r"\{([+-]\d+)\}", value)
        if m:
            return _shift_bdays(today(), int(m.group(1))).isoformat()
    if isinstance(value, dict):
        return {k: resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v) for v in value]
    return value


def variants(alt: str) -> list[str]:
    """A date may be written many ways in an answer; accept the common ones."""
    m = re.fullmatch(r"\{([+-]\d+)\}", alt)
    if not m:
        return [alt]
    d = _shift_bdays(today(), int(m.group(1)))
    return [d.isoformat(), f"{d.day} {d:%b}", f"{d:%b} {d.day}", f"{d.day} {d:%B}", f"{d:%B} {d.day}", f"{d.day:02d} {d:%b}",
            f"{d:%d/%m}", f"{d:%m/%d}", f"{d.day}/{d.month}"]


def occurred(step: dict[str, Any]) -> str:
    if step.get("hours_ago") is not None:
        return (datetime.now(timezone.utc) - timedelta(hours=step["hours_ago"])).isoformat()
    day = _shift_bdays(today(), -(step.get("bdays_ago") or 0))
    return datetime.combine(day, dtime(10, 0), TZ).isoformat()


# ---------------------------------------------------------------- the LLM
class EvalLLM:
    """Gold extraction for documents (deterministic state); a real model, or a stub, for everything else."""

    def __init__(self, real: Any = None) -> None:
        self.real = real
        self.gold: dict[str, list[dict[str, Any]]] = {}

    async def complete(self, system: str, user: str, *, label: str, temperature: float = 0.2) -> str:
        if self.real:
            return await self.real.complete(system, user, label=label, temperature=temperature)
        return "Not recorded in SprintMind (deterministic mode: answers are not evaluated) [1]."

    async def complete_json(self, system: str, user: str, *, label: str) -> dict[str, Any]:
        if label.startswith("extract"):
            title = (re.search(r"^Title: (.*)$", user, re.M) or [None, ""])[1].strip()
            return {"summary": title, "items": [dict(i) for i in self.gold.get(title, [])]}
        if self.real:
            return await self.real.complete_json(system, user, label=label)
        return {}


# ------------------------------------------------------------------ runner
class CaseRun:
    def __init__(self, case: dict[str, Any], *, real_llm: Any = None, backend: str = "local") -> None:
        self.case = case
        self.tmp = tempfile.TemporaryDirectory()
        base = get_settings() if backend == "hindsight" else None
        bank = f"sprintmind_eval_{case['id']}_{int(time.time())}"
        settings = Settings(memory_backend="local", registry_file=f"{self.tmp.name}/reg.json", team_file="data/team.json",
                            meetings_dir=f"{self.tmp.name}/meetings", ledger_path=":memory:", github_webhook_secret=SECRET,
                            groq_api_key=None, bank_id=bank, learn_from_interactions=True)
        store = (HindsightStore(base.hindsight_base_url, base.hindsight_api_key, base.hindsight_timeout, False)
                 if backend == "hindsight" else LocalStore())
        self.llm = EvalLLM(real_llm)
        self.services = build_services(settings, store=store, llm=self.llm, transcriber=None)
        self.client = TestClient(create_app(self.services), headers={"X-SprintMind-User": "neha"})
        self.client.__enter__()
        self.backend = backend
        if backend == "hindsight":
            self.client.post("/admin/setup")

    def close(self) -> None:
        if self.backend == "hindsight":
            self.client.post("/admin/reset")
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    # -- helpers
    def item_id(self, spec: dict[str, Any]) -> str | None:
        if spec.get("item"):
            return spec["item"]
        items = self.client.get("/actions", params={"kind": spec["kind"]}).json()["actions"]
        return items[0]["id"] if items else None

    def as_(self, who: str | None) -> dict[str, str]:
        return {"X-SprintMind-User": who or "neha"}

    # -- steps
    def step(self, s: dict[str, Any]) -> None:
        c = self.client
        if "doc" in s:
            d = resolve(s["doc"])
            self.llm.gold[d["title"]] = d["items"]
            r = c.post("/ingest", json={"source_type": d["source"], "title": d["title"], "content": d["content"],
                                        "occurred_at": occurred(d), "customer": d.get("customer")})
            assert r.status_code == 200, r.text
        elif "github" in s:
            g = s["github"]
            at = datetime.now(timezone.utc) - timedelta(hours=g.get("hours_ago") or 0)
            event, payload = demo_payload(g["kind"], repo="northwind/platform", ticket=g["ticket"], pr_number=g["pr"],
                                          title="Eval change", run_id=g.get("run_id"), at=at)
            body = json.dumps(payload).encode()
            sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
            r = c.post("/integrations/github/webhook", content=body, headers={
                "X-GitHub-Event": event, "X-Hub-Signature-256": sig, "X-GitHub-Delivery": g.get("delivery") or "eval",
                "Content-Type": "application/json"})
            assert r.status_code == 200, r.text
        elif "update" in s:
            u = resolve(s["update"])
            iid = self.item_id(u)
            patch = {k: u[k] for k in ("status", "owner", "due", "ticket", "note") if u.get(k)}
            r = c.patch(f"/actions/{iid}", json=patch, headers=self.as_(u.get("as")))
            assert r.status_code == 200, r.text
        elif "add_dependency" in s:
            a = s["add_dependency"]
            r = c.post(f"/items/{self.item_id(a)}/dependencies", json={"description": a["description"], "kind": a["type"]})
            assert r.status_code == 200, r.text
        elif "approve" in s:
            a = s["approve"]
            dep = next(d for d in c.get(f"/items/{self.item_id(a)}").json()["dependencies"] if d["status"] == "open")
            r = c.post(f"/dependencies/{dep['id']}/resolve", json={"note": a.get("note")})
            assert r.status_code == 200, r.text
        elif "correction" in s:
            k = s["correction"]
            self.llm.gold[f"Correction to: {k['question']}"] = k["items"]
            iid = c.post("/employee/ask", json={"question": k["question"], "person_id": k["as"]},
                         headers=self.as_(k["as"])).json()["interaction_id"]
            r = c.post("/feedback", json={"interaction_id": iid, "helpful": False, "correction": k["text"]}, headers=self.as_(k["as"]))
            assert r.status_code == 200, r.text
        elif "ask" in s:
            a = s["ask"]
            r = c.post("/employee/ask", json={"question": a["question"], "person_id": a.get("person")}, headers=self.as_(a["as"]))
            assert r.status_code == 200, r.text
        else:
            raise ValueError(f"unknown step {s}")

    # -- deterministic checks
    def check(self, chk: dict[str, Any]) -> tuple[bool, str]:
        c, ledger = self.client, self.services.delivery.ledger
        (kind, spec), = chk.items()
        spec = resolve(spec)
        if kind == "state":
            iid = self.item_id(spec)
            if not iid:
                return False, "item not found"
            it = c.get(f"/items/{iid}").json()
            got = (it["evidence"].get("status") or {}).get("source") if spec["field"] == "status_source" else it.get(spec["field"])
            return got == spec["equals"], f"{spec['field']}={got!r}"
        if kind == "history":
            iid = self.item_id(spec)
            vals = [h["new"] for h in ledger.history(iid, fields=(spec["field"],)) if h["applied"]]
            return vals == spec["values"], f"applied values newest first: {vals}"
        if kind == "not_applied":
            rows = [h for h in ledger.history(spec["item"], fields=(spec["field"],)) if not h["applied"] and h["new"] == spec["value"]]
            return bool(rows), rows[0]["note"] if rows else "no rejected change recorded"
        if kind == "dependency":
            iid = self.item_id(spec)
            deps = ledger.dependencies(iid)
            want = spec.get("type") or (spec.get("kind") if "item" in spec else None)
            ok = any(d["kind"] == want and d["status"] == spec["status"] for d in deps)
            return ok, str([(d["kind"], d["status"]) for d in deps])
        if kind == "risk":
            iid = self.item_id(spec)
            rows = [r for r in ledger.risks(item_id=iid, status="open") if r["rule"] == spec["rule"]]
            if not spec["open"]:
                return not rows, f"open: {[r['rule'] for r in ledger.risks(item_id=iid, status='open')]}"
            ok = bool(rows) and (not spec.get("severity") or rows[0]["severity"] == spec["severity"]) and \
                (not spec.get("risk_kind") or rows[0]["kind"] == spec["risk_kind"])
            return ok, str([(r["rule"], r["severity"], r["kind"]) for r in ledger.risks(item_id=iid, status="open")])
        if kind == "risk_evidence":
            rows = [r for r in ledger.risks(item_id=self.item_id(spec), status="open") if r["rule"] == spec["rule"]]
            return bool(rows) and any(e["source"] == spec["source"] for e in rows[0]["evidence"]), "evidence sources: " + \
                str([e["source"] for r in rows for e in r["evidence"]])
        if kind == "risk_history":
            rows = [r for r in ledger.risks(item_id=self.item_id(spec)) if r["rule"] == spec["rule"]]
            changes = [h["change"] for r in rows for h in r["history"]]
            return changes == spec["changes"], str(changes)
        if kind == "events":
            evs = ledger.events(source=spec.get("source"), kind_prefix=spec.get("kind"), limit=500)
            if spec.get("rule"):
                evs = [e for e in evs if e["payload"].get("rule") == spec["rule"]]
            return len(evs) == spec["count"], f"{len(evs)} events"
        if kind == "memories":
            if self.backend != "local":
                return True, "skipped (Hindsight backend)"
            n = sum(1 for m in self.services.memory.store.banks.get(self.services.memory.bank_id, [])
                    if m["text"].startswith("STANDUP:") and spec["document_contains"] in m["text"])
            return n <= spec["max"], f"{n} raw documents"
        if kind == "no_real_risks":
            n = c.get("/manager/dashboard").json()["summary"]["open_risks"]
            return n == 0, f"{n} open risks"
        if kind == "visible":
            r = c.post("/employee/ask", json={"question": spec["question"]}, headers=self.as_(spec["as"])).json()
            seen = any(spec["contains"] in m["text"] for m in r["memories"])
            return seen == spec["expect"], f"visible={seen}"
        raise ValueError(f"unknown check {kind}")

    # -- answer checks
    def answer(self, spec: dict[str, Any]) -> dict[str, Any]:
        r = self.client.post("/employee/ask", json={"question": spec["question"], "person_id": spec.get("person")},
                             headers=self.as_(spec.get("as"))).json()
        text = r.get("answer") or ""
        low = text.lower()
        groups = [[v for alt in g for v in variants(alt)] for g in spec.get("expect", [])]
        missing = [g[0] for g in groups if not any(v.lower() in low for v in g)]
        forbidden = [f for f in spec.get("forbid", []) if f.lower() in low]
        abstained = any(p in low for p in ABSTAIN)
        cited = [r["memories"][i - 1]["text"] for i in (r.get("citations") or {}).get("cited", []) if 0 < i <= len(r["memories"])]
        unsupported = [p for p in spec.get("cite_support", [])
                       if not any(any(alt.lower() in t.lower() for alt in p.split("|")) for t in cited)]
        out = {"question": spec["question"], "answer": text, "abstain_case": bool(spec.get("abstain")),
               "temporal": bool(spec.get("temporal")), "missing": missing, "forbidden": forbidden, "abstained": abstained,
               "cited": len(cited), "invalid_citations": (r.get("citations") or {}).get("invalid_removed", []),
               "unsupported_citations": unsupported}
        if spec.get("abstain"):
            out["correct"] = abstained and not forbidden
            out["citation_supported"] = None
        else:
            out["correct"] = not missing and not forbidden
            out["citation_supported"] = bool(cited) and not unsupported and not out["invalid_citations"]
        return out


def run_case(case: dict[str, Any], *, real_llm: Any = None, backend: str = "local") -> dict[str, Any]:
    # An async HTTP client is bound to the event loop that created it; each case's TestClient has its own loop.
    run = CaseRun(case, real_llm=real_llm() if callable(real_llm) else real_llm, backend=backend)
    result: dict[str, Any] = {"id": case["id"], "category": case["category"], "checks": [], "answers": [], "error": None}
    try:
        for s in case["steps"]:
            run.step(s)
        for chk in case["checks"]:
            if "answer" in chk:
                if real_llm is not None:
                    result["answers"].append(run.answer(resolve(chk["answer"]) | {"expect": chk["answer"].get("expect", []),
                                                                                  "forbid": chk["answer"].get("forbid", [])}))
                continue
            ok, detail = run.check(chk)
            result["checks"].append({"check": next(iter(chk)), "ok": ok, "detail": detail})
    except Exception as exc:  # noqa: BLE001 - reported per case
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        run.close()
    return result


def pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "n/a"


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    checks = [c for r in results for c in r["checks"]]
    answers = [a for r in results for a in r["answers"]]
    factual = [a for a in answers if not a["abstain_case"]]
    temporal = [a for a in answers if a["temporal"]]
    abstain = [a for a in answers if a["abstain_case"]]
    return {
        "cases": len(results), "case_errors": [r["id"] for r in results if r["error"]],
        "deterministic_checks": {"passed": sum(c["ok"] for c in checks), "total": len(checks)},
        "factual_correctness": {"passed": sum(a["correct"] for a in factual), "total": len(factual)},
        "temporal_correctness": {"passed": sum(a["correct"] for a in temporal), "total": len(temporal)},
        "abstention": {"passed": sum(a["correct"] for a in abstain), "total": len(abstain)},
        "citation_support": {"passed": sum(bool(a["citation_supported"]) for a in factual), "total": len(factual)},
        "invalid_citations_removed": sum(len(a["invalid_citations"]) for a in answers),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="also run answer checks with the Groq model from .env")
    ap.add_argument("--backend", choices=["local", "hindsight"], default="local")
    ap.add_argument("--only", help="comma-separated case ids")
    ap.add_argument("--json", help="write full results to this file")
    ap.add_argument("--model", help="answer model (default: GROQ_MODEL from .env)")
    args = ap.parse_args()

    real = None
    if args.llm:
        s = get_settings()
        if not s.groq_api_key:
            print("--llm needs GROQ_API_KEY in .env", file=sys.stderr)
            return 2
        from app.inspector import Inspector
        model = args.model or s.groq_model

        def real() -> GroqLLM:
            return GroqLLM(s.groq_api_key, s.groq_base_url, model, None if args.model else s.groq_fallback_model,
                           s.llm_timeout, Inspector(), s.llm_max_tokens)
        print(f"answer model: {model}\n")
    cases = [c for c in CASES if not args.only or c["id"] in args.only.split(",")]
    results = []
    for case in cases:
        t0 = time.perf_counter()
        r = run_case(case, real_llm=real, backend=args.backend)
        results.append(r)
        det = f"{sum(c['ok'] for c in r['checks'])}/{len(r['checks'])} checks"
        ans = f", {sum(a['correct'] for a in r['answers'])}/{len(r['answers'])} answers" if r["answers"] else ""
        print(f"{'ERROR' if r['error'] else 'ok   '} {case['id']:<34} {det}{ans}  ({time.perf_counter() - t0:.1f}s)"
              + (f"  {r['error']}" if r["error"] else ""))
        for c in r["checks"]:
            if not c["ok"]:
                print(f"        FAIL {c['check']}: {c['detail']}")
        for a in r["answers"]:
            if not a["correct"] or a["citation_supported"] is False:
                print(f"        ANSWER {'ok' if a['correct'] else 'WRONG'} / citations {'ok' if a['citation_supported'] else 'unsupported' if a['citation_supported'] is False else 'n/a'}: "
                      f"missing={a['missing']} forbidden={a['forbidden']} unsupported={a['unsupported_citations']}")
                print("          " + a["answer"][:300].replace("\n", " "))
    summary = summarise(results)
    print("\nSummary")
    print(f"  cases                    {summary['cases']} ({len(summary['case_errors'])} with errors)")
    for key in ("deterministic_checks", "factual_correctness", "temporal_correctness", "abstention", "citation_support"):
        v = summary[key]
        print(f"  {key.replace('_', ' '):<24} {pct(v['passed'], v['total'])}")
    print(f"  invalid citations removed {summary['invalid_citations_removed']}")
    if not args.llm:
        print("  (answer metrics need --llm)")
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps({"run_at": datetime.now(timezone.utc).isoformat(), "llm": bool(args.llm),
                                               "model": (args.model or get_settings().groq_model) if args.llm else None,
                                               "backend": args.backend, "summary": summary, "results": results}, indent=2))
    ok = not summary["case_errors"] and summary["deterministic_checks"]["passed"] == summary["deterministic_checks"]["total"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
