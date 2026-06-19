# ClipForge 3.0 Current Status

## Current State

- Current development branch: `clipforge-v3-real-provider-alpha`
- Current branch HEAD at start of dual-bucket R2 pass: `20ea06c42a0e5812520105a488596fcd597df029`
- Last verified functional baseline: `b18b6974fd63f748fe37a140644f8b83c212efc8`
- ClipForge 3.0 is in Real Provider Alpha.
- Mock workflow is runnable.
- Ark Provider is wired.
- Paid confirmation is implemented.
- Idempotency protection against duplicate paid submissions is implemented and hardened.
- `unknown_submission_state` is implemented and now blocks automatic re-submit.
- HTTPS product reference images now enter the Ark Payload.
- Payload Inspector can safely construct a request without sending it.
- Take persistence is now idempotent per generation submission.
- Usage/cost persistence is now idempotent per generation submission.
- Budget check now runs before real-provider reservation for new submissions.
- New real-provider constraints include `v3_generation_submissions.budget_approved_at`, `v3_takes.generation_submission_id UNIQUE`, and `v3_usage_events.event_key UNIQUE`.
- One real paid Seedance task has been executed.
- The provider task succeeded; the first local download failed with `403` because a signed result URL was sanitized before download.
- Existing-task recovery now re-queries the provider task, preserves the runtime signed URL for download, and guarantees `NO NEW PROVIDER SUBMISSION`.
- The recovered real task downloaded successfully, created one Take, and recorded one provider generation usage/cost event.
- Real provider task count remains `1`.
- Cloudflare R2 object storage support is implemented behind `V3_STORAGE_BACKEND=r2` with LocalStorage still the default.
- R2 dual-bucket mode is supported: product reference images use `R2_PUBLIC_BUCKET_NAME`, generated videos use `R2_PRIVATE_BUCKET_NAME`, and old `R2_BUCKET_NAME` single-bucket mode remains supported for compatibility.
- Product image uploads can store `storage_backend`, `object_key`, `content_type`, `size_bytes`, and a stable HTTPS `access_url`.
- Generated provider videos can be uploaded to private R2 storage after download; Take rows can store `storage_backend`, `object_key`, `content_type`, and `size_bytes` without persisting presigned URLs.
- R2 tests use mocked S3/R2 clients in CI/local regression.
- A real R2 smoke validation has been executed with dedicated smoke-test objects: public upload/read/delete passed, private upload/presigned-read/delete passed, and test objects were cleaned up.
- Cloud Run now has `CLIPFORGE_V3_ENABLED=true`; `/`, `/v3`, and `/v3/ready` returned HTTP 200 after the Secret Manager hardening revision.
- Cloud Run sensitive environment variables are now referenced through Secret Manager in revision `clipforge-tools-00104-hwm`; same-name plaintext sensitive values were removed from the service configuration.
- Cloud Run previously used temporary SQLite/GCSFuse safeguards while preparing the cutover.
- Maintenance write-freeze support is implemented behind `CLIPFORGE_MAINTENANCE_MODE=false` by default. When enabled, read-only GET pages and health checks remain available while business write requests, queue enqueue, and provider-generation worker starts are blocked.
- Production database cutover safety tooling is implemented: consistent SQLite snapshot creation, cutover preflight checks, dry-run cutover plan, rollback boundary planning, and a production cutover runbook.
- Production cutover Go/No-Go review is recorded at `docs/clipforge-v3/PRODUCTION_CUTOVER_GO_NO_GO.md`.
- A production-candidate Cloud SQL PostgreSQL instance was created and documented at `docs/clipforge-v3/PRODUCTION_POSTGRES_CANDIDATE.md`.
- A real maintenance freeze was validated only after deploying current source as revision `clipforge-tools-00106-gnn`; the older production image did not enforce maintenance mode from an env-only revision.
- A final candidate SQLite snapshot was created and validated. The initial PostgreSQL import was blocked because the production SQLite `jobs` table contains historical Legacy columns such as `creative_prompt`; this Legacy schema parity issue has now been fixed in commit `e78713046c53872d2f49d1bff887f7157ce9f3af`.
- The existing production candidate snapshot was formally imported into the Cloud SQL candidate database after the schema fix; migration validation and schema compare both passed.
- A 0% tagged PostgreSQL Cloud Run candidate revision `clipforge-tools-00109-wij` now starts successfully with Secret Manager-backed `DATABASE_URL`; it has not received production traffic.
- The first PostgreSQL candidate revision `clipforge-tools-00108-sir` failed startup because the `DATABASE_URL` Secret had a malformed Cloud SQL Unix socket host. Secret version `2` corrected the URL without printing or committing the value.
- The `clipforge-tools-00109-wij` tag returned HTTP `200` for `/` and `/v3/ready`; `/v3/ready` reported database `ok`, Redis/worker unavailable, and storage backend `local` despite R2 environment variables being present. This was traced to readiness reporting using the legacy `STORAGE_BACKEND` variable instead of the actual V3 storage adapter.
- Follow-up revision `clipforge-tools-pg-r2ready` was tagged `pg-candidate` at `0%` traffic and verified `storage.backend=r2` and `configured_backend=r2` on `/v3/ready`.
- Redis/worker production candidate was provisioned: Memorystore Redis `clipforge-redis-prod`, Secret Manager `clipforge-redis-url`, independent Cloud Run worker service `clipforge-tools-worker`, and PostgreSQL web candidate revision `clipforge-tools-pg-worker-ready` with Redis/worker readiness passing on `/v3/ready`.
- Non-paid queue smoke passed through Cloud Run Job `clipforge-queue-smoke`: ordinary RQ job finished, retry job intentionally failed once and then finished, failed-registry check passed, and temporary Redis keys/jobs were cleaned up by the smoke script.
- Traffic was temporarily restored to the preserved SQLite revision `clipforge-tools-00104-hwm` after an early import blocker was found.
- The old SQLite/GCSFuse database path remains preserved for audit and emergency reference, but it is no longer the active production database after the PostgreSQL cutover.
- Offline integrity check of a copied `clipforge.db` returned `ok`; the final cutover snapshot also returned integrity `ok`.
- `DATABASE_URL`-driven SQLite/PostgreSQL backend selection is now implemented in code using SQLAlchemy Core and `psycopg`, while local development and automated tests continue to default to SQLite.
- Key V3 repository paths for projects, assets, shots, prompt versions, generation submissions, Takes, and generation usage events now use named-parameter database helpers instead of relying only on SQLite-style `?` conversion.
- GitHub Actions PostgreSQL integration workflow is configured with a PostgreSQL 16 service container. Local runs skip PostgreSQL integration unless `POSTGRES_TEST_DATABASE_URL` is set.
- SQLite export, PostgreSQL import, schema compare, and migration validation rehearsal tools exist under `scripts/v3/`.
- A Cloud SQL PostgreSQL 16 test instance rehearsal has been completed and the test instance was deleted afterward.
- Production Cloud Run has been switched to PostgreSQL. Final cutover result: `docs/clipforge-v3/PRODUCTION_POSTGRES_CUTOVER_RESULT.md`.
- Current production web revision is `clipforge-tools-00115-kay` with `100%` traffic.
- Current production database is Cloud SQL PostgreSQL `clipforge-pg-prod/clipforge`.
- Current production storage backend is R2.
- Current production Redis/worker readiness is passing.
- Maintenance mode is disabled on the production revision.
- Production V3 remains in mock-provider mode: `V3_VIDEO_PROVIDER=mock` and `V3_REAL_API_ENABLED=false`.
- Guided V3 UI demo flow is now available on `/v3`.
- The guided demo supports project creation, product info editing, demo image selection or image upload, mock prompt generation, mock take generation, and inline result preview.
- The guided demo is explicitly mock-only, does not call Ark / Seedance, and keeps demo take cost at `0`.
- The guided V3 UI demo tag remains available at: `https://v3-ui-demo---clipforge-tools-znaw4q4ldq-uc.a.run.app/v3`.
- The current guided demo revision is `clipforge-tools-00115-kay` with tag `v3-ui-demo`.
- The default production traffic is now `100%` on PostgreSQL revision `clipforge-tools-00115-kay`.
- The deployed tag was smoke-tested through the guided mock flow: `/v3` returned `200`, `/v3/ready` returned `200`, project creation succeeded, Product Truth save succeeded, demo image creation succeeded, mock prompt generation succeeded, mock take generation succeeded, and the final result page showed `Provider: mock` with cost `0`.

## Verified Commands

```bash
python3 -m pytest -q tests/v3
python3 -m pytest -q tests/test_legacy_routes.py
python3 -m compileall clipforge_v3 scripts/v3
python3 scripts/v3/inspect_real_seedance_payload.py
```

Current recorded results:

- V3: `84 passed`
- V3 after download recovery hardening: `101 passed`
- Real Provider Alpha: `37 passed`
- V3 after dual-bucket R2 support: `118 passed`
- Object storage tests after dual-bucket R2 support: `17 passed`
- Object storage targeted selector after dual-bucket R2 support: `38 passed, 80 deselected`
- Guided V3 demo UI tests: `3 passed`
- Guided V3 demo Cloud Run tag smoke: `/v3 200`, `/v3/ready 200`, project/product/demo image/prompt/mock generate/result all passed, provider `mock`, cost `0`
- Legacy routes: `3 passed`
- Real R2 smoke script: `scripts/v3/test_real_r2_storage.py`
- Cloud Run database risk audit: `docs/clipforge-v3/CLOUD_RUN_DATABASE_RISK.md`
- PostgreSQL migration plan: `docs/clipforge-v3/POSTGRESQL_MIGRATION.md`
- Temporary Cloud Run database safeguards: `docs/clipforge-v3/CLOUD_RUN_TEMPORARY_DATABASE_SAFEGUARDS.md`
- Cloud Run Secret Manager hardening: `docs/clipforge-v3/CLOUD_RUN_SECRET_HARDENING.md`
- Cloud SQL PostgreSQL test rehearsal: `docs/clipforge-v3/CLOUD_SQL_TEST_REHEARSAL.md`
- Maintenance write-freeze tests: `tests/v3/test_maintenance_mode.py`
- Cutover safety tools: `scripts/v3/create_consistent_sqlite_snapshot.py`, `scripts/v3/preflight_production_postgres_cutover.py`, `scripts/v3/cutover_sqlite_to_cloudsql.py`, `scripts/v3/plan_postgres_cutover_rollback.py`
- Production database cutover runbook: `docs/clipforge-v3/PRODUCTION_DATABASE_CUTOVER_RUNBOOK.md`
- Production cutover Go/No-Go review: `docs/clipforge-v3/PRODUCTION_CUTOVER_GO_NO_GO.md`
- Production PostgreSQL candidate attempt: `docs/clipforge-v3/PRODUCTION_POSTGRES_CANDIDATE.md`
- Production PostgreSQL cutover result: `docs/clipforge-v3/PRODUCTION_POSTGRES_CUTOVER_RESULT.md`

## Safe Payload Inspection

```bash
export V3_REAL_TEST_IMAGE_URL="https://public-product-image-url"
python3 scripts/v3/inspect_real_seedance_payload.py
```

This inspector:

- Does not call paid APIs.
- Does not call `submit_task`.
- Does not call `requests.post`.

## Next Tasks

1. Review the readiness audit: `docs/clipforge-v3/REAL_PROVIDER_READINESS_AUDIT.md`
2. Review the object storage design: `docs/clipforge-v3/OBJECT_STORAGE.md`
3. Review the Cloud Run database risk audit: `docs/clipforge-v3/CLOUD_RUN_DATABASE_RISK.md`
4. Review the PostgreSQL migration plan: `docs/clipforge-v3/POSTGRESQL_MIGRATION.md`
5. Review temporary database safeguards: `docs/clipforge-v3/CLOUD_RUN_TEMPORARY_DATABASE_SAFEGUARDS.md`
6. Review Cloud Run Secret Manager hardening: `docs/clipforge-v3/CLOUD_RUN_SECRET_HARDENING.md`
7. Review Cloud SQL PostgreSQL test rehearsal: `docs/clipforge-v3/CLOUD_SQL_TEST_REHEARSAL.md`
8. Review the production database cutover runbook: `docs/clipforge-v3/PRODUCTION_DATABASE_CUTOVER_RUNBOOK.md`
9. Review the production cutover result: `docs/clipforge-v3/PRODUCTION_POSTGRES_CUTOVER_RESULT.md`
10. Gather operator click feedback from the live `/v3` guided demo page before doing a broader UI polish pass.
11. Monitor Cloud Run, Cloud SQL, Redis, worker, and R2 logs after real user writes resume.
12. Do not enable real Ark/Seedance production generation until a separate paid-provider release procedure is approved.

## Real Paid Test Protection

Required environment:

```bash
export V3_VIDEO_PROVIDER=ark
export V3_REAL_API_ENABLED=true
export V3_REAL_API_TEST_CONFIRM=I_UNDERSTAND_THIS_COSTS_MONEY
export V3_REAL_TEST_IMAGE_URL=https://...
```

The terminal must also receive:

```text
YES_PAY_SEEDANCE_ONCE
```

Automatic repeated tests are forbidden.

## Not Complete

- Private R2 video playback/download UI integration
- R2 token rotation after migration from plaintext Cloud Run env vars to Secret Manager
- Batch real-product validation
- Long-running Worker tests
- External user authentication
- Real-provider production enablement
