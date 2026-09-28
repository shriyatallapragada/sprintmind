# SOP-007 — Production Database Migration
Owner: Arjun Mehta (DevOps / DB) · Applies to: `orders-db`, `tenants-db` (Postgres, AWS RDS)

## Before the migration
1. Every migration is a reviewed Flyway script in `db/migrations/`, reviewed by Arjun Mehta **and** the Tech Lead.
2. Run a full dry-run against the latest **staging snapshot restored from production**; record the wall-clock time in the ticket.
3. If the dry-run takes longer than **15 minutes** or takes an `ACCESS EXCLUSIVE` lock on a table larger than 5 GB, it needs a maintenance window.
4. Maintenance windows affecting a customer tenant require **written approval from the customer** (email or meeting minutes). No approval, no migration — mark the ticket Blocked.
5. A rollback plan document must be shared with the affected customer at least **48 hours before** the window.
6. Take a manual RDS snapshot named `pre-<ticket>-<yyyymmdd>` immediately before starting.

## During the migration
7. Announce start/finish in `#platform-releases` and set the status page to "Scheduled maintenance".
8. Two people on the call: the migration owner and a second engineer who can execute the rollback.
9. Watch replication lag; abort if lag exceeds 60 seconds for more than 5 minutes.

## After the migration
10. Run `make verify-migration TICKET=NW-xxx` and the orders smoke suite.
11. Keep the pre-migration snapshot for 14 days.
12. Update the ticket with actual duration and any deviations from the plan.
