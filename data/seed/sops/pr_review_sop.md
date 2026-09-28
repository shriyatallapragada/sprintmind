# SOP-002 — Pull Request & Code Review Standard
Owner: Vikram Rao (Tech Lead)

1. PR titles start with the ticket id: `NW-231: per-tenant rate limiting`.
2. Keep PRs under ~400 changed lines; split larger work behind a feature flag (we use Unleash, flags named `ff_<ticket>_<desc>`).
3. Every PR touching the public API must update the OpenAPI spec in `api/openapi.yaml` and add a contract test.
4. At least one approval for normal PRs; two for anything touching auth, billing, rate limiting, or migrations.
5. Reviewers respond within one working day. If a PR waits more than 24 hours, raise it in standup.
6. Squash-merge only. The merge commit message must reference the ticket.
7. A ticket moves to **Done** only when the change is merged to `main`, deployed to staging, and QA (Daniel Okafor) has signed off the acceptance criteria.
