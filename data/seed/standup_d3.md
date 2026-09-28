#platform-standup — Geekbot daily report

Priya Raman
Yesterday: Addressed Vikram's review on PR #482: limits are now per tenant in tenant settings (requests_per_minute, burst). Merged to main and deployed to staging.
Today: Pairing with Daniel on the k6 load test. Then docs for the new 429 behaviour.
Blockers: none

Rahul Verma
Yesterday: NW-236 SSO merged and deployed to staging. Waiting on Acme to test with their real Okta tenant.
Today: Starting NW-249 webhook retries (exponential backoff, jitter, dead-letter queue).
Blockers: none

Arjun Mehta
Yesterday: Sent the maintenance window request for NW-240 to Tom Becker and Lisa Moreno at Acme: proposed Saturday 02:00–04:00 IST.
Today: Can't schedule the migration until Acme replies.
Blockers: BLOCKED — NW-240 is waiting on Acme's written approval of the maintenance window (required by SOP-007). No reply yet.

Sara Kim
Yesterday: Audit export UI complete behind flag ff_nw244_audit_export.
Today: Integrating with Rahul's real /v2/audit/export endpoint; QA handoff to Daniel tomorrow.
Blockers: none

Daniel Okafor
Yesterday: k6 script ready.
Today: Load testing the rate limiter on staging at 600 req/min per tenant with 5 concurrent tenants.
Blockers: none
