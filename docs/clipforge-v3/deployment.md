# Deployment

Local:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export CLIPFORGE_V3_ENABLED=true
redis-server
python3 worker.py --workers 3
python3 -m uvicorn app:app --reload
```

Docker:

```bash
docker compose up --build
```

Cloud Run notes:

- Web, Redis, and worker are separate runtime responsibilities.
- Local SQLite and local storage are suitable for development, Mock Alpha, and tightly controlled single-shot real API tests only.
- Local Mock/Alpha Storage is not durable for Cloud Run production.
- Production should use managed database and object storage. Cloud object storage is intentionally not implemented in this phase.
- Set timeout to 3600 seconds for long video operations.
- Keep concurrency low for worker-heavy workloads.
- Use Secret Manager for API keys and YouTube credentials.
- Do not depend on local filesystem persistence for generated videos in production.

Database:

- `DB_URL=sqlite:///path/to/clipforge.db` is supported.
- SQLite WAL and busy timeout are enabled.
- V3 migrations are additive and idempotent.

Real Seedance testing:

- Do not enable real Ark mode in shared deployments until Redis/RQ workers and storage persistence have been validated.
- Real mode requires `V3_VIDEO_PROVIDER=ark`, `V3_REAL_API_ENABLED=true`, a confirmed paid-generation token, and `ARK_API_KEY`.
- A request timeout is recorded as `unknown_submission_state`; operators must inspect provider state before retrying to avoid duplicate charges.

Production blockers:

- Durable object storage is still required.
- Long-running paid task behavior needs soak testing.
- External user authentication and authorization are not complete.
- Wider real-product validation and a production security audit are still required.
