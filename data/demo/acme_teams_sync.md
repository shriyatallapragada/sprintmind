Microsoft Teams meeting transcript — "Acme Corp <> Northwind weekly sync"
Duration: 42m 17s
Participants: Tom Becker (Acme, VP Engineering), Lisa Moreno (Acme, Integration Lead), Neha Kulkarni, Vikram Rao, Priya Raman, Rahul Verma, Arjun Mehta, Sara Kim

0:00:04 Neha Kulkarni: Thanks for joining, Tom, Lisa. Four things today: rate limits, the database maintenance window, audit export, and SSO.

0:01:12 Tom Becker: Let me start with rate limits since that incident last month hurt us too. Our quarter-end bulk imports are coming. What's the plan?
0:01:40 Priya Raman: Per-tenant limiting is live in production for internal tenants. Default is 600 requests per minute with a burst of 100. Over the limit you get a 429 with a Retry-After header instead of the 503s you saw.
0:02:31 Lisa Moreno: 600 is too low for us. Our importer needs 1,200 requests per minute sustained, with bursts up to 200. And we need Retry-After in seconds, not an HTTP date, our client library only parses integers.
0:03:05 Priya Raman: Limits are configurable per tenant now, so 1,200 with a burst of 200 is doable. I'll make Retry-After integer seconds. I'll set up your sandbox tenant with those limits by this Friday so your team can test before quarter end.
0:03:30 Tom Becker: Good. Friday for the sandbox, that's a commitment then.
0:03:41 Vikram Rao: Once Acme signs off on the sandbox we turn it on in production for the Acme tenant, and the temporary nginx rule from INC-0917 goes away.

0:06:10 Neha Kulkarni: Next, Arjun's database upgrade.
0:06:22 Arjun Mehta: We need about 25 minutes of downtime for the Postgres 16 upgrade and the orders partition swap. We proposed Saturday 2 to 4 AM IST.
0:07:15 Tom Becker: Saturday doesn't work, we run payroll exports Saturday night. Sunday 3 to 5 AM IST is approved. Consider this the written approval, Lisa will confirm by email today.
0:07:48 Lisa Moreno: One condition: we need the rollback plan document 48 hours before, so by Friday 3 AM IST at the latest.
0:08:02 Arjun Mehta: Understood. I'll send the rollback plan Thursday and run the migration Sunday 3 AM with Vikram as second engineer.

0:11:30 Neha Kulkarni: Audit export. Sara?
0:11:45 Sara Kim: CSV export is working on staging for up to 250,000 rows. Two small QA fixes left.
0:12:20 Lisa Moreno: Our compliance team reviewed the sample. They need two more columns: actor IP address and the user agent. And all timestamps in UTC ISO-8601, not local time. Also they want exports limited to 90 days per file so the files stay manageable.
0:13:02 Sara Kim: Adding actor IP and user agent means an API change too. Rahul, can the endpoint include those?
0:13:15 Rahul Verma: Yes, we already log both. I'll add them to /v2/audit/export this week.
0:13:40 Tom Becker: Can you demo it on our next sync?
0:13:48 Neha Kulkarni: Yes, audit export demo at next week's sync. Sara owns the demo.

0:17:05 Neha Kulkarni: Last, SSO.
0:17:20 Rahul Verma: Okta SAML SSO is on staging. Lisa's team tested it with your test IdP last week.
0:17:52 Tom Becker: We'll want SSO enforced for everyone on the acme.com domain, no password logins, plus just-in-time user provisioning so new hires don't need manual accounts.
0:18:30 Neha Kulkarni: Enforcement is small; JIT provisioning is new scope. We'll plan it for Sprint 15, Rahul will own it. I'll confirm the scope by email.
0:19:02 Lisa Moreno: Fine with Sprint 15, but we'd like SSO enforcement itself before the end of this month.

0:21:10 Lisa Moreno: One more, webhooks. When our endpoint is down for maintenance we lose events. How long do you retry?
0:21:35 Rahul Verma: Retries are in review right now: backoff up to 6 hours, then a dead-letter queue you can replay from.
0:21:58 Lisa Moreno: We need at least a 24-hour retry window. Our maintenance can take a full day.
0:22:10 Rahul Verma: I'll extend the schedule to 24 hours before it merges.

0:40:12 Neha Kulkarni: Recap. Priya: Acme sandbox with 1,200 per minute, burst 200, integer Retry-After, by Friday. Arjun: rollback plan by Thursday, migration Sunday 3 to 5 AM IST. Sara and Rahul: audit export with IP, user agent, UTC, 90-day limit, demo next sync. Rahul: SSO enforcement this month, JIT in Sprint 15, and 24-hour webhook retries. Thanks all.
