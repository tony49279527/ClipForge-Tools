# ClipForge V3 Production PostgreSQL Cutover Result

Date: 2026-06-19

## Summary

ClipForge production web traffic has been moved off the GCSFuse-backed SQLite revision and onto a PostgreSQL-backed Cloud Run revision.

No Ark or Seedance request was made during this cutover. No video generation task was created. No paid generation cost was incurred.

## Final Runtime State

- Google Cloud project: `gen-lang-client-0817070175`
- Region: `us-central1`
- Web service: `clipforge-tools`
- Production URL: `https://clipforge-tools-znaw4q4ldq-uc.a.run.app`
- Current production revision: `clipforge-tools-00115-kay`
- Current production traffic: `100%` to `clipforge-tools-00115-kay`
- PostgreSQL candidate tag retained: `pg-candidate` -> `clipforge-tools-pg-worker-ready` at `0%`
- V3 UI demo tag retained: `v3-ui-demo` -> `clipforge-tools-00115-kay`
- Worker service: `clipforge-tools-worker`
- Worker revision: `clipforge-tools-worker-00007-zvn`
- Cloud SQL instance: `clipforge-pg-prod`
- Cloud SQL database: `clipforge`
- Redis instance: `clipforge-redis-prod`
- Storage backend: `r2`
- Maintenance mode: `false`
- V3 provider on production revision: `mock`
- Real paid API enabled: `false`

Sensitive values are stored through Secret Manager and are not recorded here.

## Maintenance Freeze And Snapshot

The cutover entered write freeze before taking the final snapshot:

- Temporary maintenance revision: `clipforge-tools-00106-gnn`
- Maintenance traffic: `100%` during snapshot/import
- `GET /`: HTTP `200`
- `POST /v3/projects`: HTTP `503` with `maintenance_mode`

The final SQLite snapshot was created by a Cloud Run Job running inside the same GCSFuse `/data` mount and using the SQLite backup API:

- Snapshot job: `clipforge-final-snap-20260619100340`
- Execution: `clipforge-final-snap-20260619100340-dpswz`
- Execution result: succeeded
- Backup prefix: `gs://clipforge-tools-data/backups/final-postgres-cutover/20260619100340/`
- Snapshot file: `clipforge-final.db`
- Manifest file: `clipforge-final.manifest.json`
- Snapshot integrity: `ok`
- Snapshot SHA-256: `16a72a32bb138b6e4b7dc0eee4880b2642e9874e6bf5b24dec66c054484b2ca3`
- Snapshot size: `462848` bytes

Selected snapshot row counts:

- `jobs`: `18`
- `clips`: `30`
- `usage_events`: `25`
- `v3_projects`: `0`
- `v3_generation_submissions`: `0`
- `v3_takes`: `0`
- `v3_usage_events`: `0`

## PostgreSQL Import And Validation

The Cloud SQL target schema was rebuilt before final import:

- `public` schema was dropped and recreated.
- Legacy schema was initialized through `db.init_db()`.
- V3 schema was initialized through `run_v3_migrations()`.
- V3 migrations applied: `8`

SQLite export:

- Export tables: `21`
- Export rows total: `81`

PostgreSQL import:

- Dry-run import: passed
- Formal import: passed
- Imported rows:
  - `jobs`: `18`
  - `clips`: `30`
  - `usage_events`: `25`
  - V3 data tables: `0` rows in the final snapshot

Validation:

- Migration validation: `PASS`
- Schema compare: `PASS`
- Schema diff count: `0`
- Duplicate idempotency key check: `PASS`
- Duplicate usage event key check: `PASS`
- Duplicate Take generation submission check: `PASS`
- Submission/Take orphan check: `PASS`

## Traffic Switch

Traffic was moved in one-step database cutover stages:

1. `100%` traffic to `clipforge-tools-00106-gnn` for SQLite write freeze.
2. Fresh SQLite snapshot/export/import/validation completed.
3. `100%` traffic to PostgreSQL maintenance revision `clipforge-tools-pg-worker-ready`.
4. Read-only checks passed.
5. `100%` traffic to PostgreSQL non-maintenance revision `clipforge-tools-00115-kay`.

Partial database traffic splitting was not used.

## Final Health Checks

After the final switch:

- `GET /`: HTTP `200`
- `GET /v3`: HTTP `200`
- `GET /v3/ready`: HTTP `200`, `ok=true`
- Database readiness: `ok`
- Redis readiness: `ok`
- Worker readiness: `ok`
- R2 storage readiness: `ok`, backend `r2`
- FFmpeg readiness: `ok`
- Invalid JSON `POST /v3/projects`: HTTP `422`, confirming maintenance middleware is no longer blocking writes while avoiding a real database write

Final PostgreSQL row counts after the non-writing validation request:

- `jobs`: `18`
- `clips`: `30`
- `usage_events`: `25`
- `v3_projects`: `0`
- `v3_generation_submissions`: `0`
- `v3_takes`: `0`
- `v3_usage_events`: `0`

No error logs matching startup failure, database lock, operational error, traceback, or Secret leakage were found in the final checked window.

## Current Safety Position

Production is now PostgreSQL-backed. The old SQLite database and final snapshot are preserved for audit and emergency reference.

The production V3 revision is still configured for mock provider mode:

- `V3_VIDEO_PROVIDER=mock`
- `V3_REAL_API_ENABLED=false`

Do not run real Ark/Seedance production generation until a separate paid-provider release procedure is approved.

## Remaining Follow-Up

- Run an operator click-through on the live `/v3` guided demo flow.
- Decide whether and when to enable real provider mode on production.
- Keep the SQLite backup and final snapshot immutable.
- Monitor Cloud Run, Cloud SQL, Redis, and worker logs after real user writes resume.
