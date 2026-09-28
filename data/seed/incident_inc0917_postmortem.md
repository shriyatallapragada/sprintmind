# Postmortem — INC-0917: API gateway 503s during Acme bulk import
Severity: Sev-2 · Duration: 41 minutes · Incident commander: Vikram Rao · Scribe: Daniel Okafor

## Summary
Acme Corp ran an unannounced bulk import of ~380,000 shipment records through `POST /v2/shipments/bulk`
starting 10:12 IST. Traffic from the Acme tenant peaked at ~2,900 requests/min. Our gateway only had a
**global** rate limit (5,000 req/min across all tenants), so Acme's burst consumed most of the shared
capacity and the `orders-api` pods hit their connection-pool limit (`HikariPool-1 - Connection is not
available, request timed out after 30000ms`). Other tenants saw HTTP 503s for 41 minutes.

## Timeline (IST)
- 10:12 Acme import starts.
- 10:19 PagerDuty alert: 5xx rate 7.8% on api-gateway.
- 10:31 Arjun Mehta scales `orders-api` from 6 to 12 pods; partial recovery.
- 10:44 Priya Raman adds a temporary nginx `limit_req` rule for the Acme tenant key at 600 req/min.
- 10:53 Error rate back under 0.2%.

## Root cause
No per-tenant rate limiting. A single tenant could exhaust shared capacity. Clients also received 503
instead of 429, so Acme's importer retried immediately and made it worse (no `Retry-After` header).

## Action items
- NW-231 — Per-tenant rate limiting in the gateway (token bucket in Redis), returning **429 with a
  `Retry-After` header**. Owner: Priya Raman. Target: Sprint 14. Priority P1.
- NW-252 — Grafana dashboard for per-tenant request rate and 429 counts. Owner: Arjun Mehta. Sprint 14.
- Ask Acme to announce bulk imports 24h in advance until NW-231 ships. Owner: Neha Kulkarni. Done.
- The temporary nginx rule (600 req/min for Acme) stays until NW-231 is live in production.
