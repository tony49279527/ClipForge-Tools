# ClipForge V3 Worker Restart And Idempotency

## Durable State

V3 real-provider generation is protected by `v3_generation_submissions.idempotency_key`.

The same submission must be reused across:

- duplicate user clicks
- RQ retries
- worker restarts
- provider polling recovery
- artifact upload recovery

## Existing Safety Boundaries

- A saved provider task ID means the worker must poll, not submit again.
- `unknown_submission_state` blocks automatic re-submit.
- Take creation is idempotent per `generation_submission_id`.
- Usage/cost event creation is idempotent per `event_key`.
- Maintenance mode blocks enqueue and worker execution before provider submission.

## Required Production Validation

Before PostgreSQL traffic cutover, validate with mock provider only:

1. job queued before worker starts
2. worker killed after reservation
3. worker killed before mock provider submit
4. worker killed after mock task ID is saved
5. worker killed after mock download
6. worker killed after Take creation
7. worker killed after usage/cost creation

Each case must prove:

- one generation submission
- at most one mock provider submit
- one Take
- one usage/cost event
- no real Ark/Seedance call
- no infinite retry loop

## Candidate Validation Update

Date: 2026-06-18

- Worker service `clipforge-tools-worker` is running revision `clipforge-tools-worker-00007-zvn`.
- Web candidate `clipforge-tools-pg-worker-ready` reports Redis and worker readiness as `ok`.
- Non-paid queue smoke passed via Cloud Run Job `clipforge-queue-smoke`.
- The retry smoke intentionally raised once, then completed through RQ scheduler retry.
- `tests/v3/test_real_provider_alpha.py` passed locally and covers duplicate paid submission protection, saved task polling, crash replay, Take idempotency, usage/cost idempotency, and `unknown_submission_state`.
- No real Ark/Seedance call was made.

Remaining before final cutover:

- Run the final maintenance-window snapshot/import because production SQLite may have changed after the previous candidate import.
