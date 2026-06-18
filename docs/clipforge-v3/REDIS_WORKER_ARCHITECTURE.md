# ClipForge V3 Redis And Worker Architecture

## Target Shape

```text
Cloud Run web candidate
  -> Cloud SQL PostgreSQL
  -> Cloudflare R2
  -> Memorystore Redis
       -> dedicated Cloud Run RQ worker
```

## Configuration Rules

- `REDIS_URL` is the preferred queue configuration.
- `RQ_REDIS_URL` is accepted only as a compatibility fallback.
- `RQ_QUEUE_NAME` defaults to `clipforge` and must match between web and worker.
- `REDIS_REQUIRED=true` makes missing Redis configuration a readiness failure.
- Cloud Run is treated as Redis-required unless `ALLOW_LOCAL_REDIS=true` is explicitly set for a controlled local-style test.
- Production must not silently fall back to `localhost:6379`.
- Redis credentials must be delivered through Secret Manager, not Git or plaintext docs.
- `/v3/ready` reports queue name, Redis reachability, registered workers, healthy workers, and heartbeat age without printing the Redis URL.

## Worker Runtime

The production worker entrypoint is:

```bash
python scripts/v3/run_rq_worker_service.py
```

It starts:

- a lightweight HTTP health server on `$PORT`
- a child RQ worker process using `worker.py`

Health is `200` only while the RQ child process is alive. If the child exits, the health endpoint returns `503`. SIGTERM is forwarded to the worker so Cloud Run can stop the service without accepting new work indefinitely.

## Current Production Candidate Status

- PostgreSQL candidate web tag: `pg-candidate`
- Current blocker before traffic cutover: production Redis/RQ worker is not provisioned.
- Do not enable paid generation or switch traffic until Redis, worker heartbeat, and restart idempotency are validated.
