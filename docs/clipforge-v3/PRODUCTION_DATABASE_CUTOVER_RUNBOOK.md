# ClipForge V3 Production Database Cutover Runbook

This runbook is for the future maintenance window that moves ClipForge Cloud Run writes from GCSFuse-backed SQLite to Cloud SQL PostgreSQL.

It is intentionally conservative. Do not treat this as approval to create resources, change Cloud Run traffic, or switch production databases without a separate human authorization.

## Current Safety Position

- Cloud Run still uses SQLite unless a Secret Manager-backed `DATABASE_URL` is explicitly deployed.
- SQLite on GCSFuse is not production-safe for active writes.
- Maintenance write-freeze exists behind `CLIPFORGE_MAINTENANCE_MODE=true`.
- Cutover tooling is dry-run/read-only by default.
- The cutover orchestrator refuses real execution unless explicit flags and terminal confirmation are provided.
- No partial database traffic split is allowed during cutover.

## Required Preconditions

1. Working tree is clean on `clipforge-v3-real-provider-alpha`.
2. The deployed Cloud Run revision is known and preserved for rollback.
3. `CLIPFORGE_MAINTENANCE_MODE=true` is enabled before the final snapshot.
4. RQ workers are stopped or prevented from starting write tasks.
5. Cloud Run temporary safeguards remain in place: concurrency `1`, max instances `1`.
6. Final Cloud SQL PostgreSQL instance exists in the same region as Cloud Run.
7. Cloud Run service account has Cloud SQL Client permission.
8. `DATABASE_URL` is stored through Secret Manager, not plaintext Cloud Run env vars.
9. Target PostgreSQL database is empty before import.
10. A human has reviewed the rollback boundary for whether PostgreSQL has accepted any writes.

## Step 1: Enter Write Freeze

Set maintenance mode in the target revision before taking the final SQLite snapshot.

Expected behavior:

- GET pages and health checks remain available.
- POST/PUT/PATCH/DELETE requests return maintenance JSON with HTTP `503`.
- queue enqueue and provider-generation worker starts are blocked.
- no Ark/Seedance task should be created during the database cutover.

Verification:

```bash
python -m pytest -q tests/v3/test_maintenance_mode.py
```

## Step 2: Create a Consistent SQLite Snapshot

Use the SQLite backup API instead of copying active database files by hand.

```bash
python scripts/v3/create_consistent_sqlite_snapshot.py \
  --source /data/clipforge.db \
  --output /tmp/clipforge-cutover-snapshot.db \
  --manifest /tmp/clipforge-cutover-snapshot.manifest.json
```

The manifest records safe metadata only:

- snapshot SHA-256
- byte size
- integrity status
- table row counts
- primary-key high-water marks
- selected null counts
- job status distribution
- V3 core table counts

Do not commit the snapshot, manifest, WAL files, customer data, or database exports.

## Step 3: Run Cutover Preflight

The preflight can run from an offline state JSON in CI/tests, or from read-only Cloud Run metadata during an operations session.

Example dry check:

```bash
python scripts/v3/preflight_production_postgres_cutover.py \
  --project gen-lang-client-0817070175 \
  --region us-central1 \
  --service clipforge-tools \
  --snapshot-manifest /tmp/clipforge-cutover-snapshot.manifest.json
```

Required checks include:

- correct project, region, and service
- Cloud Run concurrency `1`
- Cloud Run max instances `1`
- maintenance mode enabled
- current backend still SQLite before cutover
- GCSFuse mount still present on the old revision
- Cloud SQL Client permission
- PostgreSQL 16 target
- empty target database
- Secret Manager-backed database password
- no existing production `DATABASE_URL` on the old revision
- git HEAD matches the approved cutover commit
- clean working tree
- valid snapshot manifest

Any critical failure blocks cutover.

## Step 4: Review Dry-Run Cutover Plan

```bash
python scripts/v3/cutover_sqlite_to_cloudsql.py
```

The plan requires:

- tagged PostgreSQL revision at `0%` traffic first
- Cloud SQL connection attached to the tagged revision
- Secret Manager-backed `DATABASE_URL`
- maintenance mode kept enabled on the PostgreSQL revision
- read-only validation before traffic moves
- one-step `100%` traffic move only after validation
- no partial traffic split for database cutover

Real execution is intentionally not implemented yet. Passing `--execute` only reaches guardrails and confirmation checks.

## Step 5: Import and Validate Data

Use existing rehearsal tools on the verified snapshot:

```bash
python scripts/v3/export_sqlite_for_postgres.py --sqlite-path /tmp/clipforge-cutover-snapshot.db --output-dir /tmp/clipforge-export
python scripts/v3/import_postgres_from_export.py --export-dir /tmp/clipforge-export --postgres-url "$POSTGRES_TEST_DATABASE_URL" --dry-run
python scripts/v3/compare_database_schema.py --sqlite-path /tmp/clipforge-cutover-snapshot.db --postgres-url "$POSTGRES_TEST_DATABASE_URL"
python scripts/v3/validate_database_migration.py --export-dir /tmp/clipforge-export --postgres-url "$POSTGRES_TEST_DATABASE_URL"
```

Use a disposable test database for rehearsal. Do not point `POSTGRES_TEST_DATABASE_URL` at production.

## Step 6: PostgreSQL Regression Strategy

Fast local regression:

```bash
python -m pytest -q tests/v3
python -m pytest -q tests/test_legacy_routes.py
```

Disposable PostgreSQL checks:

```bash
export POSTGRES_TEST_DATABASE_URL=postgresql+psycopg://...
python -m pytest -q tests/v3/test_database_backend.py
python -m pytest -q tests/v3/test_database_migration_tools.py
python -m pytest -q tests/v3/test_postgresql_integration.py
```

Operational note: `tests/v3/test_postgresql_integration.py` rebuilds schema repeatedly and can be slow on remote `db-f1-micro` Cloud SQL. Before production cutover, run it against a larger disposable instance or optimize the fixture to reuse schema setup with per-test table cleanup.

## Step 7: Rollback Boundary

Generate the rollback plan before moving traffic:

```bash
python scripts/v3/plan_postgres_cutover_rollback.py
python scripts/v3/plan_postgres_cutover_rollback.py --postgres-has-new-writes
```

If PostgreSQL has not accepted writes, rollback can route traffic back to the preserved SQLite revision after snapshot validation.

If PostgreSQL has accepted writes, direct rollback to SQLite is blocked. Keep maintenance mode enabled, preserve PostgreSQL data, compare differences, and perform manual reconciliation or a forward fix.

## Absolute Prohibitions

- Do not run paid Seedance generation during database cutover.
- Do not dual-write SQLite and PostgreSQL without explicit event-level reconciliation.
- Do not split traffic between SQLite and PostgreSQL revisions.
- Do not print or commit `DATABASE_URL`, database passwords, R2 secrets, API keys, tokens, snapshots, exports, videos, or customer data.
- Do not disable maintenance mode until database validation and rollback boundaries are reviewed.

## Go / No-Go

Proceed only if:

- preflight status is `PASS`
- final SQLite snapshot integrity is `ok`
- PostgreSQL import and validation pass
- Cloud Run tagged revision validates at `0%` traffic
- rollback plan is understood
- a human explicitly approves the traffic switch

Otherwise remain in maintenance mode or route back to the preserved SQLite revision before any PostgreSQL writes occur.
