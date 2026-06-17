# ClipForge V3 Production Cutover Go/No-Go Review

## 1. Review Date And Git Baseline

- Review date: 2026-06-17
- Repository: `tony49279527/ClipForge-Tools`
- Branch: `clipforge-v3-real-provider-alpha`
- Baseline pulled from remote: `8937ad3f5f89d8184011d5f6c181f93f944a5281`
- Review/fix commit: `86dbdec0488536631770789d22a4e14261803cf6`
- Working tree after review fixes: clean

This review did not create Cloud SQL resources, modify Cloud Run, enable maintenance mode, set production `DATABASE_URL`, migrate data, call Ark/Seedance, or run paid generation.

Follow-up on 2026-06-17: a production-candidate Cloud SQL instance was created and a candidate SQLite snapshot was validated, but formal PostgreSQL import failed because the current PostgreSQL Legacy schema is missing historical production `jobs` columns such as `creative_prompt`. Traffic was restored to the preserved SQLite revision. The current decision is `NO CUTOVER` until Legacy schema parity is fixed and the import validation passes.

## 2. Current Cloud Run State

Read-only Cloud Run checks used explicit project and region flags:

- Project: `gen-lang-client-0817070175`
- Region: `us-central1`
- Service: `clipforge-tools`
- Latest ready revision: `clipforge-tools-00104-hwm`
- Traffic: `100%` to `clipforge-tools-00104-hwm`
- Service URL: `https://clipforge-tools-znaw4q4ldq-uc.a.run.app`
- Container concurrency: `1`
- Max instances: `1`
- Runtime service account: `550177383294-compute@developer.gserviceaccount.com`
- GCSFuse volume: `clipforge-tools-data` mounted at `/data`
- `DATA_DIR`: `/data`
- `DB_PATH`: `/data/clipforge.db`
- `DATABASE_URL`: absent
- `CLIPFORGE_MAINTENANCE_MODE`: unset, effectively `false`
- `CLIPFORGE_V3_ENABLED`: `true`
- `V3_STORAGE_BACKEND`: `r2`

Safe GET checks:

- `/`: HTTP `200`
- `/v3`: HTTP `200`
- `/v3/ready`: HTTP `200`

Sensitive values were not printed. Secret references were inspected by name only.

## 3. Current SQLite/GCSFuse Risk

Production still uses SQLite at `/data/clipforge.db` on a GCSFuse mount. This remains the main blocker for production writes. Temporary safeguards are active because concurrency and max instances are both `1`, but those safeguards reduce risk only; they do not make SQLite WAL/SHM safe on object storage.

Earlier evidence of `clipforge.db-shm` out-of-order writes and GCS API `429` retries still means the current production database topology is not acceptable for normal online write traffic.

## 4. Maintenance Mode Coverage Result

Reviewed files:

- `app.py`
- `task_queue.py`
- `worker.py`
- `clipforge_v3/services/maintenance_service.py`
- `clipforge_v3/services/generation_service.py`
- `tests/v3/test_maintenance_mode.py`

Current coverage:

- All HTTP `POST`, `PUT`, `PATCH`, and `DELETE` requests are blocked by middleware when `CLIPFORGE_MAINTENANCE_MODE=true`.
- Auth/OAuth callback-like `GET` paths beginning with `/auth/` or `/oauth` are also blocked.
- Read-only routes such as `/`, `/v3`, `/v3/ready`, and `/healthz` remain available.
- Queue enqueue calls pass through `task_queue._enqueue_with_reusable_job_id()`, which calls `assert_writes_allowed()` before touching Redis.
- RQ wrapper functions call `assert_writes_allowed()` before executing Legacy, V2, V3 bootstrap, publish, or provider-generation tasks.
- `generation_service.submit_generation()` and `generation_service.process_generation_submission()` call `assert_writes_allowed()` before preflight, provider submit, Take creation, usage/cost writes, or recovery persistence.

Residual operational requirement:

- The worker process itself can still start and wait for work. This is acceptable only if queued task execution remains blocked by wrappers and operators pause workers during cutover as the runbook requires.
- Standalone operator scripts are not a substitute for maintenance mode. Any script capable of writes must be run only under the cutover procedure.

No critical uncovered HTTP or provider-submit path was found in this review.

## 5. Snapshot Tool Result

Reviewed and tested:

- `scripts/v3/create_consistent_sqlite_snapshot.py`
- `tests/v3/test_cutover_safety_tools.py`

The tool now:

- Uses SQLite backup API.
- Opens source databases through a strict read-only URI.
- Supports WAL sources.
- Excludes uncommitted transactions.
- Refuses source and target being the same path.
- Refuses overwrite unless explicitly requested.
- Cleans temporary output after failure.
- Runs `PRAGMA integrity_check`.
- Records SHA-256, size, table row counts, primary key lists, NULL counts, job status distribution, and V3 core counts.
- Redacts the snapshot path in the written manifest.

Fix applied during this review:

- Removed the previous fallback from read-only URI to normal writable SQLite connection.
- Normalized the temporary snapshot to `journal_mode=DELETE` after backup so a WAL-mode source does not create a snapshot that cannot be reopened read-only without sidecar files.

Validation on a temporary SQLite database passed:

- Snapshot created with `integrity=ok`.
- Snapshot validation returned `PASS`.
- Manifest SHA length was `64`.
- Manifest table counts were present.

No production `/data/clipforge.db` snapshot was created.

## 6. Preflight Result

Reviewed and tested:

- `scripts/v3/preflight_production_postgres_cutover.py`
- `tests/v3/test_cutover_safety_tools.py`

Fix applied during this review:

- Preflight now validates snapshot manifest contents, not only manifest file existence. It requires `integrity_check=ok`, a 64-character SHA-256, positive size, and a table-count mapping.

Read-only preflight against current Cloud Run returned `FAIL`, as expected before a real maintenance window:

Pass:

- project
- region
- service
- concurrency `1`
- max instances `1`
- current backend is SQLite
- GCSFuse mount exists
- production `DATABASE_URL` absent
- working tree clean

Fail:

- maintenance mode is not enabled
- final Cloud SQL target is not present in the preflight state
- PostgreSQL 16 target is not verified
- target PostgreSQL database is not verified empty
- database password secret for the final cutover is not verified
- final consistent SQLite snapshot manifest is not provided

Warnings:

- Cloud SQL backups not verified
- Cloud SQL same-region status not verified in the current read-only service-only preflight

This is a correct no-execution preflight result for the current non-maintenance production state.

## 7. Cutover Dry-Run Result

Reviewed and executed in dry-run mode:

- `scripts/v3/cutover_sqlite_to_cloudsql.py`

The dry-run plan includes:

- verify maintenance mode
- verify write freeze
- pause worker
- create consistent SQLite snapshot
- upload timestamped backup
- validate snapshot
- verify Cloud SQL schema
- export SQLite
- dry-run import
- formal import
- migration validation
- Secret Manager-backed `DATABASE_URL`
- tagged PostgreSQL revision at `0%` traffic
- read-only tag checks
- backend verification as PostgreSQL
- one-step `100%` traffic switch
- final read-only checks
- manual unfreeze
- SQLite backup preservation

The plan explicitly prints `PARTIAL_TRAFFIC_FOR_DATABASE_CUTOVER=FORBIDDEN` and does not implement real execution.

## 8. Rollback Boundary

Reviewed and executed:

- `scripts/v3/plan_postgres_cutover_rollback.py`

Boundary is clear:

- Before PostgreSQL accepts new writes: routing 100% traffic back to the preserved SQLite revision is allowed, while keeping maintenance mode enabled and validating the SQLite snapshot.
- After PostgreSQL accepts new writes: direct rollback to SQLite is blocked. Operators must keep maintenance mode enabled, preserve PostgreSQL data, compare differences, and perform reconciliation or forward repair.

This is the correct rollback boundary for avoiding split-brain data loss.

## 9. Issues Resolved In This Review

1. Snapshot source safety:
   - Removed writable fallback from `create_consistent_sqlite_snapshot.py`.
   - Source snapshots now fail rather than silently opening production SQLite in writable mode.

2. WAL snapshot reopen reliability:
   - Temporary backup output is normalized to rollback journal mode after SQLite backup so validation can reopen it read-only without WAL sidecars.

3. Preflight manifest validation:
   - Snapshot manifest checks now verify integrity, hash, size, and table-count structure.

4. Secret log hygiene:
   - `youtube_core.py` no longer logs prefixes of `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, or `GOOGLE_REFRESH_TOKEN`; it logs only `PRESENT` or `MISSING`.

## 10. Unresolved Issues

- Production Cloud SQL PostgreSQL instance has been created, but formal import has not succeeded.
- Production `DATABASE_URL` Secret has been created, but it has not been attached to a traffic-serving Cloud Run revision.
- Final production database password Secret and `DATABASE_URL` Secret exist, but the traffic-serving revision does not use them.
- Production SQLite was frozen only after deploying current source as a maintenance revision; an env-only update to the older image did not enforce write freeze.
- Final candidate SQLite snapshot has been created and validated.
- Production data has been exported, but import/validation against final PostgreSQL is blocked by Legacy schema mismatch.
- Tagged `0%` PostgreSQL Cloud Run revision has not been deployed or checked.
- Production traffic has been restored to the preserved SQLite revision, so maintenance mode is not currently serving normal traffic.
- SQLite on GCSFuse remains unsafe for production writes until cutover completes.

## 11. Formal Cloud SQL Specification Decisions

Still to decide before production cutover:

- Final Cloud SQL instance name.
- Final machine tier. The prior `db-f1-micro` test was sufficient for smoke/rehearsal but slow for full integration tests.
- Backup retention policy.
- Deletion protection setting.
- Database name and least-privilege application user.
- Secret Manager secret name for production `DATABASE_URL`.
- Cloud Run Cloud SQL connection mechanism and tagged revision name.

## 12. Maintenance Window Requirements

A real cutover window must include:

1. Human authorization.
2. Enable `CLIPFORGE_MAINTENANCE_MODE=true`.
3. Pause or stop workers.
4. Verify GET routes remain available and writes return maintenance `503`.
5. Create final SQLite snapshot using the backup API.
6. Export and import into final PostgreSQL.
7. Validate row counts, key hashes, uniqueness, sequences, and app read checks.
8. Deploy a tagged PostgreSQL revision at `0%` traffic.
9. Confirm backend is PostgreSQL on the tag.
10. Switch traffic in one step only after validation.
11. Keep maintenance mode enabled until final human approval to unfreeze.

## 13. Go / Conditional Go / No-Go Conclusion

Conclusion: `CONDITIONAL GO`

Meaning:

- The repository tooling and current Cloud Run read-only state are good enough to proceed to the next preparation task.
- Immediate production database cutover is still blocked.
- The cutover must not proceed until all failed preflight checks are intentionally satisfied during a maintenance window.

Blocking conditions for formal cutover:

- Enable maintenance mode.
- Create and verify the final Cloud SQL PostgreSQL 16 instance in `us-central1`.
- Verify Cloud SQL Client IAM and Secret Manager-backed database credentials.
- Generate a final consistent SQLite snapshot with `integrity_check=ok`.
- Import and validate production data into final PostgreSQL.
- Deploy and validate a tagged `0%` PostgreSQL Cloud Run revision.
- Obtain explicit human approval for the one-step traffic switch.

## 14. Single Next Recommended Task

Create the final Cloud SQL PostgreSQL production candidate instance under explicit human authorization, then run a full no-traffic migration rehearsal against it using the existing snapshot/export/import/validate tooling.
