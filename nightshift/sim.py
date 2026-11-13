"""Deterministic incident simulator.

A tiny state machine that stands in for production. Each incident has one
true root cause. Eight read-only diagnostic commands return text; one of them
(sometimes two) carries the clue. `apply_fix` resolves the incident only when
the fix id *and* the service are right. Nothing here calls a model, so the
simulator is the judge: accuracy is never graded by an LLM.
"""
from __future__ import annotations

import os
import random
from dataclasses import dataclass, field

COMMANDS = [
    "get_metrics", "get_logs", "get_deploys", "get_config",
    "get_pool_stats", "get_dependencies", "get_certs", "get_queue",
]

FIXES = {
    "F01": "raise_db_pool <pool name>: raise the connection pool size and kill idle-in-transaction sessions",
    "F02": "renew_cert <certificate name>: renew and reload a TLS certificate",
    "F03": "restart_consumers <consumer group>: restart crashed queue consumers and scale them to drain",
    "F04": "rollback_deploy <version to roll back TO>: roll the service back to that release",
    "F05": "fix_heap_limit <configmap>: set -XX:MaxRAMPercentage=75 so the JVM heap fits the container",
    "F06": "flush_dns <hostname>: flush stale DNS so clients resolve the current database primary",
    "F07": "rotate_api_key <key name>: rotate a third-party API key from the vault",
    "F08": "add_index <index name>: recreate a missing database index",
    "F09": "disable_flag <flag name>: turn off a feature flag",
    "F10": "tune_rate_limit <config key>: restore a rate-limit setting to its documented value",
    "F11": "free_disk <path>: rotate and compress logs under a path",
    "F12": "release_lock <lock key>: release a stale leader lock",
    "F13": "restart_service <service>: restart all pods of the service",
    "F14": "scale_up <service>: add replicas to the service",
    "F15": "raise_timeout <service>: raise client timeouts",
    "F16": "drain_node <node name>: drain and cordon an unhealthy node",
}

SERVICES = ["checkout-api", "auth", "notifications", "orders-worker", "inventory-svc",
            "payments", "search", "gateway", "reports-cron",
            "pgbouncer", "postgres", "edge-lb", "notify-queue", "search-db", "k8s-nodes", "lock-store"]


@dataclass
class Cause:
    id: str
    service: str
    title: str
    alerts: list[str]          # paraphrases of the page that wakes someone up
    fix: str
    clues: dict[str, str]      # command -> output that reveals the cause
    target: str = ""            # what the fix must be pointed at; only visible in clues or memory
    v2_of: str | None = None   # trap: same symptom as this cause, different root cause
    paged: str = ""            # service that pages; the cause may live on an upstream


CAUSES: dict[str, Cause] = {c.id: c for c in [
    Cause("C01", "checkout-api", "pgbouncer pool exhaustion",
          ["checkout-api 502 rate {p}% and p99 {s}s", "checkout is throwing 502s, p99 latency {s}s",
           "502 Bad Gateway on checkout-api ({p}% of requests)"], "F01",
          {"get_pool_stats": "pool pgb-checkout-rw: cl_active=20 cl_waiting={w}0 sv_active=20 maxwait={s}s size=20",
           "get_metrics": "cpu=34% mem=51% db_wait_ms={d} http_502={p}% (waits on pgbouncer)"}, target="pgb-checkout-rw"),
    Cause("C02", "auth", "expired TLS certificate",
          ["auth 5xx spike, TLS handshake failures from clients", "login failing: auth returns 5xx, clients report SSL errors",
           "auth error rate {p}%, handshake errors in edge logs"], "F02",
          {"get_certs": "cert auth-edge-2026 (auth.internal) notAfter=yesterday 23:59 UTC  status=EXPIRED",
           "get_logs": "x509: certificate auth-edge-2026 has expired (x{w})"}, target="auth-edge-2026"),
    Cause("C03", "notifications", "queue backlog from crashed consumers",
          ["notifications delayed by {s} minutes", "push and email notifications are late (~{s} min)",
           "notification latency SLO breached, {s} min behind"], "F03",
          {"get_queue": "queue notify.outbound depth={w}00, consumer group notify-sender-v2: 0/6 running (CrashLoopBackOff)",
           "get_logs": "notify-sender-v2 exited: KeyError 'locale' (restarts={w})"}, target="notify-sender-v2"),
    Cause("C04", "orders-worker", "bad release",
          ["orders-worker error rate {p}% right after deploy", "orders-worker failing jobs since the last release",
           "spike of failed order jobs ({p}%) after deploy"], "F04",
          {"get_deploys": "orders-worker v2.{m}.0 deployed {s} min ago; previous release v2.{n}.0 was healthy",
           "get_logs": "NullPointerException at OrderMapper.toDto (release v2.{m}.0) x{w}"}, target="v2.{n}.0"),
    Cause("C05", "inventory-svc", "JVM heap larger than container limit",
          ["inventory-svc pods OOMKilled x{w} after deploy", "inventory-svc keeps restarting: OOMKilled",
           "inventory-svc OOMKilled, {w} restarts in 10 min"], "F05",
          {"get_config": "configmap inventory-jvm-opts: JAVA_OPTS=-Xmx3g ; container limits.memory=2Gi",
           "get_metrics": "mem=99% before kill, restarts={w}, heap_max=3072Mi > limit 2048Mi"}, target="inventory-jvm-opts"),
    Cause("C06", "payments", "expired payment-provider API key",
          ["payments timeouts calling provider, {p}% failed", "card payments failing ({p}%), provider calls time out",
           "payments provider errors and timeouts"], "F07",
          {"get_logs": "provider responded 401 invalid_api_key for key cardco-live-key-2 (x{w}); retries exhausted -> timeout",
           "get_dependencies": "provider.api: reachable, auth=FAILED (key cardco-live-key-2 expired)"}, target="cardco-live-key-2"),
    Cause("C07", "search", "missing index after migration",
          ["search p99 {s}s, users report slow results", "search is slow: p99 {s}s",
           "search latency alarm p99={s}s"], "F08",
          {"get_logs": "slow query {s}100ms: Seq Scan on products (rows=4.2M) filter=sku; expected idx_products_sku",
           "get_deploys": "migration 0142_drop_legacy_columns ran {s}h ago (dropped idx_products_sku)"}, target="idx_products_sku"),
    Cause("C08", "gateway", "wrong rate-limit config",
          ["gateway 429 spike, {p}% of requests throttled", "clients get HTTP 429 from gateway",
           "gateway throttling {p}% of traffic"], "F10",
          {"get_config": "edge-ratelimit-rps=50 (documented: 5000), changed {s}h ago by config-sync",
           "get_metrics": "rps=3900 throttled={p}% by edge-ratelimit-rps cpu=22%"}, target="edge-ratelimit-rps"),
    Cause("C09", "orders-worker", "disk full",
          ["orders-worker write errors, jobs stuck", "orders-worker: 'no space left' alerts",
           "orders-worker failing to persist job state"], "F11",
          {"get_metrics": "disk_used=100% on /var/log/orders (38G of debug logs) inode_used=41%",
           "get_logs": "ENOSPC: no space left on device /var/log/orders (x{w})"}, target="/var/log/orders"),
    Cause("C10", "reports-cron", "stale leader lock",
          ["daily reports job did not run", "reports-cron missed its 02:00 run",
           "no report emails today, reports-cron silent"], "F12",
          {"get_logs": "skip run: lock reports/leader held by node-3 since {s} days ago",
           "get_dependencies": "lock store: key=reports/leader owner=node-3 (node-3 terminated)"}, target="reports/leader"),
    Cause("C11", "checkout-api", "broken feature-flag rollout",
          ["checkout-api 502 rate {p}% and p99 {s}s", "checkout errors for about {p}% of users",
           "some users ({p}%) cannot complete checkout"], "F09",
          {"get_config": "flag new_pricing_engine rollout={p}% (enabled {s}h ago)",
           "get_logs": "PricingV2Exception: missing tax region (only for new_pricing_engine=on cohort)"}, target="new_pricing_engine"),
    Cause("C12", "gateway", "one unhealthy node",
          ["gateway intermittent 503s", "random 503s from gateway, about {p}%",
           "gateway 503 errors come and go"], "F16",
          {"get_dependencies": "upstreams healthy except node-{w}: error_rate={p}0% (others 0.1%)",
           "get_metrics": "503={p}% all from node-{w}, node-{w} disk latency {s}00ms"}, target="node-{w}"),
    # --- v2 traps: same pages, the world changed ---
    Cause("C01b", "checkout-api", "stale DNS after DB failover",
          ["checkout-api 502 rate {p}% and p99 {s}s", "checkout is throwing 502s, p99 latency {s}s",
           "502 Bad Gateway on checkout-api ({p}% of requests)"], "F06",
          {"get_dependencies": "db-primary.internal resolves to 10.0.3.14 (old primary, read-only since failover {s}h ago)",
           "get_logs": "ERROR: cannot execute UPDATE in a read-only transaction on db-primary.internal (x{w})",
           "get_pool_stats": "pool pgb-checkout-rw: cl_waiting=0 maxwait=0s (pool healthy)"}, target="db-primary.internal", v2_of="C01"),
    Cause("C05b", "inventory-svc", "memory leak in new release",
          ["inventory-svc pods OOMKilled x{w} after deploy", "inventory-svc keeps restarting: OOMKilled",
           "inventory-svc OOMKilled, {w} restarts in 10 min"], "F04",
          {"get_deploys": "inventory-svc v5.{m}.0 deployed {s} min ago ('cache all SKUs in memory'); previous v5.{n}.0 healthy",
           "get_metrics": "heap grows 40MB/min until kill, restarts={w}",
           "get_config": "configmap inventory-jvm-opts: JAVA_OPTS=-XX:MaxRAMPercentage=75 (already fixed, ok)"}, target="v5.{n}.0", v2_of="C05"),
    Cause("C06b", "payments", "provider rate limit misconfigured",
          ["payments timeouts calling provider, {p}% failed", "card payments failing ({p}%), provider calls time out",
           "payments provider errors and timeouts"], "F10",
          {"get_logs": "provider responded 429 too_many_requests (x{w}); cardco-client-rps=500 exceeds contract 50",
           "get_config": "cardco-client-rps=500 (contract allows 50)",
           "get_dependencies": "provider.api: reachable, auth=OK (key valid), throttled"}, target="cardco-client-rps", v2_of="C06"),
    # --- extra traps, used only by the trap-heavy schedule (--schedule traps) ---
    Cause("C02b", "auth", "one bad edge node failing handshakes",
          ["auth 5xx spike, TLS handshake failures from clients", "login failing: auth returns 5xx, clients report SSL errors",
           "auth error rate {p}%, handshake errors in edge logs"], "F16",
          {"get_certs": "cert auth-edge-2027 (auth.internal) valid for 300 more days (renewed)",
           "get_dependencies": "edge nodes: node-{w} handshake_errors={p}0% (others 0.1%)",
           "get_logs": "handshake failures all from node-{w}: tls: internal error (x{w})"}, target="node-{w}", v2_of="C02"),
    Cause("C03b", "notifications", "mail provider rate limit misconfigured",
          ["notifications delayed by {s} minutes", "push and email notifications are late (~{s} min)",
           "notification latency SLO breached, {s} min behind"], "F10",
          {"get_queue": "queue notify.outbound depth={w}00 growing; consumer group notify-sender-v2: 6/6 running, each throttled",
           "get_config": "notify-smtp-rps=5 (documented: 500), changed {s}h ago",
           "get_logs": "smtp send throttled by notify-smtp-rps (x{w})"}, target="notify-smtp-rps", v2_of="C03"),
    Cause("C04b", "orders-worker", "broken feature flag, release is fine",
          ["orders-worker error rate {p}% right after deploy", "orders-worker failing jobs since the last release",
           "spike of failed order jobs ({p}%) after deploy"], "F09",
          {"get_deploys": "orders-worker v2.{m}.0 deployed {s} min ago; canary on v2.{n}.0 shows the same errors",
           "get_config": "flag orders_async_v3 enabled {s} min ago, same time as the deploy",
           "get_logs": "AsyncOrderV3Error: missing idempotency key (only when orders_async_v3=on) x{w}"}, target="orders_async_v3", v2_of="C04"),
    Cause("C07b", "search", "slow disk on one search-db node",
          ["search p99 {s}s, users report slow results", "search is slow: p99 {s}s",
           "search latency alarm p99={s}s"], "F16",
          {"get_logs": "queries use idx_products_sku (ok); slow only when routed to node-{w}",
           "get_metrics": "search-db node-{w} disk latency {s}00ms (others 3ms)"}, target="node-{w}", v2_of="C07"),
    Cause("C08b", "gateway", "bad gateway release",
          ["gateway 429 spike, {p}% of requests throttled", "clients get HTTP 429 from gateway",
           "gateway throttling {p}% of traffic"], "F04",
          {"get_config": "edge-ratelimit-rps=5000 (documented: 5000, ok)",
           "get_deploys": "gateway v3.{m}.0 deployed {s}h ago ('new token bucket'); previous v3.{n}.0 healthy",
           "get_logs": "token bucket refill=0 after v3.{m}.0 (x{w})"}, target="v3.{n}.0", v2_of="C08"),
    Cause("C09b", "orders-worker", "disk full from temp files",
          ["orders-worker write errors, jobs stuck", "orders-worker: 'no space left' alerts",
           "orders-worker failing to persist job state"], "F11",
          {"get_metrics": "disk_used=100% on /var/lib/orders/tmp (52G of spill files); /var/log/orders 12%",
           "get_logs": "ENOSPC: no space left on device /var/lib/orders/tmp (x{w})"}, target="/var/lib/orders/tmp", v2_of="C09"),
    Cause("C10b", "reports-cron", "expired database client certificate",
          ["daily reports job did not run", "reports-cron missed its 02:00 run",
           "no report emails today, reports-cron silent"], "F02",
          {"get_dependencies": "lock store: key=reports/leader free (ok); postgres: TLS client auth FAILED",
           "get_certs": "cert reports-db-client-2026 notAfter={s} days ago status=EXPIRED",
           "get_logs": "x509: certificate reports-db-client-2026 has expired (x{w})"}, target="reports-db-client-2026", v2_of="C10"),
]}

# Where the root cause actually lives when it is not the paged service.
UPSTREAM = {"C01": "pgbouncer", "C01b": "postgres", "C02": "edge-lb", "C03": "notify-queue",
            "C07": "search-db", "C12": "k8s-nodes", "C10": "lock-store", "C06b": "payments",
            "C02b": "edge-lb", "C07b": "search-db"}
for _cid, _c in CAUSES.items():
    _c.paged = _c.service
    _c.service = UPSTREAM.get(_cid, _c.service)

DEPS = {"checkout-api": ["pgbouncer", "postgres", "payments", "inventory-svc"], "auth": ["edge-lb", "postgres"],
        "notifications": ["notify-queue", "postgres"], "orders-worker": ["postgres", "notify-queue"],
        "inventory-svc": ["postgres"], "payments": ["postgres", "edge-lb"], "search": ["search-db", "k8s-nodes"],
        "gateway": ["k8s-nodes", "edge-lb", "auth"], "reports-cron": ["lock-store", "postgres"],
        "pgbouncer": ["postgres"], "postgres": [], "edge-lb": [], "notify-queue": [], "search-db": [],
        "k8s-nodes": [], "lock-store": []}

# 40 pages: 12 first-seen v1 causes, 25 repeats (22 of a v1 cause, 3 of a v2 cause), and 3 v2 traps
# (first time the remembered fix stops working). v2 replaces v1 for that service from then on.
SCHEDULE = [
    "C01", "C02", "C03", "C01", "C04", "C05", "C02", "C06", "C01", "C07",
    "C03", "C05", "C08", "C04", "C09", "C06", "C10", "C02", "C07", "C11",
    "C08", "C12", "C03", "C01b", "C09", "C05", "C10", "C11", "C01b", "C05b",
    "C12", "C06", "C04", "C06b", "C07", "C05b", "C08", "C11", "C06b", "C12",
]

# Trap-heavy schedule (--schedule traps): the same 12 first-seen causes, then 10 traps (each the first time a
# remembered fix stops working) and a repeat of each new cause. Used to measure the ledger with more than 3 traps.
TRAP_SCHEDULE = [
    "C01", "C02", "C03", "C01", "C04", "C05", "C02", "C06", "C07", "C03",
    "C08", "C09", "C04", "C10", "C11", "C12", "C05", "C08",
    "C01b", "C02b", "C03b", "C09", "C04b", "C05b", "C06b", "C07b", "C08b", "C09b", "C10b",
    "C01b", "C02b", "C03b", "C04b", "C05b", "C06b", "C07b", "C08b", "C09b", "C10b", "C11",
]
SCHEDULES = {"main": SCHEDULE, "traps": TRAP_SCHEDULE}


@dataclass
class Incident:
    idx: int
    id: str
    cause: Cause
    alert: str
    shift: str           # "A" or "B"
    night: int
    first_seen: bool     # first time this exact root cause appears
    trap: bool           # a v2 cause whose v1 fix may be remembered
    vals: dict = field(default_factory=dict)
    resolved: bool = False
    tries: list = field(default_factory=list)

    def _fmt(self, s: str) -> str:
        return s.format(**self.vals)

    def run(self, command: str, service: str) -> str:
        if command not in COMMANDS:
            return f"error: unknown command {command}"
        c = self.cause
        if service == c.service and command in c.clues:
            return self._fmt(c.clues[command])
        if service == c.paged and service != c.service:
            if command == "get_dependencies":
                deps = DEPS.get(service, [])
                return f"{service} upstreams: " + ", ".join(
                    f"{d} {'DEGRADED (errors/timeouts from here)' if d == c.service else 'healthy'}" for d in deps)
            if command in ("get_metrics", "get_logs"):
                return f"{service}: symptom confirmed ({self.alert}); own cpu/mem normal; errors are on upstream calls"
        return self._normal(command, service)

    def _normal(self, command: str, service: str) -> str:
        r = random.Random(f"{self.id}:{command}:{service}")
        return {
            "get_metrics": f"cpu={r.randint(20, 45)}% mem={r.randint(40, 60)}% err={r.random():.2f}% p99={r.randint(80, 240)}ms",
            "get_logs": f"{service}: no errors above WARN in last 15m ({r.randint(2, 9)} warnings: retry ok)",
            "get_deploys": f"{service}: last deploy {r.randint(2, 9)} days ago, healthy since",
            "get_config": f"{service}: config unchanged in {r.randint(3, 20)} days",
            "get_pool_stats": f"{service}: pool cl_waiting=0 cl_active={r.randint(3, 12)} maxwait=0s",
            "get_dependencies": f"{service}: all upstreams healthy (p99 < {r.randint(20, 90)}ms)",
            "get_certs": f"{service}: certificates valid for {r.randint(40, 300)} more days",
            "get_queue": f"{service}: no queues owned or depth=0",
        }[command]

    @property
    def target(self) -> str:
        return self._fmt(self.cause.target)

    def apply_fix(self, fix: str, service: str, target: str = "") -> tuple[bool, str]:
        self.tries.append((fix, service, target))
        if fix not in FIXES:
            return False, f"error: unknown fix {fix}"
        if fix == self.cause.fix and service == self.cause.service and target.strip().lower() == self.target.lower():
            self.resolved = True
            return True, f"{FIXES[fix].split(':')[0]} {target} applied on {service}: alerts cleared, error rate back to baseline"
        return False, f"{FIXES[fix].split(':')[0]} {target or '(no target)'} applied on {service}: symptoms persist ({self.alert})"


def build(seed: int = 0, schedule: str | None = None) -> list[Incident]:
    """schedule: "main" (default) or "traps"; NIGHTSHIFT_SCHEDULE sets the default for a whole run."""
    r = random.Random(seed)
    seen: set[str] = set()
    out = []
    for i, cid in enumerate(SCHEDULES[schedule or os.environ.get("NIGHTSHIFT_SCHEDULE", "main")]):
        c = CAUSES[cid]
        n = r.randint(3, 8)
        vals = {"p": r.randint(6, 31), "s": r.randint(2, 9), "w": r.randint(4, 19), "d": r.randint(900, 3000), "n": n, "m": n + 1}
        alert = r.choice(c.alerts).format(**vals)
        night = i // 2
        inc = Incident(i, f"INC-{i + 1:02d}", c, alert, "A" if night % 2 == 0 else "B", night,
                       first_seen=cid not in seen, trap=(c.v2_of is not None and cid not in seen), vals=vals)
        seen.add(cid)
        out.append(inc)
    return out


if __name__ == "__main__":
    for inc in build(0):
        print(inc.id, inc.shift, inc.cause.id, "NEW" if inc.first_seen else "rep", "TRAP" if inc.trap else "", inc.alert)
