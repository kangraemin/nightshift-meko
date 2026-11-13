# Acme Shop on-call runbook (team wiki, last edited 7 months ago)

General: acknowledge the page, check dashboards, look at recent deploys, then logs. Restarting pods buys time but is rarely the fix. Write a postmortem for anything that pages twice.

## checkout-api: 502s and high latency
Checkout talks to Postgres through pgbouncer. When the pool is saturated, requests queue and the load balancer returns 502. Check pool stats (cl_waiting, maxwait). If clients are waiting, raise the pool (F01 raise_db_pool) and kill idle-in-transaction sessions. Scaling checkout up makes pool pressure worse.

## auth: 5xx and TLS errors
Most auth outages so far were certificates. Check certificate expiry on auth.internal. Renew and reload (F02). Calendar reminders exist but are often ignored.

## notifications: delayed messages
Notifications are sent by consumers reading the notify.outbound queue. If the queue depth grows, check whether consumers are running. Crashed consumers must be restarted and scaled to drain (F03).

## orders-worker: errors after a release
If errors start right after a deploy, compare with the previous version and roll back (F04). Investigate on a branch later.

## inventory-svc: OOMKilled
inventory-svc is a JVM service. OOM kills usually mean the heap is configured larger than the container memory limit. Compare JAVA_OPTS with limits.memory; prefer MaxRAMPercentage (F05).

## payments: provider timeouts
Payments calls an external card provider. Timeouts often hide an auth failure on the provider side. Look for 401 responses in logs and rotate the provider API key from the vault (F07).

## orders-worker: write errors
Check disk usage on the worker hosts. Logs fill /var/log quickly when debug logging is left on. Rotate and compress logs (F11).

## Things we still need to document
search slowness, gateway throttling, scheduled jobs that silently stop, feature flags, flaky nodes.
