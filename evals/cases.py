"""Evaluation cases for SprintMind's delivery memory.

Each case replays a short, realistic history through the real HTTP API (ingest, GitHub webhook with
signatures, dashboard edits, approvals, corrections) and then checks:

  * state / risk / history / dependency / event checks: deterministic, always run
  * answer checks: need a real LLM (`python -m evals.run --llm`); they measure factual correctness,
    temporal correctness, abstention on unknowns and whether citations point at supporting evidence

Documents carry their gold extraction (`items`) so ingestion is deterministic in both modes; the LLM
is only exercised for answers. Dates are written relative to today:
  "bdays_ago": 3            the document happened 3 business days ago (10:00 team time)
  "hours_ago": 2            ... or 2 hours ago
  "{+2}" / "{-1}"           a due date 2 business days ahead / 1 business day ago
"""
from __future__ import annotations

TEAM_NAMES = ["Neha Kulkarni", "Vikram Rao", "Priya Raman", "Rahul Verma", "Arjun Mehta", "Sara Kim", "Daniel Okafor"]


def doc(source, title, items, *, content=None, bdays_ago=None, hours_ago=None, customer=None):
    return {"doc": {"source": source, "title": title, "items": items, "customer": customer,
                    "content": content or "\n".join(f"{i.get('owner') or ''}: {i.get('title')} {i.get('detail') or ''}".strip()
                                                    for i in items),
                    "bdays_ago": bdays_ago, "hours_ago": hours_ago}}


def gh(kind, ticket, pr, *, hours_ago=0, run_id=None, delivery=None):
    return {"github": {"kind": kind, "ticket": ticket, "pr": pr, "hours_ago": hours_ago, "run_id": run_id, "delivery": delivery}}


SANDBOX = {"kind": "commitment", "ticket": "NW-231", "customer": "Acme Corp", "owner": "Priya Raman", "status": "in_progress",
           "title": "Acme sandbox tenant at 1,200 req/min (burst 200, integer Retry-After)", "due": "{+2}"}

CASES: list[dict] = [
    # ------------------------------------------------ historical retrieval
    {"id": "hist-commitment", "category": "historical_retrieval",
     "steps": [doc("meeting", "Acme Corp weekly sync", [SANDBOX], customer="Acme Corp", bdays_ago=1,
                   content="Lisa Moreno: we need 1,200 requests per minute with bursts of 200 and integer Retry-After. "
                           "Priya Raman: I'll have the sandbox tenant configured under NW-231.")],
     "checks": [{"state": {"kind": "commitment", "field": "owner_id", "equals": "priya"}},
                {"state": {"kind": "commitment", "field": "due", "equals": "{+2}"}},
                {"answer": {"question": "What did we commit to Acme for their sandbox, who owns it and when is it due?",
                            "expect": [["1,200", "1200"], ["Priya"], ["{+2}"]], "cite_support": ["1,200|1200"]}}]},
    {"id": "hist-planning-owner", "category": "historical_retrieval",
     "steps": [doc("meeting", "Sprint 14 Planning", [
         {"kind": "task", "ticket": "NW-236", "title": "Okta SAML SSO for the Acme tenant", "owner": "Rahul Verma", "priority": "P1", "status": "todo"},
         {"kind": "task", "ticket": "NW-252", "title": "Grafana tenant dashboard", "owner": "Arjun Mehta", "priority": "P3", "status": "todo"}], bdays_ago=10)],
     "checks": [{"state": {"item": "tkt-NW-236", "field": "owner", "equals": "Rahul Verma"}},
                {"answer": {"question": "Who owns NW-236 and what is it?", "expect": [["Rahul"], ["SSO", "SAML"]],
                            "cite_support": ["NW-236"]}}]},
    {"id": "hist-sop-citation", "category": "citation_accuracy",
     "steps": [doc("sop", "SOP-004 Hotfix Release Procedure", [
         {"kind": "sop_step", "title": "Hotfix canary", "detail": "Deploy hotfixes to a 10% canary for 15 minutes before full rollout"},
         {"kind": "sop_step", "title": "Hotfix approval", "detail": "A hotfix needs one approving review from the tech lead"}], bdays_ago=60)],
     "checks": [{"answer": {"question": "According to our hotfix SOP, what is the canary step?", "expect": [["10%", "10 %"], ["15 min"]],
                            "cite_support": ["canary"]}}]},

    # ------------------------------------------------ newer updates override older ones
    {"id": "override-jira-over-standup", "category": "temporal_override",
     "steps": [doc("standup", "Daily standup", [{"kind": "status_update", "ticket": "NW-244", "title": "Audit export API", "owner": "Rahul Verma", "status": "in_progress"}], bdays_ago=3),
               doc("task_update", "Jira digest", [{"kind": "status_update", "ticket": "NW-244", "title": "Audit export API", "status": "in_review"}], bdays_ago=1)],
     "checks": [{"state": {"item": "tkt-NW-244", "field": "status", "equals": "in_review"}},
                {"history": {"item": "tkt-NW-244", "field": "status", "values": ["in_review", "in_progress"]}},
                {"answer": {"question": "What is the current status of NW-244?", "expect": [["review"]], "temporal": True,
                            "cite_support": ["review"]}}]},
    {"id": "override-github-beats-meeting", "category": "temporal_override",
     "steps": [doc("standup", "Daily standup", [{"kind": "status_update", "ticket": "NW-236", "title": "Okta SSO", "owner": "Rahul Verma", "status": "in_progress"}], bdays_ago=3),
               gh("pr_merged", "NW-236", 479, hours_ago=20),
               doc("meeting", "Acme Corp weekly sync", [{"kind": "status_update", "ticket": "NW-236", "title": "Okta SSO", "status": "in_progress"}], hours_ago=2,
                   content="Rahul Verma: SSO is still in progress on our side.")],
     "checks": [{"state": {"item": "tkt-NW-236", "field": "status", "equals": "done"}},
                {"not_applied": {"item": "tkt-NW-236", "field": "status", "value": "in_progress"}},
                {"answer": {"question": "Is NW-236 SSO finished?", "expect": [["merged", "done", "complete"]], "forbid": ["is still in progress"],
                            "temporal": True, "cite_support": ["merged|done"]}}]},
    {"id": "override-stale-late-arrival", "category": "temporal_override",
     "steps": [doc("task_update", "Jira digest (today)", [{"kind": "status_update", "ticket": "NW-247", "title": "Flaky checkout tests", "owner": "Daniel Okafor", "status": "done"}], hours_ago=3),
               doc("standup", "Daily standup (last week, imported late)", [{"kind": "status_update", "ticket": "NW-247", "title": "Flaky checkout tests", "status": "in_progress"}], bdays_ago=5)],
     "checks": [{"state": {"item": "tkt-NW-247", "field": "status", "equals": "done"}},
                {"not_applied": {"item": "tkt-NW-247", "field": "status", "value": "in_progress"}}]},

    # ------------------------------------------------ blockers
    {"id": "blocker-created", "category": "blockers",
     "steps": [doc("standup", "Daily standup", [{"kind": "blocker", "ticket": "NW-240", "title": "Postgres 16 upgrade", "owner": "Arjun Mehta",
                                                 "blocked_on": "Acme's written approval of the maintenance window"}], bdays_ago=2)],
     "checks": [{"state": {"item": "tkt-NW-240", "field": "status", "equals": "blocked"}},
                {"dependency": {"item": "tkt-NW-240", "kind": "approval", "status": "open"}},
                {"risk": {"item": "tkt-NW-240", "rule": "pending_approval", "open": True}},
                {"answer": {"question": "What is blocking NW-240?", "expect": [["approval"], ["Acme"], ["maintenance window"]],
                            "cite_support": ["approval"]}}]},
    {"id": "blocker-resolved-by-approval", "category": "customer_approvals",
     "steps": [doc("standup", "Daily standup", [{"kind": "blocker", "ticket": "NW-240", "title": "Postgres 16 upgrade", "owner": "Arjun Mehta",
                                                 "blocked_on": "Acme's written approval of the maintenance window"}], bdays_ago=3),
               doc("meeting", "Acme Corp weekly sync", [{"kind": "decision", "title": "Acme approved the maintenance window for Sunday 3-5 AM IST"}],
                   customer="Acme Corp", bdays_ago=1, content="Tom Becker: Sunday 3 to 5 AM IST is approved. Consider this the written approval.")],
     "checks": [{"dependency": {"item": "tkt-NW-240", "kind": "approval", "status": "resolved"}},
                {"state": {"item": "tkt-NW-240", "field": "status", "equals": "todo"}},
                {"risk": {"item": "tkt-NW-240", "rule": "pending_approval", "open": False}},
                {"answer": {"question": "Is NW-240 still blocked on Acme's approval?", "expect": [["approved", "no longer", "unblocked", "not blocked"]],
                            "forbid": ["is still blocked"], "temporal": True, "cite_support": ["approv"]}}]},
    {"id": "approval-pending-negation", "category": "customer_approvals",
     "steps": [doc("standup", "Daily standup", [{"kind": "blocker", "ticket": "NW-240", "title": "Postgres 16 upgrade", "owner": "Arjun Mehta",
                                                 "blocked_on": "Acme's written approval of the maintenance window"}], bdays_ago=3),
               doc("standup", "Daily standup", [{"kind": "decision", "title": "Maintenance window approval still pending from Acme"}], bdays_ago=1)],
     "checks": [{"dependency": {"item": "tkt-NW-240", "kind": "approval", "status": "open"}},
                {"state": {"item": "tkt-NW-240", "field": "status", "equals": "blocked"}}]},
    {"id": "approval-recorded-in-dashboard", "category": "customer_approvals",
     "steps": [doc("meeting", "Acme Corp weekly sync", [{"kind": "commitment", "title": "Enforce SSO for all acme.com users", "owner": "Rahul Verma",
                                                          "due": "{+8}", "customer": "Acme Corp", "status": "todo"}], customer="Acme Corp", bdays_ago=1),
               {"add_dependency": {"kind": "commitment", "description": "Acme sign-off on the enforcement date", "type": "approval"}},
               {"approve": {"kind": "commitment", "note": "Lisa Moreno confirmed the date by email"}}],
     "checks": [{"dependency": {"kind": "commitment", "type": "approval", "status": "resolved"}},
                {"events": {"kind": "approval.granted", "count": 1}},
                {"risk": {"kind": "commitment", "rule": "pending_approval", "open": False}}]},
    {"id": "dependency-between-tickets", "category": "blockers",
     "steps": [doc("standup", "Daily standup", [{"kind": "task", "ticket": "NW-236", "title": "Okta SSO", "owner": "Rahul Verma", "status": "in_review"},
                                                {"kind": "blocker", "ticket": "NW-249", "title": "Webhook retries", "owner": "Rahul Verma", "due": "{+4}",
                                                 "blocked_on": "NW-236 SSO must merge first"}], bdays_ago=1),
               gh("pr_merged", "NW-236", 479, hours_ago=1)],
     "checks": [{"dependency": {"item": "tkt-NW-249", "kind": "item", "status": "resolved"}},
                {"state": {"item": "tkt-NW-249", "field": "status", "equals": "todo"}},
                {"risk": {"item": "tkt-NW-249", "rule": "dependency_incomplete", "open": False}}]},

    # ------------------------------------------------ deadline and ownership changes
    {"id": "deadline-change", "category": "deadline_ownership",
     "steps": [doc("meeting", "Acme Corp weekly sync", [{"kind": "commitment", "title": "Audit export demo for Acme compliance", "owner": "Sara Kim",
                                                          "due": "{+2}", "customer": "Acme Corp", "status": "todo"}], customer="Acme Corp", bdays_ago=2),
               doc("meeting", "Acme follow-up call", [{"kind": "commitment", "title": "Audit export demo for Acme compliance", "owner": "Sara Kim",
                                                        "due": "{+6}", "customer": "Acme Corp", "status": "todo"}], customer="Acme Corp", bdays_ago=1,
                   content="Lisa Moreno: let's move the audit export demo to the following week.")],
     "checks": [{"state": {"kind": "commitment", "field": "due", "equals": "{+6}"}},
                {"history": {"kind": "commitment", "field": "due", "values": ["{+6}", "{+2}"]}},
                {"answer": {"question": "When is the Acme audit export demo due now?", "expect": [["{+6}"]], "temporal": True,
                            "cite_support": ["audit export"]}}]},
    {"id": "owner-change", "category": "deadline_ownership",
     "steps": [doc("meeting", "Acme Corp weekly sync", [{"kind": "commitment", "title": "Add actor IP and user agent to the audit export API", "owner": "Sara Kim",
                                                          "due": "{+5}", "customer": "Acme Corp", "status": "todo"}], customer="Acme Corp", bdays_ago=2),
               {"update": {"kind": "commitment", "owner": "rahul", "note": "Rahul owns the API side", "as": "neha"}}],
     "checks": [{"state": {"kind": "commitment", "field": "owner_id", "equals": "rahul"}},
                {"history": {"kind": "commitment", "field": "owner", "values": ["Rahul Verma", "Sara Kim"]}},
                {"answer": {"question": "Who owns adding actor IP and user agent to the Acme audit export API?", "expect": [["Rahul"]],
                            "temporal": True, "cite_support": ["Rahul"]}}]},

    # ------------------------------------------------ CI failures and recoveries
    {"id": "ci-failure-risk", "category": "ci",
     "steps": [doc("meeting", "Acme Corp weekly sync", [SANDBOX], customer="Acme Corp", hours_ago=4),
               gh("ci_failed", "NW-231", 488, hours_ago=1, run_id=901)],
     "checks": [{"risk": {"kind": "commitment", "rule": "ci_failing", "open": True, "severity": "high"}},
                {"risk_evidence": {"kind": "commitment", "rule": "ci_failing", "source": "github"}},
                {"answer": {"question": "Is the Acme sandbox commitment at risk? Why?", "expect": [["CI", "check", "build"], ["fail"]],
                            "cite_support": ["fail"]}}]},
    {"id": "ci-recovery", "category": "ci",
     "steps": [doc("meeting", "Acme Corp weekly sync", [SANDBOX], customer="Acme Corp", hours_ago=5),
               gh("ci_failed", "NW-231", 488, hours_ago=3, run_id=902),
               gh("ci_passed", "NW-231", 488, hours_ago=1, run_id=903)],
     "checks": [{"risk": {"kind": "commitment", "rule": "ci_failing", "open": False}},
                {"risk_history": {"kind": "commitment", "rule": "ci_failing", "changes": ["opened", "resolved"]}},
                {"answer": {"question": "What is the latest CI state for the Acme sandbox work on NW-231?", "expect": [["pass", "green", "success"]],
                            "forbid": ["currently failing", "is failing"], "temporal": True, "cite_support": ["pass"]}}]},
    {"id": "ci-webhook-dedup", "category": "duplicates",
     "steps": [doc("meeting", "Acme Corp weekly sync", [SANDBOX], customer="Acme Corp", hours_ago=4),
               gh("ci_failed", "NW-231", 488, hours_ago=1, run_id=904, delivery="a"),
               gh("ci_failed", "NW-231", 488, hours_ago=1, run_id=904, delivery="b")],
     "checks": [{"events": {"source": "github", "count": 1}},
                {"events": {"kind": "risk.opened", "rule": "ci_failing", "count": 1}}]},
    {"id": "duplicate-ingestion", "category": "duplicates",
     "steps": [doc("standup", "Daily standup", [{"kind": "status_update", "ticket": "NW-231", "title": "Rate limiting", "owner": "Priya Raman", "status": "in_review"}], bdays_ago=1),
               doc("standup", "Daily standup", [{"kind": "status_update", "ticket": "NW-231", "title": "Rate limiting", "owner": "Priya Raman", "status": "in_review"}], bdays_ago=1)],
     "checks": [{"events": {"kind": "statement.", "count": 1}},
                {"history": {"item": "tkt-NW-231", "field": "status", "values": ["in_review"]}},
                {"memories": {"document_contains": "Daily standup", "max": 1}}]},

    # ------------------------------------------------ conflicting sources
    {"id": "conflict-standup-vs-github", "category": "conflicts",
     "steps": [gh("pr_opened", "NW-252", 495, hours_ago=6),
               doc("standup", "Daily standup", [{"kind": "status_update", "ticket": "NW-252", "title": "Grafana tenant dashboard", "owner": "Arjun Mehta", "status": "done"}], hours_ago=1)],
     "checks": [{"state": {"item": "tkt-NW-252", "field": "status", "equals": "in_review"}},
                {"risk": {"item": "tkt-NW-252", "rule": "conflicting_evidence", "open": True}},
                {"answer": {"question": "Is NW-252 done?", "expect": [["review", "not done", "not yet", "conflict", "disagree"]],
                            "forbid": ["NW-252 is done"], "temporal": True, "cite_support": ["NW-252"]}}]},

    # ------------------------------------------------ missing information and abstention
    {"id": "missing-owner", "category": "missing_information",
     "steps": [doc("meeting", "Acme Corp weekly sync", [{"kind": "commitment", "title": "Share an audit export sample file with Acme compliance",
                                                          "customer": "Acme Corp", "status": "todo"}], customer="Acme Corp", bdays_ago=1)],
     "checks": [{"risk": {"kind": "commitment", "rule": "missing_owner", "open": True, "risk_kind": "missing_info"}},
                {"no_real_risks": True},
                {"answer": {"question": "Who owns sharing the audit export sample file with Acme compliance, and when is it due?",
                            "abstain": True, "forbid": TEAM_NAMES}}]},
    {"id": "missing-due", "category": "missing_information",
     "steps": [doc("meeting", "Sprint 14 Planning", [{"kind": "task", "ticket": "NW-252", "title": "Grafana dashboard for per-tenant request rates",
                                                      "owner": "Arjun Mehta", "status": "todo"}], bdays_ago=5)],
     "checks": [{"state": {"item": "tkt-NW-252", "field": "due", "equals": None}},
                {"answer": {"question": "What is the due date for NW-252, the Grafana dashboard?", "abstain": True,
                            "forbid": ["due on", "due by", "is due"]}}]},
    {"id": "unknown-customer", "category": "missing_information",
     "steps": [doc("meeting", "Acme Corp weekly sync", [SANDBOX], customer="Acme Corp", bdays_ago=1)],
     "checks": [{"answer": {"question": "What did Globex Corporation ask for in their kickoff call?", "abstain": True,
                            "forbid": ["Globex asked for", "Globex requested", "Globex wants"]}}]},

    # ------------------------------------------------ deadlines
    {"id": "overdue", "category": "deadline_ownership",
     "steps": [doc("meeting", "Acme Corp weekly sync", [{"kind": "commitment", "title": "Send the NW-240 rollback plan to Acme", "owner": "Arjun Mehta",
                                                          "due": "{-2}", "customer": "Acme Corp", "status": "todo"}], customer="Acme Corp", bdays_ago=4)],
     "checks": [{"risk": {"kind": "commitment", "rule": "overdue", "open": True, "severity": "high"}},
                {"answer": {"question": "Is the rollback plan for Acme late?", "expect": [["overdue", "late", "past", "missed"]],
                            "cite_support": ["rollback"]}}]},
    {"id": "stale-progress", "category": "deadline_ownership",
     "steps": [doc("standup", "Daily standup", [{"kind": "task", "title": "Tenant usage report for Acme QBR", "owner": "Sara Kim",
                                                 "due": "{+5}", "status": "in_progress"}], bdays_ago=6)],
     "checks": [{"risk": {"kind": "task", "rule": "stale_progress", "open": True}}]},

    # ------------------------------------------------ corrections
    {"id": "correction-overrides", "category": "corrections",
     "steps": [doc("standup", "Daily standup", [{"kind": "status_update", "ticket": "NW-231", "title": "Rate limiting", "owner": "Priya Raman", "status": "in_progress"}], bdays_ago=2),
               {"correction": {"as": "priya", "question": "Is NW-231 done?", "text": "NW-231 is done, it was merged to main yesterday",
                               "items": [{"kind": "status_update", "ticket": "NW-231", "title": "Rate limiting", "status": "done"}]}}],
     "checks": [{"state": {"item": "tkt-NW-231", "field": "status", "equals": "done"}},
                {"state": {"item": "tkt-NW-231", "field": "status_source", "equals": "feedback"}},
                {"answer": {"question": "What is the status of NW-231?", "expect": [["done", "merged", "complete"]], "temporal": True,
                            "cite_support": ["done|merged"]}}]},

    # ------------------------------------------------ access control
    {"id": "private-interactions", "category": "access_control",
     "steps": [{"ask": {"as": "arjun", "person": "arjun", "question": "Draft my salary review talking points about the migration"}}],
     "checks": [{"visible": {"as": "priya", "question": "salary review talking points migration", "contains": "Arjun Mehta asked", "expect": False}},
                {"visible": {"as": "neha", "question": "salary review talking points migration", "contains": "Arjun Mehta asked", "expect": True}}]},
]
