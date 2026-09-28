"""GitHub webhook handling: signature verification, payload normalisation and ticket linking.

Supported deliveries (subscribe to these in the repo's webhook settings):
  pull_request   opened / reopened / ready_for_review / closed (merged or not)
  workflow_run   completed (success / failure / timed_out)
  check_run      completed (success / failure / timed_out)
  status         success / failure / error
Everything else (including `ping`) is acknowledged and ignored.

Tickets are linked from the PR title, branch name and body using the configured project keys
(TICKET_PREFIXES, e.g. "NW" matches NW-231, nw-231, feature/nw-231-limits). A CI run without a
ticket in its branch is linked through the PR numbers GitHub reports for it.

`demo_payload` builds payloads in the same shape GitHub sends, so demo events go through exactly
the same normalise -> ledger -> risk pipeline as real webhooks.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

FAILED = {"failure", "timed_out", "startup_failure", "error"}
PASSED = {"success"}


class SignatureError(ValueError):
    pass


def verify_signature(secret: str, body: bytes, header: str | None) -> None:
    """GitHub signs the raw body with HMAC-SHA256 and sends `X-Hub-Signature-256: sha256=<hex>`."""
    if not header or not header.startswith("sha256="):
        raise SignatureError("missing X-Hub-Signature-256 header")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, header):
        raise SignatureError("signature does not match")


def ticket_regex(prefixes: str) -> re.Pattern[str]:
    keys = [re.escape(p.strip()) for p in prefixes.split(",") if p.strip()] or ["[A-Z][A-Z0-9]{1,9}"]
    return re.compile(rf"(?<![A-Za-z0-9])({'|'.join(keys)})[-_ ]?(\d{{1,6}})(?![0-9])", re.I)


def find_tickets(pattern: re.Pattern[str], *texts: str | None) -> list[str]:
    out: list[str] = []
    for t in texts:
        for key, num in pattern.findall(t or ""):
            tid = f"{key.upper()}-{int(num)}"
            if tid not in out:
                out.append(tid)
    return out


@dataclass
class EngineeringEvent:
    kind: str                     # github.pr_opened | github.pr_merged | github.pr_closed | github.ci_failed | github.ci_passed
    dedup_key: str
    occurred_at: datetime
    title: str
    tickets: list[str]
    actor: str | None = None
    url: str | None = None
    repo: str | None = None
    pr_numbers: list[int] = field(default_factory=list)
    branch: str | None = None
    check_name: str | None = None
    conclusion: str | None = None
    head_sha: str | None = None


def _ts(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def normalise(event: str, payload: dict[str, Any], pattern: re.Pattern[str]) -> EngineeringEvent | None:
    repo = (payload.get("repository") or {}).get("full_name")
    sender = (payload.get("sender") or {}).get("login")
    if event == "pull_request":
        pr = payload.get("pull_request") or {}
        action = payload.get("action")
        number = pr.get("number") or payload.get("number")
        head = pr.get("head") or {}
        tickets = find_tickets(pattern, pr.get("title"), head.get("ref"), pr.get("body"))
        base = dict(repo=repo, pr_numbers=[number] if number else [], branch=head.get("ref"), head_sha=head.get("sha"),
                    url=pr.get("html_url"), tickets=tickets)
        if action in ("opened", "reopened", "ready_for_review"):
            return EngineeringEvent(kind="github.pr_opened", dedup_key=f"gh:pr:{repo}#{number}:{action}:{head.get('sha')}",
                                    occurred_at=_ts(pr.get("updated_at") or pr.get("created_at")),
                                    title=f"PR #{number} {action.replace('_', ' ')}: {pr.get('title', '')}",
                                    actor=(pr.get("user") or {}).get("login") or sender, **base)
        if action == "closed":
            merged = bool(pr.get("merged"))
            return EngineeringEvent(kind="github.pr_merged" if merged else "github.pr_closed",
                                    dedup_key=f"gh:pr:{repo}#{number}:{'merged' if merged else 'closed'}",
                                    occurred_at=_ts(pr.get("merged_at") or pr.get("closed_at")),
                                    title=f"PR #{number} {'merged' if merged else 'closed without merging'}: {pr.get('title', '')}",
                                    actor=(pr.get("merged_by") or {}).get("login") or sender, **base)
        return None

    if event in ("workflow_run", "check_run"):
        run = payload.get(event) or {}
        if payload.get("action") != "completed" and run.get("status") != "completed":
            return None
        conclusion = run.get("conclusion")
        if conclusion not in FAILED | PASSED:
            return None  # cancelled / skipped / neutral runs say nothing about the code
        if event == "workflow_run":
            branch, name, sha = run.get("head_branch"), run.get("name"), run.get("head_sha")
            label = run.get("display_title") or ""
            key = f"gh:wf:{repo}:{run.get('id')}:{run.get('run_attempt', 1)}:{conclusion}"
            at = run.get("updated_at") or run.get("run_started_at")
        else:
            suite = run.get("check_suite") or {}
            branch, name, sha = suite.get("head_branch"), run.get("name"), run.get("head_sha")
            label = (run.get("output") or {}).get("title") or ""
            key = f"gh:check:{repo}:{run.get('id')}:{conclusion}"
            at = run.get("completed_at")
        prs = [p.get("number") for p in run.get("pull_requests") or [] if p.get("number")]
        failed = conclusion in FAILED
        return EngineeringEvent(kind="github.ci_failed" if failed else "github.ci_passed", dedup_key=key,
                                occurred_at=_ts(at), repo=repo, pr_numbers=prs, branch=branch, head_sha=sha,
                                title=f"CI '{name}' {'failed' if failed else 'passed'} on {branch or sha}",
                                tickets=find_tickets(pattern, branch, label), actor=sender, url=run.get("html_url"),
                                check_name=name, conclusion=conclusion)

    if event == "status":
        state = payload.get("state")
        if state not in ("success", "failure", "error"):
            return None
        branches = [b.get("name") for b in payload.get("branches") or []]
        failed = state != "success"
        return EngineeringEvent(kind="github.ci_failed" if failed else "github.ci_passed",
                                dedup_key=f"gh:status:{repo}:{payload.get('id')}:{state}",
                                occurred_at=_ts(payload.get("updated_at")), repo=repo, branch=branches[0] if branches else None,
                                head_sha=payload.get("sha"), check_name=payload.get("context"), conclusion=state,
                                title=f"CI '{payload.get('context')}' {'failed' if failed else 'passed'}",
                                tickets=find_tickets(pattern, *branches, payload.get("description")), actor=sender,
                                url=payload.get("target_url"))
    return None


def demo_payload(kind: str, *, repo: str, ticket: str, pr_number: int, title: str, branch: str | None = None,
                 check_name: str = "ci / test", run_id: int | None = None, actor: str = "priya-raman",
                 at: datetime | None = None) -> tuple[str, dict[str, Any]]:
    """Build a realistic GitHub payload. kind: pr_opened | pr_merged | ci_failed | ci_passed."""
    at_s = (at or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    branch = branch or f"{ticket.lower()}-{re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:30]}"
    sha = hashlib.sha1(f"{branch}:{run_id}:{kind}".encode()).hexdigest()
    repository = {"full_name": repo, "html_url": f"https://github.com/{repo}"}
    if kind in ("pr_opened", "pr_merged"):
        merged = kind == "pr_merged"
        pr = {"number": pr_number, "title": f"{ticket}: {title}", "body": f"Implements {ticket}.",
              "html_url": f"https://github.com/{repo}/pull/{pr_number}", "user": {"login": actor},
              "head": {"ref": branch, "sha": sha}, "base": {"ref": "main"}, "created_at": at_s, "updated_at": at_s,
              "merged": merged, "merged_at": at_s if merged else None, "closed_at": at_s if merged else None,
              "merged_by": {"login": "vikram-rao"} if merged else None}
        return "pull_request", {"action": "closed" if merged else "opened", "number": pr_number, "pull_request": pr,
                                "repository": repository, "sender": {"login": actor}}
    if kind in ("ci_failed", "ci_passed"):
        run_id = run_id or int(hashlib.sha1(f"{branch}{at_s}".encode()).hexdigest()[:8], 16)
        run = {"id": run_id, "name": check_name, "head_branch": branch, "head_sha": sha, "run_attempt": 1,
               "status": "completed", "conclusion": "failure" if kind == "ci_failed" else "success",
               "display_title": f"{ticket}: {title}", "html_url": f"https://github.com/{repo}/actions/runs/{run_id}",
               "run_started_at": at_s, "updated_at": at_s,
               "pull_requests": [{"number": pr_number, "head": {"ref": branch}, "base": {"ref": "main"}}]}
        return "workflow_run", {"action": "completed", "workflow_run": run, "repository": repository,
                                "sender": {"login": actor}}
    raise ValueError(f"unknown demo event kind {kind!r}")
