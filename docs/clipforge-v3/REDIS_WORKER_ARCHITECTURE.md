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
- Redis instance: `clipforge-redis-prod`
- Redis product: Google Cloud Memorystore for Redis
- Region: `us-central1`
- Tier: `BASIC`
- Capacity: `1GB`
- Network: private `default` VPC through Private Service Access
- Public access: none
- AUTH: not enabled
- Worker service: `clipforge-tools-worker`
- Worker revision validated: `clipforge-tools-worker-00004-rck`
- Worker instances: min `1`, max `1`
- Worker concurrency: `1`
- Worker CPU: no throttling while instance is allocated
- Queue: `clipforge`
- Web candidate revision: `clipforge-tools-pg-redis2`
- Web candidate traffic: `0%`, tag `pg-candidate`
- Production traffic: still `100%` on SQLite revision `clipforge-tools-00104-hwm`
- Current blocker before traffic cutover: queue smoke and restart/idempotency validation are not yet complete.
- Do not enable paid generation or switch traffic until Redis, worker heartbeat, and restart idempotency are validated.

Estimated added monthly cost is expected to remain below USD 100 for the initial candidate: small Basic Memorystore Redis plus one always-on single-instance worker. This estimate excludes the already-created Cloud SQL candidate.
