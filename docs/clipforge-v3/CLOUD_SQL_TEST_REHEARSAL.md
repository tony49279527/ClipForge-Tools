# Cloud SQL PostgreSQL Test Rehearsal

## Scope

- Date: 2026-06-17
- Google Cloud project: `gen-lang-client-0817070175`
- Region: `us-central1`
- Test instance: `clipforge-pg-test`
- Test database: `clipforge_test`
- Test user: `clipforge_test`
- PostgreSQL: `POSTGRES_16`
- Tier: `db-f1-micro`
- Storage: `10 GB SSD`
- Network: Cloud SQL Auth Proxy on `127.0.0.1`; no authorized networks were added.

No database password, full database URL, access token, Secret value, or user row content is recorded here.

## Resource Lifecycle

- Cloud SQL Admin API was enabled in `gen-lang-client-0817070175`.
- The Cloud Run runtime service account was granted `roles/cloudsql.client`.
- A single isolated Cloud SQL test instance was created.
- A temporary Secret Manager secret was used for the test database password.
- The test instance was deleted after rehearsal.
- The temporary password secret was deleted after rehearsal.
- Production Cloud Run was not changed.
- Production `DATABASE_URL` remains unset.
- Production SQLite at `/data/clipforge.db` was not modified.

## Connection Checks

- Auth Proxy: passed.
- SQLAlchemy connection: passed.
- psycopg connection: passed.
- `SELECT version()` confirmed PostgreSQL 16.
- Commit check: passed.
- Rollback check: passed.
- SQLAlchemy `pool_pre_ping` reconnect check: passed.
- Invalid PostgreSQL password failed without falling back to SQLite.

## Schema Checks

- Legacy schema initialization: passed in staged connection test.
- V3 migrations: passed.
- V3 migrations repeated: passed.
- Existing migration-tool PostgreSQL rehearsal test: passed.
- Full `tests/v3/test_postgresql_integration.py` did not fail assertions, but timed out on the remote `db-f1-micro` instance after 900 seconds because each test rebuilds schema and remote DDL is slow. Optimize the suite or use a larger disposable instance before production cutover.

## Representative SQLite Migration

A synthetic temporary SQLite database was created locally. It did not contain customer prompts, API keys, signed URLs, R2 secrets, or real Ark task IDs.

Representative rows:

- `jobs`: 2
- `v3_projects`: 2
- `v3_product_truth`: 2
- `v3_assets`: 2
- `v3_shots`: 2
- `v3_prompt_versions`: 2
- `v3_preflight_checks`: 2
- `v3_generation_submissions`: 2
- `v3_takes`: 1
- `v3_usage_events`: 1
- `v3_operation_events`: 2

Migration rehearsal:

- SQLite export: passed.
- Manifest/hash generation: passed for 21 tables.
- PostgreSQL import dry-run: passed.
- Validate-only pass: passed.
- Formal import into disposable test database: passed.
- Migration validation: passed.
- Schema compare: passed.
- Unique-key conflict import caused rollback: passed.
- No half-imported rows after failed import: passed.
- Repaired import after rollback: passed.
- Re-import into non-empty database refused: passed.
- Sequence correction after import: passed.

## Production SQLite Copy Rehearsal

A non-mutating backup object was created:

```text
gs://clipforge-tools-data/backups/cloudsql-rehearsal/20260617T072945Z/clipforge.db
```

This was a rehearsal copy only. Because Cloud Run uses SQLite on GCSFuse and WAL/SHM may be active, this must not be treated as a final consistent production snapshot. A real cutover still needs a maintenance window and controlled backup procedure.

Read-only copy results:

- Size: `462848` bytes.
- SHA-256: `c91cec2c0d8b63a272f2e8d3afa63e4445d17bb32edfdc0a2794d2417b7249d2`
- `PRAGMA integrity_check`: `ok`
- Tables exported: 21
- Manifest hashes: 21
- Dry-run tables: 21
- Validate-only tables: 21

Nonzero table counts:

- `jobs`: 18
- `clips`: 30
- `usage_events`: 25
- `schema_migrations`: 8

V3 table counts in the copy were all zero at rehearsal time.

Status distribution summary:

- `jobs`: `failed=8`, `succeeded=9`, `uploading=1`
- `clips`: `failed=12`, `succeeded=18`
- `usage_events`: `succeeded=25`

No production data was imported into PostgreSQL.

## Cloud Run Readiness Notes

- Cloud Run service account has `roles/cloudsql.client`.
- Instance connection name format was confirmed: `PROJECT:REGION:INSTANCE`.
- Future Cloud Run PostgreSQL should use a Secret Manager-backed `DATABASE_URL`.
- Future Cloud Run deployment must add a Cloud SQL connection and can use a Unix socket path under `/cloudsql/<INSTANCE_CONNECTION_NAME>`.
- The current `db.py` passes `DATABASE_URL` through to SQLAlchemy/psycopg, so SQLAlchemy's Cloud SQL Unix socket URL form can be used in a future staging revision.
- Current Cloud Run revision was not modified.
- Current Cloud Run still uses SQLite at `/data/clipforge.db`.

## Remaining Production Cutover Requirements

- Create the final Cloud SQL PostgreSQL instance.
- Take a consistent SQLite backup during a maintenance window.
- Import into the final PostgreSQL database.
- Validate row counts, key hashes, uniqueness, sequences, and application smoke tests.
- Deploy a new Cloud Run revision with Cloud SQL connection and Secret Manager-backed `DATABASE_URL`.
- Keep the previous SQLite revision available for rollback until validation completes.
