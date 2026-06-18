# ClipForge V3 Final PostgreSQL Cutover Handoff

Date: 2026-06-18

This handoff is for the next short maintenance window. It does not authorize an automatic traffic switch. Do not run the final cutover unless the operator explicitly confirms it in the terminal.

## Current Online State

- Google Cloud project: `gen-lang-client-0817070175`
- Region: `us-central1`
- Web service: `clipforge-tools`
- Current production revision: `clipforge-tools-00104-hwm`
- Current production traffic: `100%` to SQLite revision `clipforge-tools-00104-hwm`
- PostgreSQL candidate tag: `pg-candidate`
- PostgreSQL candidate revision: `clipforge-tools-pg-worker-ready`
- PostgreSQL candidate traffic: `0%`
- Worker service: `clipforge-tools-worker`
- Worker revision: `clipforge-tools-worker-00007-zvn`
- Redis instance: `clipforge-redis-prod`
- Cloud SQL instance: `clipforge-pg-prod`
- Cloud SQL database: `clipforge`
- R2 backend: ready

Latest read-only candidate checks:

- `GET /`: ready on candidate tag
- `GET /v3`: ready on candidate tag
- `GET /v3/ready`: `ok=true`
- Database: `ok`
- Storage: `r2`
- Redis: `ok`
- Worker: `ok`
- Production traffic was not switched during this preparation pass.

## Final Cutover Still Required

Because production traffic has remained on SQLite after the previous candidate import, PostgreSQL is a candidate copy, not a guaranteed current copy. Final cutover still requires a fresh maintenance-window snapshot and import.

Remaining steps:

1. Enter maintenance mode on the SQLite production path.
2. Confirm no active real Ark/Seedance generation or critical write is running.
3. Pause or keep workers prevented from starting new write/provider tasks.
4. Create a fresh consistent SQLite snapshot.
5. Export the fresh snapshot.
6. Clear/rebuild the PostgreSQL candidate database or use an explicitly approved empty target.
7. Import the fresh export into PostgreSQL.
8. Validate row counts, primary keys, hashes, constraints, sequences, and representative reads.
9. Re-check `pg-candidate` at `0%`.
10. After terminal confirmation only, move `100%` traffic to the PostgreSQL revision in one step.
11. Keep maintenance mode enabled until final read-only checks pass.
12. Disable maintenance mode only after human approval.

## Final Snapshot Commands

Run inside the maintenance window against the production SQLite database path:

```bash
TS="$(date -u +%Y%m%dT%H%M%SZ)"
SNAPSHOT_DIR="/tmp/clipforge-final-cutover-${TS}"
mkdir -p "$SNAPSHOT_DIR"

python scripts/v3/create_consistent_sqlite_snapshot.py \
  --source /data/clipforge.db \
  --output "$SNAPSHOT_DIR/clipforge-final.db" \
  --manifest "$SNAPSHOT_DIR/clipforge-final.manifest.json"

python scripts/v3/create_consistent_sqlite_snapshot.py \
  --source /data/clipforge.db \
  --output "$SNAPSHOT_DIR/clipforge-final.db" \
  --manifest "$SNAPSHOT_DIR/clipforge-final.validation.json" \
  --validate-only
```

Upload the snapshot and manifests to a timestamped backup location. Do not overwrite previous backups. Do not commit these files.

## Export, Import, And Validation Commands

Use a Secret Manager-backed PostgreSQL URL in the shell environment without printing it.

```bash
EXPORT_DIR="$SNAPSHOT_DIR/export"

python scripts/v3/export_sqlite_for_postgres.py \
  --source-sqlite "$SNAPSHOT_DIR/clipforge-final.db" \
  --output-dir "$EXPORT_DIR"

python scripts/v3/import_postgres_from_export.py \
  --source-dir "$EXPORT_DIR" \
  --target-database-url "$POSTGRES_CANDIDATE_URL" \
  --dry-run

python scripts/v3/import_postgres_from_export.py \
  --source-dir "$EXPORT_DIR" \
  --target-database-url "$POSTGRES_CANDIDATE_URL" \
  --execute-confirm I_UNDERSTAND_THIS_WRITES_TO_POSTGRES

python scripts/v3/validate_database_migration.py \
  --source-dir "$EXPORT_DIR" \
  --postgres-url "$POSTGRES_CANDIDATE_URL"

python scripts/v3/compare_database_schema.py \
  --sqlite-url "$SNAPSHOT_DIR/clipforge-final.db" \
  --postgres-url "$POSTGRES_CANDIDATE_URL"
```

Do not print or commit the PostgreSQL URL.

## Candidate Readiness Commands

```bash
curl -sS --max-time 20 "https://pg-candidate---clipforge-tools-znaw4q4ldq-uc.a.run.app/"
curl -sS --max-time 20 "https://pg-candidate---clipforge-tools-znaw4q4ldq-uc.a.run.app/v3"
curl -sS --max-time 20 "https://pg-candidate---clipforge-tools-znaw4q4ldq-uc.a.run.app/v3/ready"
```

Required:

- database `ok`
- storage backend `r2`
- Redis `ok`
- worker `ok`
- candidate remains `0%` traffic before final approval
- production traffic remains on `clipforge-tools-00104-hwm` before final approval

## Final Traffic Switch Command

Do not run this command unless all validation passes and the operator types the required confirmation:

```text
YES_CUTOVER_CLIPFORGE_TO_POSTGRES
```

One-step cutover command:

```bash
gcloud run services update-traffic clipforge-tools \
  --project=gen-lang-client-0817070175 \
  --region=us-central1 \
  --to-revisions clipforge-tools-pg-worker-ready=100
```

Partial traffic splits such as `5%`, `25%`, or `50%` are forbidden for this database cutover.

## Disable Maintenance Mode After Validation

After traffic is on PostgreSQL and read-only checks pass, deploy or update the PostgreSQL revision with:

```text
CLIPFORGE_MAINTENANCE_MODE=false
```

Do this only after a human confirms that the PostgreSQL revision is healthy and writes should resume.

## Failure Handling

Stop and do not switch traffic if any of these occur:

- snapshot integrity is not `ok`
- export/import/validation/schema compare fails
- Redis or worker readiness fails
- candidate tag returns `500`
- logs show Secret leakage
- active real provider generation is detected
- Cloud Run candidate is not Ready

If PostgreSQL has not accepted new writes, keep or return traffic to `clipforge-tools-00104-hwm` and preserve the fresh SQLite snapshot.

If PostgreSQL has accepted new writes, do not blindly roll back to SQLite. Keep maintenance mode enabled, preserve PostgreSQL data, compare differences, and do a forward repair or explicit reconciliation.

## Absolute Safety Rules

- Do not delete SQLite.
- Do not delete Cloud SQL.
- Do not delete Redis.
- Do not run Ark/Seedance during cutover.
- Do not create video generation tasks.
- Do not commit snapshots, exports, logs, database files, URLs, passwords, or Secret values.
- Do not switch traffic without the final terminal confirmation.
