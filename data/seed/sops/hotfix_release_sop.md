# SOP-004 — Hotfix Release Procedure
Owner: Vikram Rao (Tech Lead) · Applies to: all services deployed to `prod-ap-south-1` · Last reviewed by Neha Kulkarni

## When this applies
A hotfix is any change shipped to production outside the normal Thursday release train to fix a
Sev-1 or Sev-2 issue, or a customer-blocking defect that a customer has escalated in writing.

## Steps
1. Open a Jira ticket with the `hotfix` label and link the incident (INC-xxxx) or customer escalation.
2. Branch from the **current production tag** (e.g. `release/2026.38.2`), never from `main`. Name it `hotfix/NW-<ticket>-<short-desc>`.
3. Keep the diff minimal: no refactors, no dependency bumps, no migrations. Schema changes are never allowed in a hotfix — use SOP-007 instead.
4. The PR needs **two approvals**, one of which must be the on-call tech lead (Vikram Rao, or Priya Raman as backup).
5. CI must be fully green including the `contract-tests` and `smoke-prod-like` jobs. Do not re-run flaky jobs more than once; if it fails twice, page QA (Daniel Okafor).
6. Deploy to `staging` first and run `make smoke ENV=staging`. Minimum soak: 30 minutes.
7. Production deploy uses a **canary: 10% of traffic for 15 minutes**, watching the error-rate and p95 latency panels on the "API Gateway / Tenants" Grafana dashboard. Abort if 5xx rate > 0.5% or p95 > 800 ms.
8. Promote to 100% and tag the release `release/<year>.<week>.<patch+1>`.
9. Cherry-pick the fix back into `main` within 24 hours and link the PR in the ticket.
10. Post in `#platform-releases` and, if a customer was affected, the account owner sends the customer a written confirmation the same day.

## Freeze rules
- No hotfixes Friday after 17:00 IST or on weekends unless it is a Sev-1, and then only with Neha Kulkarni's written approval in `#platform-incidents`.
- Rollback is always `helm rollback <service> <previous-revision>`; you do not need approval to roll back.
