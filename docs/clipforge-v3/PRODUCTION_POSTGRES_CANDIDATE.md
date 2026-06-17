# ClipForge V3 Production PostgreSQL Candidate Attempt

## Summary

Date: 2026-06-17

Baseline commit: `13b9d53963c5344adde52b22282af0bdb6d83cc6`

This operations pass created a production-candidate Cloud SQL PostgreSQL instance and proved that a final SQLite snapshot can be taken under a real maintenance write freeze. The PostgreSQL traffic cutover was not performed. The migration is blocked because the production SQLite `jobs` table contains historical Legacy columns that the current PostgreSQL schema initialization does not create.

No Ark or Seedance task was created. No PostgreSQL Cloud Run revision received production traffic.

## Cloud SQL Candidate

- Project: `gen-lang-client-0817070175`
- Region: `us-central1`
- Instance: `clipforge-pg-prod`
- Database: `clipforge`
- Application user: `clipforge_app`
- PostgreSQL version: `POSTGRES_16`
- Edition: `ENTERPRISE`
- Tier: `db-custom-1-3840`
- Storage: `10GB` SSD with auto-increase
- Availability: zonal
- Backups: enabled
- Point-in-time recovery: enabled
- Deletion protection: enabled
- Authorized networks: none configured

Secret Manager entries created:

- `clipforge-database-password`
- `clipforge-database-url`

The Cloud Run runtime service account was granted Cloud SQL Client and Secret Manager access to these secrets. Secret values were not printed or committed.

## Maintenance Freeze Result

The first env-only update created revision `clipforge-tools-00105-q7t`, but it did not enforce write freeze because the old production image did not include the maintenance middleware. A source deployment from the current branch created revision `clipforge-tools-00106-gnn` with `CLIPFORGE_MAINTENANCE_MODE=true`.

Validated behavior on `clipforge-tools-00106-gnn`:

- `GET /`: HTTP `200`
- `GET /v3/ready`: HTTP `200`
- `POST /v3/projects`: HTTP `503` with `maintenance_mode`
- `DELETE /api/templates/1`: HTTP `503` with `maintenance_mode`
- `DATABASE_URL`: absent

After the migration blocker was found, traffic was restored to the preserved SQLite revision:

- Restored traffic: `100%` to `clipforge-tools-00104-hwm`
- Root path: HTTP `200`
- `/v3/ready`: HTTP `200`

## Data Safety Note

Before the source-deployed maintenance revision was available, one verification request against the env-only revision returned HTTP `200` for `DELETE /api/templates/1`, proving the old image did not enforce maintenance mode. The final snapshot showed `templates: 0`. Operators should treat this as a possible production data change and verify whether template row `1` was expected to exist before any future cutover.

## SQLite Snapshot

The final candidate snapshot was created after the working maintenance revision was active.

Snapshot prefix:

```text
gs://clipforge-tools-data/backups/production-postgres-candidate/20260617T094614Z/
```

Files uploaded:

- `clipforge-prod-candidate.db`
- `clipforge-prod-candidate.manifest.json`
- `clipforge-prod-candidate.validation.json`

Snapshot result:

- Integrity: `ok`
- Validation: `PASS`
- SHA-256: `b45e19c8ddd752c1c9005137e675d22a0a5d4772aa93cf464b38dfeb88da8b31`
- Size: `462848` bytes

Selected row counts:

- `jobs`: `18`
- `clips`: `30`
- `usage_events`: `25`
- `templates`: `0`
- `v3_projects`: `0`
- `v3_generation_submissions`: `0`
- `v3_takes`: `0`
- `v3_usage_events`: `0`

## Export And Import Result

SQLite export completed:

- Export tables: `21`
- Export rows total: `81`

Dry-run PostgreSQL import passed table emptiness checks. Formal import failed before commit and should be treated as not completed.

Blocking error:

```text
psycopg.errors.UndefinedColumn: column "creative_prompt" of relation "jobs" does not exist
```

The production SQLite export includes historical Legacy `jobs` columns such as `creative_prompt`. Current PostgreSQL schema initialization does not create those columns. Therefore the migration cannot proceed until Legacy schema compatibility is fixed and re-validated against the production snapshot.

## PostgreSQL Validation Status

Confirmed:

- Cloud SQL instance exists and accepts psycopg connections through Cloud SQL Auth Proxy.
- Empty Legacy schema initialization completed.
- V3 schema migration completed.
- Snapshot/export tooling works on the production candidate snapshot.

Not confirmed:

- Formal import into candidate PostgreSQL.
- Migration validation against candidate PostgreSQL.
- Schema compare against candidate PostgreSQL after import.
- 0% Cloud Run PostgreSQL tagged revision.
- PostgreSQL read-only app validation through a tag URL.

## Current Runtime State After Attempt

- Production traffic remains on SQLite revision `clipforge-tools-00104-hwm`.
- The production traffic-serving revision does not have `DATABASE_URL`.
- PostgreSQL candidate instance remains created for the next attempt.
- No production traffic was routed to PostgreSQL.
- No Ark or Seedance request was made.

## Required Fix Before Next Attempt

Single next task:

Fix Legacy PostgreSQL schema compatibility for all historical production SQLite columns, then rerun the snapshot export/import/validation against the existing production snapshot before attempting any 0% PostgreSQL Cloud Run revision.

Minimum scope:

- Compare production SQLite `jobs`, `clips`, `usage_events`, and other Legacy tables against PostgreSQL schema.
- Add missing Legacy columns to schema initialization and migrations.
- Add a migration-tool test using a fixture that includes `creative_prompt` and other historical columns.
- Re-run full SQLite tests.
- Re-run PostgreSQL migration rehearsal against the candidate Cloud SQL database.
- Do not route traffic to PostgreSQL until formal import and validation pass.
