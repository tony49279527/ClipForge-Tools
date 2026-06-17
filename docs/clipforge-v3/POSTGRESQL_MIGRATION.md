# ClipForge V3 PostgreSQL Migration Plan

## 1. Current SQLite/GCSFuse Risk

The current Cloud Run deployment stores the shared ClipForge SQLite database at `/data/clipforge.db`, where `/data` is a GCSFuse mount. The latest database risk audit confirmed that the copied main database passed `PRAGMA integrity_check`, but Cloud Run logs also showed `clipforge.db-shm` out-of-order writes and GCS API `429` retries. SQLite WAL/SHM files are not a safe online write model on GCSFuse, especially with Cloud Run concurrency `80`, max instances `20`, and Legacy plus V3 sharing the same database.

The current page health checks only prove that startup and reads can work. They do not prove that project creation, uploads, generation submission, Take creation, cost writes, or concurrent Legacy writes are safe.

## 2. Target Architecture

- Local development: SQLite through the default file-backed configuration.
- Automated tests: temporary SQLite by default.
- Cloud Run production/alpha writes: PostgreSQL through `DATABASE_URL`.
- Object storage: Cloudflare R2 remains independent of the database backend.
- Real provider execution: must remain blocked from public production writes until the PostgreSQL cutover is tested.

## 3. DATABASE_URL Behavior

`db.py` now uses `DATABASE_URL` as the first-priority database selector.

Supported examples:

```bash
DATABASE_URL=sqlite:////absolute/path/to/clipforge.db
DATABASE_URL=postgresql+psycopg://USER:REDACTED@HOST:5432/clipforge
```

If `DATABASE_URL` is unset, the app preserves the previous local SQLite behavior using `DB_PATH`, `DATA_DIR`, or the legacy SQLite-only `DB_URL`.

`DB_URL` remains backward compatible for SQLite only. Non-SQLite `DB_URL` values are intentionally rejected; PostgreSQL must use `DATABASE_URL`.

Logs and diagnostics must never print the full URL. Only safe metadata such as dialect, whether a host is configured, and a non-secret database name summary should be shown.

## 4. Local SQLite Setup

Default local setup requires no database environment variable:

```bash
python -m pytest -q tests/v3
```

Optional explicit local SQLite:

```bash
export DATABASE_URL=sqlite:////tmp/clipforge-dev.db
```

SQLite still applies local pragmas such as foreign keys, WAL journal mode, and busy timeout only for the SQLite dialect.

## 5. PostgreSQL Setup

PostgreSQL support is implemented through SQLAlchemy Core with the `psycopg` 3 driver:

```bash
DATABASE_URL=postgresql+psycopg://...
```

The engine uses `pool_pre_ping`, bounded pool sizing, and connection recycle settings suitable for Cloud Run. Cloud SQL-specific networking, instance creation, IAM, private IP, Unix socket routing, and secret delivery are not implemented in this commit.

## 6. Schema Compatibility

The database adapter compiles critical SQLite DDL patterns for PostgreSQL:

- `INTEGER PRIMARY KEY AUTOINCREMENT` becomes PostgreSQL identity primary keys.
- `INSERT OR IGNORE` used by schema migration recording becomes `ON CONFLICT DO NOTHING`.
- `PRAGMA table_info(...)` column checks are routed to SQLite PRAGMA or PostgreSQL `information_schema.columns`.
- Row results are returned through a mapping-compatible `DbRow`.

The migration path is repeatable for SQLite and has a real PostgreSQL integration test hook through `POSTGRES_TEST_DATABASE_URL`. That integration test is skipped unless an explicit disposable PostgreSQL test database is provided.

GitHub Actions now includes `.github/workflows/postgresql-integration.yml`, which runs PostgreSQL 16 as a service container and executes the PostgreSQL integration and migration rehearsal tests without Cloud SQL or production credentials.

## 7. SQL Incompatibilities Resolved

Resolved in the backend foundation:

- Replaced direct `sqlite3.connect()` application entrypoint with SQLAlchemy engine selection.
- Preserved existing `?` placeholder callers through an adapter that converts placeholders to named SQLAlchemy binds outside literals and comments.
- Preserved `lastrowid` behavior for existing repository code, using PostgreSQL `RETURNING id` where needed.
- Added backend-neutral `DatabaseIntegrityError` while keeping compatibility with existing SQLite integrity assertions.
- Stopped migration column detection from depending directly on SQLite PRAGMA in V3 migration code.

Still to reduce in later passes:

- Many repositories still use legacy cursor-style SQL instead of SQLAlchemy Core statements directly.
- Root Legacy tables and V3 migrations still contain SQLite-shaped DDL text that is compiled by the adapter rather than expressed as explicit SQLAlchemy schema objects.
- Some tests still inspect SQLite directly because local fixtures intentionally use temporary SQLite.

## 8. Repository Migration Status

Legacy and V3 code now route through `db.get_conn()` and the shared adapter for normal app paths. V3 repositories that depend on uniqueness protection use `DatabaseIntegrityError` instead of directly catching `sqlite3.IntegrityError`.

The following higher-risk V3 paths now use named-parameter helpers instead of relying only on SQLite-style `?` conversion:

- project create/read/update
- product truth create
- asset create/read/update
- shot create/read/update
- prompt version create
- generation submission reservation/read/update/claim
- Take create/read/update/get-or-create
- generation usage event idempotent create

Remaining adapter-dependent paths include broad Legacy CRUD, some V3 list/report queries, review/final-assembly/retake helpers, continuity helpers, and migration DDL compilation.

This is a foundation, not a finished production migration. The code can select PostgreSQL and create schema from an empty database path, but production data has not been exported, transformed, imported, or cut over.

## 9. Test Strategy

Current SQLite regression:

```bash
python -m pytest -q tests/v3
python -m pytest -q tests/test_legacy_routes.py
```

Optional disposable PostgreSQL integration:

```bash
export POSTGRES_TEST_DATABASE_URL=postgresql+psycopg://...
python -m pytest -q tests/v3/test_database_backend.py
python -m pytest -q tests/v3/test_postgresql_integration.py
python -m pytest -q tests/v3/test_database_migration_tools.py
```

Do not point `POSTGRES_TEST_DATABASE_URL` at production Cloud SQL or any database containing real ClipForge data.

## 10. Existing Data Migration Plan

1. Freeze or temporarily block online write paths.
2. Take an immutable backup of `/data/clipforge.db` and related WAL files if present.
3. Run `PRAGMA integrity_check` on a safe copied SQLite database.
4. Export table schemas and row counts.
5. Create an empty PostgreSQL test database.
6. Run app schema initialization against PostgreSQL.
7. Import Legacy and V3 data in dependency order.
8. Validate row counts, unique constraints, representative projects, assets, submissions, takes, usage events, and Legacy jobs.
9. Run read-only smoke checks against the imported PostgreSQL database.
10. Only then test controlled write paths on a staging/alpha service.

Rehearsal tools:

- `scripts/v3/compare_database_schema.py`
- `scripts/v3/export_sqlite_for_postgres.py`
- `scripts/v3/import_postgres_from_export.py`
- `scripts/v3/validate_database_migration.py`

The import tool is dry-run by default and requires explicit confirmation before writing. It refuses SQLite targets and refuses non-local PostgreSQL targets for this rehearsal phase.

## 11. Cloud SQL Connection Plan

Use Secret Manager or Cloud Run secret environment variables for `DATABASE_URL`. Do not print or commit database credentials.

Recommended Cloud Run order:

1. Create a dedicated Cloud SQL PostgreSQL test instance.
2. Configure networking and least-privilege database user.
3. Deploy a staging revision with `DATABASE_URL` set to the PostgreSQL test database.
4. Run schema initialization and smoke tests.
5. Import copied SQLite data into the test database.
6. Validate application reads and safe writes.
7. Plan production cutover only after staging validation.

Planning helper:

```bash
bash scripts/v3/prepare_cloud_sql_postgres.sh --help
bash scripts/v3/prepare_cloud_sql_postgres.sh --project YOUR_TEST_PROJECT --plan
```

Do not pass `--execute` until a human explicitly authorizes Cloud SQL creation.

## 12. Cutover Sequence

1. Disable or gate external V3 write workflows.
2. Stop background workers that can write database state.
3. Backup SQLite from GCSFuse.
4. Import into PostgreSQL.
5. Deploy Cloud Run with `DATABASE_URL`.
6. Run health checks and read-only validation.
7. Run limited write validation.
8. Re-enable V3 write flows only after validation passes.

## 13. Rollback Sequence

1. Preserve the previous Cloud Run revision before cutover.
2. Keep the SQLite backup immutable.
3. If PostgreSQL cutover fails before new writes, route traffic back to the previous revision.
4. If PostgreSQL has accepted writes, do not blindly roll back to SQLite without a reconciliation plan.
5. Never dual-write SQLite and PostgreSQL without explicit event-level reconciliation.

## 14. Test Rehearsal Completed

A Cloud SQL PostgreSQL 16 test rehearsal was completed on 2026-06-17 using an isolated instance named `clipforge-pg-test` in `gen-lang-client-0817070175/us-central1`. The instance was deleted after the rehearsal.

Validated:

- SQLAlchemy and psycopg connections through Cloud SQL Auth Proxy.
- PostgreSQL 16 version check.
- Commit, rollback, reconnect, and failed-password behavior.
- Legacy schema initialization.
- V3 migration repeatability.
- Existing migration-tool PostgreSQL rehearsal test.
- Representative SQLite export/import/validation with Legacy jobs, V3 projects, Product Truth, assets, shots, prompt versions, preflight, generation submissions, Take, usage/cost, and operation events.
- Unique conflict rollback.
- Non-empty database import refusal.
- PostgreSQL sequence correction after import.
- Read-only production SQLite copy export/dry-run/validate-only.

The full `tests/v3/test_postgresql_integration.py` run timed out on the remote `db-f1-micro` test instance after 900 seconds because it rebuilds schema repeatedly. No assertion failure was observed before timeout, but this suite should be optimized or run on a larger disposable instance before production cutover.

Detailed report: `docs/clipforge-v3/CLOUD_SQL_TEST_REHEARSAL.md`.

## 15. Known Incomplete Items

- No production data has been migrated.
- Cloud Run still has not been switched to PostgreSQL.
- The Cloud SQL test instance used for rehearsal has been deleted.
- Real PostgreSQL integration is only run when `POSTGRES_TEST_DATABASE_URL` points to a disposable test database.
- Repositories still use a compatibility cursor API and should eventually move to explicit SQLAlchemy Core statements.

## 16. Single Next Task

In a maintenance window, create a consistent SQLite backup, migrate to a final Cloud SQL PostgreSQL instance, and switch a new Cloud Run revision to Secret Manager-backed `DATABASE_URL`; if validation fails, route traffic back to the SQLite revision.
