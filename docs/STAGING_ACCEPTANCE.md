# Vcar staging acceptance — seven workstreams

This is an execution record, not a claim that staging or production has passed.
Run only against an isolated staging deployment with approved test accounts and
non-production data. Never paste credentials, Telegram initData, JWTs, or payment
webhook secrets into this report.

## 0. Environment gate

- [ ] Record commit SHA, deployment timestamp, OS image, Python/Node/PostgreSQL/PostGIS versions.
- [ ] Confirm the host is isolated staging, backups are available, and rollback owner is present.
- [ ] Confirm test Telegram bots and test passenger/driver/admin identities.
- [ ] Confirm no production payment credentials or production database URL are loaded.

## 1. Payments — provider integration gate

- [ ] Obtain current official ABA PayWay / Bakong merchant documentation and sandbox credentials.
- [ ] Implement provider-specific signature/authenticity verification from that documentation.
- [ ] Verify amount, currency (USD/KHR), merchant, order reference, and final settlement state server-side.
- [ ] Enforce webhook replay protection and idempotency at the database transaction boundary.
- [ ] Test invalid signature, altered amount/currency, duplicate callback, out-of-order callback,
      timeout, refund, and reconciliation cases.
- [ ] Reconcile provider settlement report against the wallet ledger before enabling the feature flag.

**Current repository boundary:** wallet ledger and audited admin adjustments only. No real
provider adapter or verified ABA/Bakong settlement is claimed. Keep external payment disabled
until all checks above pass. Never credit a wallet based only on a browser redirect or client claim.
Wallet idempotency now binds a replay to the same user, amount, currency, kind, order, provider
reference and canonical audit metadata. This is a ledger safety guard, not provider signature
verification or evidence of settlement.

**Cancellation-fee boundary:** order cancellation currently records `cancel_fee` on the order
and in the immutable order event only. It does not debit the passenger wallet, credit the driver,
or create a settlement ledger entry. Bot messages must describe the amount as recorded/pending,
not as already deducted or paid. Do not enable collection or driver payout until the fee currency,
charge trigger, payer balance/collection method, refund rules and driver/platform allocation are
approved and covered by an explicit balanced-ledger workflow.
The current release deliberately does not invent a callback signature, merchant identity, or
provider status mapping. Provider integration remains blocked on the official merchant contract
and sandbox; CI cannot certify settlement correctness without those inputs.

## 2. Cambodia OSRM map and route quality

- [ ] Download an approved Cambodia OSM extract and record source URL, date, checksum, and profile.
- [ ] Build a new map in a versioned directory; do not overwrite the active map during preparation.
- [ ] Run `scripts/smoke_osrm.py --base-url http://127.0.0.1:5000 --origin 104.9282,11.5564 --destination 104.9500,11.5700`.
- [ ] Validate representative Phnom Penh, airport, provincial, rural, ferry/bridge and border routes.
- [ ] Compare route geometry, distance, duration and snapping against a human-reviewed reference set.
- [ ] Check unreachable roads, one-way restrictions, access tags and profile assumptions.
- [ ] Switch the active map only after review; retain the previous map for rollback.

OSRM does not provide live traffic. Do not represent its duration as a traffic-aware ETA.

## 3. GPS/WebSocket capacity

Run the authenticated load script only with separate short-lived staging driver and admin JWTs. It connects admin fleet-stream subscribers while a driver publishes GPS updates:
```bash
DRIVER_TOKEN='(inject securely)' ADMIN_TOKEN='(inject securely)' python scripts/load_test_locations.py \
  --base-url https://staging.example.invalid --connections 50 --messages 3 --reconnect-probes 5 \
  --json-output artifacts/gps-50.json
```
Repeat at 100, 250 and 500 connections, allowing the host to recover between runs.
Record host CPU, RSS, Redis memory, DB pool saturation, event-loop lag, disconnects and
server-side request rates alongside the generated client report. The script reports
admin subscriber connection success, initial snapshot delivery, fleet event delivery, reconnect
snapshot delivery, publish throughput and p50/p95/p99 connection/fan-out latency. Its exit gate
requires connection, frame, initial snapshot and reconnect snapshot rates to meet the configured
minimum and zero recorded failures. A run with `--reconnect-probes 0` (or no initial snapshot
attempts) records a zero snapshot rate and cannot pass the corresponding acceptance gate; skipped
probes are not counted as successful. Client-side results alone do not establish server capacity.

Acceptance thresholds must be agreed before the run; suggested starting gates:
- connection success >= 99%;
- frame delivery >= 99%;
- initial snapshot and reconnect snapshot delivery >= 99%;
- no sustained CPU/RAM saturation or DB/Redis pool exhaustion;
- no cross-account location leakage;
- clean disconnect and reconnect behavior.

Optional client-side p95 latency ceilings can be made hard exit gates instead of
being merely reported:
```bash
VCAR_MAX_CONNECT_P95_MS=1500 VCAR_MAX_FANOUT_P95_MS=500 \
  bash scripts/run_staging_load_profiles.sh
```
These values are examples only, not approved production SLAs. Set them from the
staging host/network baseline before a run. When configured, the load script
rejects missing, non-finite, negative, unordered or over-ceiling p50/p95/p99
distributions; when omitted, latency percentiles remain report-only. The four-tier
helper passes the same ceilings to every 50/100/250/500 profile.

The admin fleet stream periodically reconciles from PostgreSQL and falls back to a durable
snapshot if Redis Pub/Sub fails. Driver and administrator stream recovery paths have automated
Redis-failure/re-subscription coverage. Run a Redis restart exercise in staging and verify the
browser remains populated while Redis recovers.

Driver online intent and a monotonic presence generation are persisted in PostgreSQL.
The Redis reconciliation worker repairs only approved drivers whose durable online flag is true,
and it rechecks/locks that state before writing the projection. GPS ingestion holds the same user
row lock through its best-effort Redis publish so an offline transition cannot be overtaken by an
older in-flight GPS request. Migration `20261113_0009` initializes existing drivers as offline;
after upgrading, drivers must explicitly go online again. Redis markers are rebuilt from the
database generation after Redis loss or a database restore that rolls the durable generation back; the reconciliation worker replaces stale Redis generation markers only after rechecking the locked PostgreSQL row.

The administrator fleet WebSocket now sends a durable PostgreSQL snapshot immediately after
subscribing to Redis, including on reconnect. If a Pub/Sub connection fails, it rebuilds the
subscription and sends another durable snapshot. The driver location WebSocket also rebuilds
its subscription and periodically reconciles from PostgreSQL every 30 seconds, repairing missed
Redis publications. The frontend reconciles its marker set from admin snapshots before applying
subsequent live events. CI covers the reconnect paths; the 50–500 connection staging runs below
remain NOT RUN and are not implied by the code change.

## 4. Database backup and restore

- [ ] Verify `MIGRATION_DATABASE_URL` is root-only and points to the schema owner.
- [ ] Run `scripts/backup_database.sh` and verify dump permissions (0600), integrity and retention.
- [ ] Copy the dump to isolated staging restore host and verify checksum.
- [ ] Restore into a disposable database first; do not target production.
- [ ] Verify Alembic revision, row counts, PostGIS extension, wallet ledger invariants and order history.
- [ ] Run API readiness and critical business flows after restore.
- [ ] Record restore duration and RPO/RTO; prove rollback before any production change.

The backup script validates the custom-format archive before publishing it. Concurrent backup
runs receive unique temporary and final archive names, avoiding same-second overwrite. Restore
rejects an unreadable/invalid archive before creating the pre-restore backup or stopping services,
and aborts before service stop if the pre-restore backup command fails. CI covers these fail-closed
paths. A successful archive listing is not a full restore rehearsal.

## 5. Installer and upgrade matrix

- [ ] Fresh install on Ubuntu 24.04 and Debian 13 disposable VMs.
- [ ] Upgrade from the previous supported release with existing data and secrets.
- [ ] Verify Python 3.12–3.14 and Node 26 checks, PostgreSQL/PostGIS, Redis and frontend build.
- [ ] Verify systemd service user cannot read `.env` or migration credentials.
- [ ] Verify services start, survive reboot, restart on failure and pass readiness checks.
- [ ] Verify failed install/upgrade rollback and rerun idempotency.

The supported-OS gate is now isolated in `scripts/check_supported_os.sh` and covered for
Ubuntu 22.04 rejection, Ubuntu 24.04 acceptance, Debian 12 rejection, and Debian 13 acceptance.
CI syntax checks and this preflight test are not a substitute for the full disposable-VM runs.
The installer publishes frontend assets
through a staged directory and restores the previous assets if the final directory swap fails;
CI covers publish success, incomplete build rejection, and swap-failure rollback. This does not
replace the Ubuntu/Debian VM upgrade matrix.

## 6. Monitoring and alerting

- [ ] Configure a private Blackbox Exporter using `deploy/monitoring/blackbox-vcar.yml` and `deploy/monitoring/prometheus-vcar-scrape.yml`.
- [ ] Probe `/health/live`, `/health/ready` and OSRM route endpoint from the monitoring network.
- [ ] Alert on failed probes, sustained readiness failure, high CPU/RAM/disk, DB pool saturation,
      Redis unavailable, backup age and backup/restore job failure.
- [ ] Route alerts to an owned on-call destination and test delivery/acknowledgement.
- [ ] Ensure probes and logs never expose secrets or WebSocket tickets.

The app now exposes `/metrics` only when `METRICS_BEARER_TOKEN` is configured and a matching
Bearer token is supplied. Metrics include pending outbox count/age, fresh/stale driver counts,
asyncpg pool current/idle/maximum connections, and Redis PING availability. The scrape remains
available during a Redis outage so the Redis alert can fire. Alert rules cover metrics scrape
failure, Redis unavailability, sustained DB pool saturation, and outbox backlog/age. CI validates
the alert rules with Prometheus `promtool`. Prometheus reads the token from
`/etc/prometheus/secrets/vcar-metrics-token`; provision this file with restrictive permissions
and the same random value as the app environment. Test alert delivery and acknowledgement in
staging. The supplied scrape and alert files are templates, not an installed monitoring stack.

## 7. Passenger / driver / admin end-to-end

- [ ] Passenger: Telegram WebApp authentication, map selection, quote, order creation and status.
- [ ] Driver: Telegram identity exchange, online state, GPS upload, order offer/acceptance.
- [ ] Driver: route, navigation and off-route re-route; validate GPS permissions and background behavior.
- [ ] Admin: fleet map, live location fan-out, stale driver handling and order visibility.
- [ ] Exercise duplicate submit, concurrent driver acceptance, reconnect, Redis restart and API restart.
- [ ] Verify access control between passenger/driver/admin roles and tenant/user boundaries.
- [ ] Verify wallet ledger history and admin audit trail; do not test real money without provider sandbox.
- [ ] Capture redacted traces, screenshots, test IDs and pass/fail evidence.

## 8. Order status event reconnect/replay

- [ ] Connect a passenger to an owned order stream and record the latest processed event ID from the initial snapshot.
- [ ] Disconnect, advance the order through multiple transitions, and reconnect with that cursor.
- [ ] Verify events are replayed in ascending event ID order before live Pub/Sub frames.
- [ ] Verify overlap between replay and queued Pub/Sub frames is deduplicated by event ID.
- [ ] Verify a cursor older than 500 events receives a current-state snapshot with resync_required=true.
- [ ] Verify a future cursor is reset to a current snapshot and cross-passenger order IDs remain denied.

The server subscribes to Redis before reading the durable outbox. Clients should persist the
latest processed event ID in session state and reconnect with the last_event_id query parameter;
events are at-least-once at the transport boundary, so clients must deduplicate by event ID. CI covers
ordering, bounded replay, resync, and replay/PubSub overlap. This does not yet constitute a full
passenger UI end-to-end run.

## Sign-off

| Track | Result | Evidence / issue | Owner | Date |
|---|---|---|---|---|
| 1 Payments | BLOCKED pending official provider sandbox | | | |
| 2 OSRM | NOT RUN on staging | | | |
| 3 GPS load | NOT RUN on staging | | | |
| 4 Backup/restore | NOT RUN on staging | | | |
| 5 Installer VM matrix | NOT RUN on disposable VMs | | | |
| 6 Monitoring | TEMPLATE ONLY | | | |
| 7 E2E | NOT RUN on staging | | | |


### Batch execution helper

Use `bash scripts/run_staging_load_profiles.sh` to execute the 50, 100, 250 and 500 subscriber tiers sequentially. It requires `VCAR_STAGING_ACK=I_CONFIRM_ISOLATED_STAGING`, `VCAR_BASE_URL`, and short-lived `DRIVER_TOKEN` / `ADMIN_TOKEN` environment variables; tokens are never command-line arguments or printed. Each tier writes a JSON report under `artifacts/gps-load/` (directory mode 0700), waits 30 seconds by default, and stops immediately if a tier fails. Tune `VCAR_LOAD_COOLDOWN_SECONDS`, `VCAR_LOAD_MESSAGES`, `VCAR_RECONNECT_PROBES`, `VCAR_MIN_DELIVERY_RATE`, and `VCAR_LOAD_OUTPUT_DIR` as needed. This helper automates the sequence only; it does not run staging tests itself or claim that any tier has passed.


### Backup freshness and failure telemetry

The backup script can publish node_exporter textfile metrics when `VCAR_BACKUP_METRICS_FILE` points to a file inside the configured textfile collector directory. It atomically writes `vcar_backup_last_success_timestamp_seconds` and `vcar_backup_last_run_success`; a failed run sets the latter to 0 while preserving the last verified backup timestamp. Prometheus rules alert when a backup run fails or the last verified backup is older than 24 hours. Configure the environment variable in the scheduled backup job, ensure node_exporter reads the containing directory, and verify the textfile is readable by the exporter. CI tests success/failure metric behavior and evaluates both alert rules; actual scheduled-job and Alertmanager delivery still require staging setup.

### Multi-currency wallet rollback gate

Migration `20261112_0007_multi_currency_wallets` changes wallet uniqueness from one wallet per user to one wallet per user and currency. Its downgrade intentionally fails when a user has multiple currency wallets because restoring the former `UNIQUE(user_id)` constraint would be unsafe. Before any downgrade across this revision, run this query on a database copy and the target:

```sql
SELECT user_id, count(*) AS wallet_count,
       array_agg(currency ORDER BY currency) AS currencies
FROM wallet_accounts
GROUP BY user_id
HAVING count(*) > 1;
```

Any returned row blocks direct downgrade. Do not delete wallets or ledger rows, or merge balances manually to force rollback. Prefer restoring a verified pre-upgrade backup during an approved maintenance window. If post-upgrade financial activity exists, keep the current schema and ship a forward fix/new migration instead. An empty result is not sufficient by itself: rehearse downgrade/upgrade on a disposable copy and reconcile wallet balances and ledger rows before approval.
