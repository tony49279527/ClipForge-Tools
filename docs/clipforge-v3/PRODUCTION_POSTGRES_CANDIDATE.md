# ClipForge V3 Production PostgreSQL Candidate Attempt

## Summary

Date: 2026-06-17

Baseline commit: `13b9d53963c5344adde52b22282af0bdb6d83cc6`
Follow-up schema parity commit: `e78713046c53872d2f49d1bff887f7157ce9f3af`

This operations pass created a production-candidate Cloud SQL PostgreSQL instance and proved that a final SQLite snapshot can be taken under a real maintenance write freeze. The PostgreSQL traffic cutover was not performed. The initial migration attempt was blocked because the production SQLite `jobs` table contained historical Legacy columns that PostgreSQL schema initialization did not create. That schema parity blocker was fixed in `e78713046c53872d2f49d1bff887f7157ce9f3af`, and the existing production candidate snapshot was then imported and validated against the Cloud SQL candidate database.

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

Initial dry-run PostgreSQL import passed table emptiness checks. The first formal import failed before commit and should be treated as not completed.

Initial blocking error:

```text
psycopg.errors.UndefinedColumn: column "creative_prompt" of relation "jobs" does not exist
```

The production SQLite export includes historical Legacy `jobs` columns such as `creative_prompt`. The blocker was resolved by adding the missing production-history columns to Legacy schema initialization and by adding regression coverage for `create_job()`.

Follow-up validation after `e78713046c53872d2f49d1bff887f7157ce9f3af`:

- Formal import into the Cloud SQL candidate database: `PASS`
- Migration validation: `PASS`
- Schema compare: `PASS`
- Schema diff count: `0`
- Imported rows:
  - `jobs`: `18`
  - `clips`: `30`
  - `usage_events`: `25`
  - V3 tables: `0` rows in this production candidate snapshot

## PostgreSQL Validation Status

Confirmed:

- Cloud SQL instance exists and accepts psycopg connections through Cloud SQL Auth Proxy.
- Empty Legacy schema initialization completed.
- V3 schema migration completed.
- Snapshot/export tooling works on the production candidate snapshot.
- Historical Legacy `jobs` column parity is fixed for the exported production snapshot.
- Formal import, migration validation, and schema compare now pass on the Cloud SQL candidate database.
- A 0% tagged Cloud Run PostgreSQL candidate revision now starts successfully with a Secret Manager-backed `DATABASE_URL`.

Not confirmed:

- Production traffic cutover to PostgreSQL.
- PostgreSQL write behavior under production traffic.
- Worker/Redis readiness in the PostgreSQL candidate revision.
- R2 readiness in the PostgreSQL candidate revision: environment variables are present, but `/v3/ready` reported `Storage backend active: local` on the tag and needs a focused follow-up before traffic cutover.

## Current Runtime State After Attempt

- Production traffic remains on SQLite revision `clipforge-tools-00104-hwm`.
- The production traffic-serving revision does not have `DATABASE_URL`.
- PostgreSQL candidate instance remains created for the next attempt.
- No production traffic was routed to PostgreSQL.
- No Ark or Seedance request was made.
- A first 0% PostgreSQL candidate revision `clipforge-tools-00108-sir` failed startup because the Secret Manager `DATABASE_URL` contained a malformed Cloud SQL Unix socket host. No production traffic was routed to this revision.
- Secret `clipforge-database-url` was updated with a corrected SQLAlchemy/psycopg Cloud SQL socket URL in version `2`; the value was not printed or committed.
- A second 0% PostgreSQL candidate revision `clipforge-tools-00109-wij` deployed from image `us-central1-docker.pkg.dev/gen-lang-client-0817070175/cloud-run-source-deploy/clipforge-tools:e787130` and started successfully.
- `clipforge-tools-00109-wij` direct tag checks:
  - `GET /`: HTTP `200`
  - `GET /v3/ready`: HTTP `200`
  - Database check: `ok`
  - Redis and worker checks: unavailable, expected until Redis/worker production configuration is supplied
  - Storage check: reported `local` despite `V3_STORAGE_BACKEND=r2` and dual-bucket variables being present; investigate before any PostgreSQL traffic switch
- Traffic remains `100%` on `clipforge-tools-00104-hwm`; `clipforge-tools-00109-wij` has only the `pg-candidate` tag and `0%` traffic.

## R2 Readiness Follow-Up

Date: 2026-06-18

Root cause:

- `clipforge_v3/services/readiness_service.py` correctly instantiated storage through `get_storage()`.
- The readiness response then reported `os.getenv("STORAGE_BACKEND", "local")` instead of the actual adapter backend.
- Cloud Run had `V3_STORAGE_BACKEND=r2` and no `STORAGE_BACKEND`, so `/v3/ready` showed `local` even though the V3 R2 configuration was present.

Fix:

- Commit `742b1de0bb13bee9b3912fcb2b0d23fea6121c9c` changed readiness to report `get_storage().backend` and include `configured_backend`.
- Commit `b954fee9a089418c79dac98d4e7fbaced0e8e76d` added `.gcloudignore` so future Cloud Build contexts exclude local artifacts, databases, uploads, outputs, videos, and `.venv`.

0% candidate validation:

- New tagged revision: `clipforge-tools-pg-r2ready`
- Tag: `pg-candidate`
- Traffic: `0%`
- Production traffic: still `100%` on SQLite revision `clipforge-tools-00104-hwm`
- `GET /`: HTTP `200`
- `GET /v3`: HTTP `200`
- `GET /v3/ready`: HTTP `200`
- Database check: `ok`
- Storage check: `backend=r2`, `configured_backend=r2`
- Redis and worker checks: unavailable, still requiring production Redis/worker readiness review
- Recent revision logs: no startup failure and no detected Secret-like leakage

This resolves the storage readiness mismatch. It does not authorize traffic cutover.

## Required Fix Before Next Attempt

Single next task:

Verify Redis/worker production readiness for the PostgreSQL candidate and keep the candidate at `0%` traffic until a fresh maintenance-window snapshot/import and final cutover approval.

Minimum scope:

- Confirm the candidate has the intended Redis/queue configuration.
- Confirm workers cannot duplicate paid provider submissions after restart.
- Keep `clipforge-tools-pg-r2ready` at `0%` traffic.
- Do not route traffic to PostgreSQL until Redis/worker and maintenance/cutover gates are explicitly reviewed.
