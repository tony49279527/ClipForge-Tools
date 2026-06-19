# ClipForge V3 Cloud Run Database Risk Audit

## 1. Current Database Topology

Audited repository state:

- Repository: `tony49279527/ClipForge-Tools`
- Branch: `clipforge-v3-real-provider-alpha`
- Audited commit: `fcb4d18cc99e8fb90f51fc0e37e41c769b0b0c7e`
- Cloud Run service: `clipforge-tools`
- Region: `us-central1`
- Current ready revision during audit: `clipforge-tools-00103-x9z`

At audit time, the application used `db.py` as the shared SQLite access layer for both Legacy routes and ClipForge V3. The key runtime logic was:

- `db.py::DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).resolve()`
- `db.py::DB_URL = os.getenv("DB_URL", "")`
- `db.py::_resolve_db_path()` accepts only SQLite `DB_URL`; non-SQLite `DB_URL` raises `RuntimeError("Only sqlite DB_URL is supported in this build.")`
- `db.py::DB_PATH` defaults to `DATA_DIR / "clipforge.db"`
- `db.py::get_conn()` opens `sqlite3.connect(DB_PATH, check_same_thread=False, timeout=...)`
- `db.py::get_conn()` sets `PRAGMA journal_mode = WAL` on every connection
- `db.py::get_conn()` sets `PRAGMA busy_timeout = 30000`

Follow-up code now adds `DATABASE_URL`-driven SQLite/PostgreSQL backend selection through SQLAlchemy Core while preserving local SQLite defaults. A PostgreSQL integration workflow and migration rehearsal tools now exist. This does not change the audited production topology until Cloud Run is explicitly configured with a PostgreSQL `DATABASE_URL` and production data is migrated.

Cloud Run currently sets:

- `DATA_DIR=/data`
- `DB_PATH=/data/clipforge.db`
- `DB_URL` unset
- `DATABASE_URL` unset

Therefore the active SQLite database path is:

```text
/data/clipforge.db
```

SQLite WAL/SHM companion paths are:

```text
/data/clipforge.db-wal
/data/clipforge.db-shm
```

Legacy and V3 share the same database file. Legacy tables such as `jobs`, `clips`, `storyboard_frames`, and `usage_events` are in the same SQLite database as V3 tables such as `v3_projects`, `v3_assets`, `v3_generation_submissions`, `v3_takes`, and `v3_usage_events`.

## 2. Cloud Run And GCSFuse Configuration

Cloud Run service description showed:

- GCSFuse volume driver: `gcsfuse.run.googleapis.com`
- GCS bucket mounted: `clipforge-tools-data`
- Mount path: `/data`
- Container concurrency: `80`
- Min scale: `1`
- Max scale: `20`
- Timeout: `300` seconds
- CPU throttling: `false`
- Startup CPU boost: `true`
- 100% traffic: `clipforge-tools-00103-x9z`

The database path `/data/clipforge.db` is inside the GCSFuse mount. The container local filesystem still exists for normal temporary files, but it is not the active database location because `DATA_DIR=/data` and `DB_PATH=/data/clipforge.db`.

The bucket currently contains `clipforge.db` and legacy output/upload objects. A listing for `clipforge.db`, `clipforge.db-wal`, and `clipforge.db-shm` showed only:

```text
gs://clipforge-tools-data/clipforge.db
```

No persistent `clipforge.db-wal` or `clipforge.db-shm` object was listed at audit time, but Cloud Run logs clearly show runtime access to `clipforge.db-shm` through GCSFuse.

## 3. Observed Errors

Recent Cloud Run logs for `clipforge-tools` included repeated GCSFuse messages:

```text
Out of order write detected. File clipforge.db-shm will now use legacy staged writes.
BufferedWriteHandler.OutOfOrderError for object: clipforge.db-shm, expectedOffset: 0, actualOffset: 4095
Retrying for the error: googleapi: got HTTP response code 429
Existing file clipforge.db of size ... will use legacy staged writes.
```

The app did start successfully after these messages:

```text
Application startup complete.
Default STARTUP TCP probe succeeded.
```

No log evidence was found in this audit for:

- `database locked`
- `disk I/O error`
- `readonly database`
- `malformed`
- `corrupt`

However, the absence of those strings is not proof that the deployment is safe for writes.

## 4. SQLite WAL/SHM Risk

SQLite WAL mode relies on coordinated writes and locking across:

- the main database file
- the `-wal` file
- the `-shm` shared-memory index file

The current deployment puts those files on a GCSFuse mount. That is unsafe for production writes because object-storage-backed FUSE semantics are not equivalent to a local POSIX filesystem for SQLite locking, WAL coordination, and shared-memory behavior.

The observed `clipforge.db-shm` out-of-order writes are direct evidence that SQLite shared-memory writes are being mediated by GCSFuse in a way that the storage layer treats as non-sequential. GCSFuse falling back to "legacy staged writes" may keep the process alive, but it does not make SQLite WAL/SHM semantics a safe multi-writer database protocol.

Risk conclusion:

- WAL/SHM on GCSFuse should be treated as unsafe.
- The current `PRAGMA journal_mode = WAL` amplifies the risk because every app connection tries to use WAL mode on the mounted path.
- GET 200 only proves the app can start and serve simple reads; it does not prove safe writes.

## 5. Multi-Instance And Revision Risk

Cloud Run currently allows:

- up to 20 instances
- up to 80 concurrent requests per instance
- min 1 instance

This means multiple threads and potentially multiple container instances can try to write the same `/data/clipforge.db` file. During a revision rollout, two revisions can also briefly coexist while traffic is shifting or old instances drain. Both revisions would point at the same GCSFuse-mounted database path.

Application startup also has write risk. `app.py::on_startup()` runs `run_v3_migrations()` when `CLIPFORGE_V3_ENABLED=true`. Migration code uses SQLite DDL and writes to `schema_migrations`. With multiple instances or overlapping revisions, startup can create concurrent database writers before any user action happens.

Write paths at risk include:

- Legacy job creation and status updates
- Legacy clip, storyboard, image-version, usage, and YouTube publish updates
- V3 project creation
- V3 asset upload metadata
- V3 director plan and shot updates
- V3 generation submission state changes
- V3 Take creation
- V3 usage/cost events
- V3 migrations and schema bookkeeping

## 6. Current Data Integrity Check

The live database object was copied from GCS to a local temporary directory and checked offline. This avoided running SQLite checks directly against the mounted production file.

Commands run against the copied file:

```bash
sqlite3 /tmp/.../clipforge.db "PRAGMA integrity_check;"
sqlite3 /tmp/.../clipforge.db "PRAGMA journal_mode;"
sqlite3 /tmp/.../clipforge.db "PRAGMA database_list;"
```

Results:

- `PRAGMA integrity_check;` returned `ok`
- `PRAGMA journal_mode;` returned `wal`
- `PRAGMA database_list;` pointed at the copied local file

Key row counts in the copied database:

- `jobs`: 18
- `v3_projects`: 0
- `v3_generation_submissions`: 0
- `v3_takes`: 0
- `v3_usage_events`: 0

This means the copied main database file was readable and structurally intact at the time of the audit. It does not prove that future writes on GCSFuse are safe, and it does not prove that a live concurrent write could not be lost or corrupted.

## 7. Short-Term Safeguards

Update on 2026-06-17:

- Cloud Run container concurrency has been temporarily reduced to `1`.
- Cloud Run max instances has been temporarily reduced to `1`.
- Min instances remains `1`.
- The GCSFuse `/data` mount is still present.
- `DATA_DIR=/data` and `DB_PATH=/data/clipforge.db` are still active.
- `DATABASE_URL` is still unset in Cloud Run.
- A Cloud SQL PostgreSQL 16 test rehearsal has been completed and the test instance was deleted afterward.
- Production data has not been migrated to PostgreSQL.

This update reduces simultaneous writer risk, but it does not make SQLite on GCSFuse production-safe.

Until the database is moved off GCSFuse, the safest operational posture is to avoid production writes.

Recommended temporary safeguards:

1. Set Cloud Run max instances to `1`.
2. Set Cloud Run container concurrency to `1`.
3. Keep V3 visible only for read-only/internal validation, or disable V3 write routes until a database migration is complete.
4. Do not run paid generation from Cloud Run while SQLite remains on GCSFuse.
5. Avoid rollouts during active writes.
6. Do not run write-heavy smoke tests against the current production database.
7. Back up `gs://clipforge-tools-data/clipforge.db` before any database architecture change.

These are risk-reduction measures, not a production solution.

## 8. Solution Comparison

### Solution A: Continue SQLite + GCSFuse

Recommendation: reject for production writes.

Problems:

- SQLite file locks are not a reliable distributed database concurrency mechanism on object-storage FUSE.
- WAL requires safe coordination with `-wal` and `-shm` files.
- GCSFuse logs already show `clipforge.db-shm` out-of-order writes.
- Multi-instance Cloud Run can write the same DB path concurrently.
- Revision rollout can briefly create cross-revision writers.
- Recovery after partial WAL/SHM issues is operationally fragile.

This setup may appear to work for light reads and occasional writes, but it is not a defensible data layer for ClipForge V3 real-provider state.

### Solution B: SQLite In `/tmp`

Recommendation: acceptable only for disposable local-like smoke tests.

Pros:

- `/tmp` is local to the Cloud Run instance.
- SQLite locking and WAL behavior are closer to expected local filesystem semantics.
- Performance is likely better than GCSFuse.

Cons:

- Data disappears on instance restart.
- Multiple instances do not share the same database.
- Revision rollout creates separate databases.
- Not suitable for durable jobs, Take state, usage/cost records, or paid-provider recovery.

### Solution C: SQLite + Single Instance + Persistent Disk

Recommendation: not the right target for this Cloud Run service.

Cloud Run has no ordinary attachable persistent disk model equivalent to a VM block device for SQLite. A single-instance/concurrency-1 setup can reduce immediate risk, but with the current mounted GCS bucket it still does not provide true local-disk SQLite semantics. If a VM or another platform with a real persistent disk were used, SQLite could be viable for a small single-writer tool, but that would be a deployment architecture change away from the current Cloud Run shape.

### Solution D: Cloud SQL PostgreSQL

Recommendation: target architecture.

Pros:

- Designed for concurrent writes from Cloud Run.
- Handles multiple instances and revision rollouts.
- Supports managed backups, point-in-time recovery options, monitoring, and IAM/Secret Manager integration.
- Uses standard `DATABASE_URL` patterns.
- Avoids storing paid-provider state in an object-storage-mounted file.
- Lets local development and tests continue using SQLite while Cloud Run uses PostgreSQL.

Cons:

- Requires a database abstraction/migration pass.
- Current code is SQLite-specific.
- Adds Cloud SQL cost and operational configuration.
- Requires schema migration planning and production data export/import.

### Solution E: Other Managed PostgreSQL

Recommendation: viable fallback if Cloud SQL is not desired.

Any managed PostgreSQL that supports a standard `DATABASE_URL` can work, provided Cloud Run networking, TLS, credentials, backups, and monitoring are configured. This offers flexibility, but Cloud SQL is the more native Google Cloud option for this deployment.

## 9. Recommended Target Architecture

Use:

```text
Local development and automated tests -> SQLite
Cloud Run production/alpha online state -> PostgreSQL
```

Configuration:

```text
DATABASE_URL=sqlite:///...                  # local/test
DATABASE_URL=postgresql+psycopg://...       # Cloud Run
```

The app should stop using `DB_URL` as the primary future setting and standardize on `DATABASE_URL`, while maintaining backward compatibility for existing local SQLite settings if needed.

Cloud Run should use Cloud SQL PostgreSQL before enabling public write workflows for V3.

## 10. PostgreSQL Migration Impact

The current code is not database-portable. A PostgreSQL migration will touch multiple areas:

- `db.py`
  - currently imports `sqlite3`
  - uses `sqlite3.Row`
  - rejects non-SQLite `DB_URL`
  - sets SQLite PRAGMAs
  - uses `?` placeholders
  - uses `AUTOINCREMENT` table definitions
- `clipforge_v3/migrations.py`
  - uses SQLite DDL and `PRAGMA table_info`
  - uses `INSERT OR IGNORE`
  - relies on SQLite-style schema inspection
- `clipforge_v3/repositories/*.py`
  - use `get_conn()`
  - use `?` placeholders
  - use `sqlite3.IntegrityError`
  - use `cur.lastrowid`
  - use SQLite row behavior
- `db.py` Legacy repositories
  - same placeholder and row assumptions
  - Legacy tables share the same database and must be migrated or isolated
- Tests
  - current fixtures assume SQLite
  - should keep SQLite for fast local tests, with a separate PostgreSQL compatibility suite when Cloud SQL is introduced

PostgreSQL-specific replacements likely needed:

- `?` placeholders -> `%s` or SQLAlchemy/text abstraction
- `INTEGER PRIMARY KEY AUTOINCREMENT` -> identity/serial columns
- `cur.lastrowid` -> `RETURNING id`
- `INSERT OR IGNORE` -> `INSERT ... ON CONFLICT DO NOTHING`
- `PRAGMA table_info` -> information schema or migration tool metadata
- `sqlite3.IntegrityError` -> driver-specific integrity error abstraction

The migration should avoid a broad rewrite of business logic, but it needs a real database access abstraction or a small repository adapter layer.

## 11. Step-By-Step Migration Plan

1. Freeze Cloud Run production writes or set max instances/concurrency to 1 as a temporary guard.
2. Back up `gs://clipforge-tools-data/clipforge.db`.
3. Use the implemented `DATABASE_URL` abstraction in `db.py` for a disposable PostgreSQL test database.
4. Run schema creation, migration repeatability, repository idempotency, export/import, and validation checks against PostgreSQL.
5. Create Cloud SQL PostgreSQL instance and database.
6. Export current SQLite data and import into PostgreSQL with validation.
7. Deploy Cloud Run with `DATABASE_URL` pointing to Cloud SQL and without SQLite-on-GCSFuse as the active DB.
8. Validate reads and safe writes on staging/alpha.
9. Only after validation, allow V3 write workflows and real-provider operations from the deployed service.

## 12. Rollback Strategy

Before migration:

- Preserve the current Cloud Run revision.
- Preserve the current GCS database object.
- Export SQLite data to an immutable backup location.

During migration:

- Deploy PostgreSQL support behind configuration.
- Keep local SQLite support.
- Do not delete the GCS SQLite database.
- Validate data counts and key records after import.

Rollback options:

- Route traffic back to the prior Cloud Run revision.
- Restore `DATA_DIR=/data` and SQLite settings only for read-only emergency access.
- Do not perform new writes into both SQLite and PostgreSQL unless a formal dual-write reconciliation plan exists.

## 13. Cloud SQL Test Rehearsal Update

The disposable PostgreSQL test rehearsal has been completed and documented in `docs/clipforge-v3/CLOUD_SQL_TEST_REHEARSAL.md`.

Important results:

- A Cloud SQL PostgreSQL 16 test instance was created, used, and deleted.
- Cloud SQL Auth Proxy connection worked from local rehearsal tooling.
- Representative SQLite export/import/validation passed.
- A read-only production SQLite copy passed integrity check and export/dry-run/validate-only.
- Production Cloud Run still has no `DATABASE_URL`.
- Production Cloud Run still uses `/data/clipforge.db` on GCSFuse.
- Production data has not been migrated.

## 14. Single Next Recommended Task

**In a maintenance window, create a consistent SQLite backup, migrate to a final Cloud SQL PostgreSQL instance, and switch Cloud Run `DATABASE_URL` to Cloud SQL with a clear rollback plan.**

Do not run Ark, Seedance, paid generation, or production write tests until the database persistence risk is addressed or temporary safeguards are explicitly applied.

Temporary safeguard runbook: `docs/clipforge-v3/CLOUD_RUN_TEMPORARY_DATABASE_SAFEGUARDS.md`.
