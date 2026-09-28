Sprint 14 Planning — Northwind Platform Team (MS Teams, 58 min)
Attendees: Neha Kulkarni, Vikram Rao, Priya Raman, Rahul Verma, Arjun Mehta, Sara Kim, Daniel Okafor
Sprint length: 2 weeks (10 working days). Capacity: 62 story points.

Neha Kulkarni: Sprint goal is "Make Acme production-ready at scale": rate limiting from the INC-0917 postmortem, SSO, the orders DB upgrade, and the audit export Acme's compliance team keeps asking about.

Committed tickets:
- NW-231 Per-tenant API rate limiting (Redis token bucket, 429 + Retry-After). Owner: Priya Raman. 8 pts. P1. Reviewer: Vikram Rao.
- NW-236 Okta SAML SSO for the Acme tenant. Owner: Rahul Verma. 5 pts. P1. Needs Acme's IdP metadata XML.
- NW-240 Postgres 14 → 16 upgrade and monthly partitioning of the `orders` table (1.3 billion rows, 410 GB). Owner: Arjun Mehta. 13 pts. P1. Will likely need a customer maintenance window per SOP-007.
- NW-244 Audit log CSV export (API + admin UI). Owner: Sara Kim (UI) with Rahul Verma on the API endpoint. 8 pts. P2.
- NW-247 Fix flaky checkout end-to-end tests in CI (currently ~14% flake rate). Owner: Daniel Okafor. 5 pts. P2.
- NW-249 Webhook delivery retries with exponential backoff. Owner: Rahul Verma. 5 pts. P2. Start after SSO.
- NW-252 Grafana dashboard for per-tenant request rates and 429s. Owner: Arjun Mehta. 3 pts. P3.

Vikram Rao: Rate limiting and the migration are the risky ones. Priya, keep NW-231 behind the flag ff_nw231_tenant_ratelimit so we can turn it on per tenant.
Arjun Mehta: The last full dry-run of the orders table rewrite took almost an hour, so I'm assuming we need a window. I'll email Acme once I have a real number.
Daniel Okafor: I'll need the rate limiter on staging by mid-sprint to write the load test.
Neha Kulkarni: Acme sync is weekly; bring anything that needs their approval to that call. Sprint review is on the last Friday.
