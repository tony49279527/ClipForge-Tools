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
- Cloud Run has temporary SQLite/GCSFuse safeguards: container concurrency is `1` and max instances is `1`.
- Maintenance write-freeze support is implemented behind `CLIPFORGE_MAINTENANCE_MODE=false` by default. When enabled, read-only GET pages and health checks remain available while business write requests, queue enqueue, and provider-generation worker starts are blocked.
- Cloud Run database persistence is still not production-safe: `/data/clipforge.db` is on a GCSFuse mount, and earlier SQLite WAL/SHM activity produced repeated `clipforge.db-shm` out-of-order write errors.
- Offline integrity check of a copied `clipforge.db` returned `ok`, but that only proves the copied main database file was readable at audit time; it does not make GCSFuse-backed SQLite safe for writes.
- `DATABASE_URL`-driven SQLite/PostgreSQL backend selection is now implemented in code using SQLAlchemy Core and `psycopg`, while local development and automated tests continue to default to SQLite.
- Key V3 repository paths for projects, assets, shots, prompt versions, generation submissions, Takes, and generation usage events now use named-parameter database helpers instead of relying only on SQLite-style `?` conversion.
- GitHub Actions PostgreSQL integration workflow is configured with a PostgreSQL 16 service container. Local runs skip PostgreSQL integration unless `POSTGRES_TEST_DATABASE_URL` is set.
- SQLite export, PostgreSQL import, schema compare, and migration validation rehearsal tools exist under `scripts/v3/`.
- A Cloud SQL PostgreSQL 16 test instance rehearsal has been completed and the test instance was deleted afterward.
- Production Cloud Run has not been switched to PostgreSQL, no production Cloud SQL database is active, and production SQLite data has not been migrated.

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
- Legacy routes: `3 passed`
- Real R2 smoke script: `scripts/v3/test_real_r2_storage.py`
- Cloud Run database risk audit: `docs/clipforge-v3/CLOUD_RUN_DATABASE_RISK.md`
- PostgreSQL migration plan: `docs/clipforge-v3/POSTGRESQL_MIGRATION.md`
- Temporary Cloud Run database safeguards: `docs/clipforge-v3/CLOUD_RUN_TEMPORARY_DATABASE_SAFEGUARDS.md`
- Cloud Run Secret Manager hardening: `docs/clipforge-v3/CLOUD_RUN_SECRET_HARDENING.md`
- Cloud SQL PostgreSQL test rehearsal: `docs/clipforge-v3/CLOUD_SQL_TEST_REHEARSAL.md`
- Maintenance write-freeze tests: `tests/v3/test_maintenance_mode.py`

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
8. In a maintenance window, create a consistent SQLite backup, migrate to a final Cloud SQL PostgreSQL instance, and switch a new Cloud Run revision to Secret Manager-backed `DATABASE_URL`.
9. Do not run paid generation from the deployed V3 UI until database persistence is moved off GCSFuse-backed SQLite or an explicit temporary paid-test procedure is approved.

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
- Production Cloud SQL PostgreSQL instance and cutover
- Production Cloud Run switch to PostgreSQL
- R2 token rotation after migration from plaintext Cloud Run env vars to Secret Manager
- Batch real-product validation
- Long-running Worker tests
- External user authentication
- Production deployment
