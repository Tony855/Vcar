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

Run the authenticated load script only with a short-lived staging driver token:
```bash
DRIVER_TOKEN='(inject securely)' python scripts/load_test_locations.py \
  --base-url https://staging.example.invalid --connections 50 --messages 3 \
  --json-output artifacts/gps-50.json
```
Repeat at 100, 250 and 500 connections, allowing the host to recover between runs.
Record host CPU, RSS, Redis memory, DB pool saturation, event-loop lag, disconnects and
server-side request rates alongside the generated client report. The script reports
connection success, frame delivery, publish throughput and p50/p95/p99 connection/fan-out
latency. Client-side results alone do not establish server capacity.

Acceptance thresholds must be agreed before the run; suggested starting gates:
- connection success >= 99%;
- frame delivery >= 99%;
- no sustained CPU/RAM saturation or DB/Redis pool exhaustion;
- no cross-account location leakage;
- clean disconnect and reconnect behavior.

## 4. Database backup and restore

- [ ] Verify `MIGRATION_DATABASE_URL` is root-only and points to the schema owner.
- [ ] Run `scripts/backup_database.sh` and verify dump permissions (0600), integrity and retention.
- [ ] Copy the dump to isolated staging restore host and verify checksum.
- [ ] Restore into a disposable database first; do not target production.
- [ ] Verify Alembic revision, row counts, PostGIS extension, wallet ledger invariants and order history.
- [ ] Run API readiness and critical business flows after restore.
- [ ] Record restore duration and RPO/RTO; prove rollback before any production change.

The backup script validates the custom-format archive before publishing it. A successful
archive listing is not a full restore rehearsal.

## 5. Installer and upgrade matrix

- [ ] Fresh install on Ubuntu 24.04 and Debian 13 disposable VMs.
- [ ] Upgrade from the previous supported release with existing data and secrets.
- [ ] Verify Python 3.12–3.14 and Node 26 checks, PostgreSQL/PostGIS, Redis and frontend build.
- [ ] Verify systemd service user cannot read `.env` or migration credentials.
- [ ] Verify services start, survive reboot, restart on failure and pass readiness checks.
- [ ] Verify failed install/upgrade rollback and rerun idempotency.

CI syntax checks are not a substitute for these VM runs.

## 6. Monitoring and alerting

- [ ] Configure a private Blackbox Exporter using `deploy/monitoring/blackbox-vcar.yml`.
- [ ] Probe `/health/live`, `/health/ready` and OSRM route endpoint from the monitoring network.
- [ ] Alert on failed probes, sustained readiness failure, high CPU/RAM/disk, DB pool saturation,
      Redis unavailable, backup age and backup/restore job failure.
- [ ] Route alerts to an owned on-call destination and test delivery/acknowledgement.
- [ ] Ensure probes and logs never expose secrets or WebSocket tickets.

The supplied Blackbox target list is a configuration template, not an installed monitoring stack.

## 7. Passenger / driver / admin end-to-end

- [ ] Passenger: Telegram WebApp authentication, map selection, quote, order creation and status.
- [ ] Driver: Telegram identity exchange, online state, GPS upload, order offer/acceptance.
- [ ] Driver: route, navigation and off-route re-route; validate GPS permissions and background behavior.
- [ ] Admin: fleet map, live location fan-out, stale driver handling and order visibility.
- [ ] Exercise duplicate submit, concurrent driver acceptance, reconnect, Redis restart and API restart.
- [ ] Verify access control between passenger/driver/admin roles and tenant/user boundaries.
- [ ] Verify wallet ledger history and admin audit trail; do not test real money without provider sandbox.
- [ ] Capture redacted traces, screenshots, test IDs and pass/fail evidence.

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
