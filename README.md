# ClipForge Tools

ClipForge Tools is a FastAPI web app for generating AI product videos for hardware tools, automotive tools, RV tools, and Amazon sellers. Users submit product details in the browser, the backend generates short Seedance clips, downloads them, stitches them with FFmpeg, and can optionally upload the final video to YouTube.

## What It Does

- Create video jobs from a web form
- Generate multiple short Seedance clips instead of one long video
- Save clips under `outputs/{job_id}/clips/`
- Stitch clips into `outputs/{job_id}/final_video.mp4`
- Show task status, clip paths, token usage, estimated cost, and YouTube URL
- Save uploaded reference images locally for now and leave object storage integration ready for V2

## Runtime Configuration

Environment variables:

- `ARK_API_KEY`: required
- `YOUTUBE_CLIENT_SECRET_PATH`: default `/secrets/client_secret.json`
- `YOUTUBE_TOKEN_PATH`: default `/secrets/youtube_token.json`
- `YOUTUBE_ACCOUNTS_DIR`: default `./secrets/youtube_accounts`
- `DATA_DIR`: default `./data`
- `DATABASE_URL`: optional database URL; defaults to local SQLite and supports `postgresql+psycopg://...` for PostgreSQL
- `POSTGRES_TEST_DATABASE_URL`: optional disposable PostgreSQL URL for integration and migration rehearsal tests; never point this at production
- `DB_URL`: backward-compatible SQLite-only URL, for example `sqlite:///./data/clipforge.db`
- `OUTPUTS_DIR`: default `./outputs`
- `UPLOADS_DIR`: default `./uploads`
- `PRICE_PER_MILLION_TOKENS_CNY`: default `46`
- `REDIS_URL`: default `redis://localhost:6379/0`
- `RQ_QUEUE_NAME`: default `clipforge`
- `RQ_WORKER_COUNT`: default `3`
- `MAX_CLIP_WORKERS`: default `4`
- `JOB_RETRIES`: default `2`
- `JOB_RETRY_DELAY`: default `5`
- `CLIPFORGE_V3_ENABLED`: default `false`
- `V3_VIDEO_PROVIDER`: default `mock`
- `V3_REAL_API_ENABLED`: default `false`
- `V3_STORAGE_BACKEND`: default `local`; set to `r2` only when Cloudflare R2 configuration is complete
- `R2_PUBLIC_BUCKET_NAME`: public R2 bucket for V3 product reference images
- `R2_PRIVATE_BUCKET_NAME`: private R2 bucket for V3 generated videos
- `R2_PUBLIC_BASE_URL`: HTTPS public base URL for product reference images
- `R2_BUCKET_NAME`: backward-compatible single-bucket R2 configuration only
- `SEEDANCE_PROVIDER`: default `ark`
- `SEEDANCE_MODEL`: Seedance model ID for V3 provider adapter
- `SEEDANCE_BASE_URL`: Seedance provider base URL
- `SEEDANCE_DEFAULT_RESOLUTION`: default `720p`
- `SEEDANCE_GENERATE_AUDIO`: default `true`
- `SEEDANCE_WATERMARK`: default `false`
- `SEEDANCE_PROMPT_MAX_CHARS`: default `2000`
- `STORAGE_BACKEND`: default `local`
- `V3_MAX_UPLOAD_BYTES`: default `26214400`

Notes:

- If YouTube secret files are missing, the web app still starts normally.
- A job only fails at the YouTube upload step if upload is requested and YouTube credentials are unavailable.
- `youtube_token.json` should be generated locally first, then mounted into Cloud Run using Secret Manager or volumes.
- For multi-account switching, the app can load per-account credentials from `YOUTUBE_ACCOUNTS_DIR`.
- The UI defaults to Chinese. Users can switch to English from the top navigation.

## Multi-Account YouTube Upload

This version supports a practical operator workflow: choose a different YouTube upload account for each job.

Recommended directory structure:

```text
secrets/
  youtube_accounts/
    my_channel/
      client_secret.json
      youtube_token.json
      meta.json
    friend_alex/
      client_secret.json
      youtube_token.json
      meta.json
    client_rv_tools/
      client_secret.json
      youtube_token.json
      meta.json
```

Example `meta.json`:

```json
{
  "account_id": "client_rv_tools",
  "display_name": "客户-RV Tools",
  "channel_label": "RV Tools Official"
}
```

How it works:

- the New Job page shows a YouTube account selector
- each job stores `youtube_account_id`
- upload uses that account's `client_secret.json` and `youtube_token.json`
- if upload is enabled but no account is selected, job creation is rejected

## Project Structure

```text
.
├── app.py
├── db.py
├── clipforge_v3/
├── video_core.py
├── youtube_core.py
├── templates/
├── static/
├── data/
├── uploads/
├── outputs/
├── requirements.txt
├── Dockerfile
└── README.md
```

## Local Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export ARK_API_KEY="..."
redis-server
python worker.py --workers 3
uvicorn app:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

The web app now enqueues long-running jobs into Redis/RQ, so a Redis server and at least one worker process must be running for video/image/publish tasks to progress.

## ClipForge 3.0

ClipForge 3.0 is mounted under `/v3` and is controlled by `CLIPFORGE_V3_ENABLED=true`.

Version overview:

- ClipForge 1.0: original direct job flow at `/`, `/jobs`, and related status pages.
- ClipForge 2.0: storyboard-oriented workflow at `/v2` and `/v2/jobs`.
- ClipForge 3.0: professional product director and production console at `/v3`.

V3 endpoints:

- `GET /v3`
- `GET /v3/projects`
- `GET /v3/projects/new`
- `POST /v3/projects`
- `GET /v3/projects/{project_id}`
- `GET /v3/projects/{project_id}/status`
- `GET /v3/health`
- `GET /v3/ready`

V3 keeps ClipForge 1.0 and 2.0 intact while adding separate tables and modules for Product Truth, Assets, Shot Contracts, Prompt Versions, Takes, Reviews, Continuity States, Usage Events, Operation Events, Preflight checks, Retake Plans, and Final Assembly.

Current V3 workflow steps:

1. Brief
2. Product Truth
3. Assets
4. Director Plan
5. Shot Contracts
6. Prompt Compile
7. Draft Generation
8. Production Generation
9. Review
10. Final Assembly
11. Publish

Current V3 status:

- Mock Alpha: available. It exercises V3 planning, prompt compilation, mock generation, review, continuity, and final assembly without paid APIs.
- Real API Test: available only for a manually confirmed single Seedance shot with idempotency safeguards.
- Production: not complete. Durable object storage, long-running paid task validation, external user authentication, broader real-product validation, and production security review are still required.

Current V3 capabilities:

- Versioned Product Truth extraction and manual confirmation gate
- Real product reference asset upload, role assignment, audit metadata, replacement history, and controlled local storage
- Reference Role Map generation
- Seedance mode routing (`T2V`, `I2V`, `V2V`, `R2V`, `FLF2V`, `edit`, `extend`)
- Fidelity allocation with overload split warnings
- Structured Shot Contract planning
- Deterministic Prompt Compiler with anti-slop pass, linter, compression, payload preview, and 2000-character enforcement
- Seedance Provider Adapter with capability validation and sanitized payload logging
- Preflight checks with fail-open/fail-closed reference handling
- Continuity Ledger, dependency scheduling, Take versioning, Review Studio, Retake Protocol, budget checks, and Final Assembly from selected Takes
- Chinese operator console with English switch, cost center, blocking errors, and readiness checks

V3 docs:

Current V3 development status: `docs/clipforge-v3/CURRENT_STATUS.md`
Real Provider readiness audit: `docs/clipforge-v3/REAL_PROVIDER_READINESS_AUDIT.md`
Object storage design: `docs/clipforge-v3/OBJECT_STORAGE.md`
Cloud Run database risk audit: `docs/clipforge-v3/CLOUD_RUN_DATABASE_RISK.md`
PostgreSQL migration plan: `docs/clipforge-v3/POSTGRESQL_MIGRATION.md`
Temporary database safeguards: `docs/clipforge-v3/CLOUD_RUN_TEMPORARY_DATABASE_SAFEGUARDS.md`
Production database cutover runbook: `docs/clipforge-v3/PRODUCTION_DATABASE_CUTOVER_RUNBOOK.md`
Redis and worker architecture: `docs/clipforge-v3/REDIS_WORKER_ARCHITECTURE.md`
Worker restart idempotency: `docs/clipforge-v3/WORKER_RESTART_IDEMPOTENCY.md`
Production cutover Go/No-Go review: `docs/clipforge-v3/PRODUCTION_CUTOVER_GO_NO_GO.md`
Production PostgreSQL candidate attempt: `docs/clipforge-v3/PRODUCTION_POSTGRES_CANDIDATE.md`

- `docs/clipforge-v3/README.md`
- `docs/clipforge-v3/user-guide-zh.md`
- `docs/clipforge-v3/operator-guide.md`
- `docs/clipforge-v3/deployment.md`
- `docs/clipforge-v3/provider-configuration.md`
- `docs/clipforge-v3/director-system.md`
- `docs/clipforge-v3/error-codes.md`
- `docs/clipforge-v3/evals.md`
- `docs/clipforge-v3/security.md`

Run V3 migrations and tests:

```bash
export CLIPFORGE_V3_ENABLED=true
python3 scripts/v3/migrate_v3.py
python3 scripts/test_v3_workflow_smoke.py
python3 evaluation/run_evals.py
python3 -m pytest -q
```

V3 real Seedance safeguards:

- Default mode is `V3_VIDEO_PROVIDER=mock` and `V3_REAL_API_ENABLED=false`.
- `ARK_API_KEY` alone does not trigger a paid call.
- Real Ark submission requires provider mode, real API gate, backend paid confirmation token, and an idempotency reservation.
- HTTP timeout is recorded as `unknown_submission_state`; operators must inspect provider state before retrying.

Manual single-shot real API test:

```bash
export CLIPFORGE_V3_ENABLED=true
export V3_VIDEO_PROVIDER=ark
export V3_REAL_API_ENABLED=true
export V3_REAL_API_TEST_CONFIRM=I_UNDERSTAND_THIS_COSTS_MONEY
export ARK_API_KEY="..."
python3 scripts/v3/test_real_seedance_single_shot.py
```

V3 still requires external configuration for real paid video generation and publishing. CI uses mocks and local files only.

## Docker Local Test

```bash
docker build -t clipforge-tools .
docker run -p 8080:8080 -e ARK_API_KEY="..." clipforge-tools
```

Then open [http://127.0.0.1:8080](http://127.0.0.1:8080).

## Docker Compose Queue Stack

For the full queue-backed workflow locally:

```bash
docker compose up --build
```

This starts:

- `web`: FastAPI application
- `redis`: queue backend
- `worker`: RQ worker processes for background jobs

## Cloud Run Deployment Notes

This repo is prepared for Cloud Run:

- `app.py` is the entrypoint
- the service listens on `0.0.0.0` and reads `PORT`
- `Dockerfile` installs `ffmpeg`
- local runtime files go to `data/`, `uploads/`, and `outputs/`, or to paths overridden by environment variables

Important Cloud Run limits and recommendations:

1. Cloud Run request timeout defaults to 300 seconds. For this app, set the Cloud Run timeout to `3600` seconds.
2. Cloud Run local filesystem is temporary. `outputs/`, `uploads/`, and `data/` are acceptable for V1 testing only. Production should move to object storage and Cloud SQL/PostgreSQL. Do not run production writes against SQLite on a GCSFuse mount.
3. Set concurrency to `1` for the web service because Seedance generation and FFmpeg stitching are heavy tasks.
4. Recommended resources: memory `2GiB`, CPU `2`.
5. FFmpeg is already installed in the Dockerfile.
6. Manage secrets with environment variables and Secret Manager. Do not commit secrets to GitHub.
7. YouTube uploads from unaudited API projects may be forced to `Private`.
8. This app now uses Redis/RQ for background execution. A single Cloud Run web service is not enough by itself; you also need a Redis service plus separate worker execution infrastructure.

## Cloud Run Pre-Deploy Checklist

1. `Dockerfile` exists
2. `requirements.txt` exists
3. `ARK_API_KEY` is configured
4. Secret Manager is configured for YouTube JSON files
5. Cloud Run timeout is `3600`
6. Memory is `2GiB`
7. Concurrency is `1`
8. Redis is available to the service
9. At least one RQ worker deployment/process is running

## GitHub -> Cloud Run

In the Cloud Run console:

1. Click `Connect repository`
2. Choose GitHub repository `tony49279527/ClipForge-Tools`
3. Select branch `main`
4. Use `Dockerfile` as the build method
5. Set service name to `clipforge-tools`
6. Choose region `us-central1`
7. Allow unauthenticated access for testing if needed

## YouTube Credentials

Cloud Run should not rely on repo files like `client_secret.json` or `youtube_token.json`.

Recommended setup:

- mount `client_secret.json` to `YOUTUBE_CLIENT_SECRET_PATH`
- mount `youtube_token.json` to `YOUTUBE_TOKEN_PATH`

If `YOUTUBE_CLIENT_SECRET_PATH` or `YOUTUBE_TOKEN_PATH` does not exist, the app still runs, but any requested YouTube upload step will fail and write the error into `job.error_message`.

Also note:

- YouTube Data API upload uses `videos.insert`
- after July 28, 2020, new and unaudited API projects may force uploads to `Private` even if `unlisted` or `public` is requested

## API Routes

- `GET /`
- `POST /jobs`
- `GET /jobs`
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/status`
- `GET /healthz`

## Security Notes

Do not commit:

- `ARK_API_KEY`
- `SEEDANCE_API_KEY`
- `client_secret.json`
- `youtube_token.json`
- generated video files
- SQLite database files
- `data/`
- `uploads/`
- `outputs/`
- provider payloads or responses containing private URLs or credentials

## Development Notes

- Seedance reference images must be public URLs
- uploaded local images are stored now but are not sent to Seedance directly
- `upload_to_object_storage(file_path)` is intentionally reserved for future Cloud Storage / S3 / R2 / TOS support
- background execution now uses Redis + RQ instead of in-process background threads
