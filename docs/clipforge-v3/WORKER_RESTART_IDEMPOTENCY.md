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
