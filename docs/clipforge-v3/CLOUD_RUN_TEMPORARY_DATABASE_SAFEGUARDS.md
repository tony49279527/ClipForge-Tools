# Cloud Run Temporary Database Safeguards

These safeguards are for the period before ClipForge Cloud Run writes are moved from GCSFuse-backed SQLite to PostgreSQL. They reduce risk but do not make SQLite on GCSFuse production-safe.

## Why This Is Needed

Current audited topology:

- SQLite database: `/data/clipforge.db`
- `/data` is a GCSFuse mount
- Legacy and V3 share the same SQLite database
- Cloud Run concurrency was `80`
- max instances was `20`
- logs showed `clipforge.db-shm` out-of-order writes and GCS API `429` retries

SQLite WAL/SHM writes on GCSFuse are not safe for concurrent online writes. Page GET health checks do not prove writes are safe.

## Recommended Temporary Settings

Before any online write activity while still on SQLite/GCSFuse:

- Set max instances to `1`
- Set container concurrency to `1`
- Pause public/external V3 write operations
- Do not run real paid Seedance generation from Cloud Run
- Stop or avoid long-running background workers that can write concurrently
- Back up the SQLite database before deployment or test windows
- Avoid deploying a new Revision while write workflows are active

## Example Commands

Do not run these automatically. Review service and project first.

```bash
gcloud run services update clipforge-tools \
  --region us-central1 \
  --concurrency 1 \
  --max-instances 1
```

To keep V3 visible but block paid writes, prefer application-level gates and operational policy until PostgreSQL is ready. Do not rely on UI visibility alone as a safety boundary.

## SQLite Backup Before Write Windows

Use a copied database file for integrity checks. Do not run destructive checks against the mounted live file during traffic.

```bash
gcloud storage cp gs://clipforge-tools-data/clipforge.db ./clipforge-db-backup-$(date +%Y%m%d-%H%M%S).db
sqlite3 ./clipforge-db-backup-YYYYMMDD-HHMMSS.db "PRAGMA integrity_check;"
```

Do not commit database backups to Git.

## Revision Switching Risk

During a Cloud Run rollout, an old and new Revision can overlap. If both write the same SQLite database on GCSFuse, corruption and lock-risk increase. Avoid deployments during write tests, or block writes before deploying.

## What These Safeguards Do Not Solve

- They do not make GCSFuse file locking equivalent to a database server.
- They do not protect against all Revision overlap windows.
- They do not provide point-in-time database recovery.
- They do not support safe multi-user production writes.

## Exit Criteria

Remove these temporary restrictions only after:

1. A dedicated Cloud SQL PostgreSQL test instance has been created.
2. PostgreSQL schema initialization has passed.
3. SQLite export/import rehearsal has passed.
4. Migration validation has passed.
5. Cloud Run has been deployed with a PostgreSQL `DATABASE_URL`.
6. Read and limited write smoke checks have passed.
