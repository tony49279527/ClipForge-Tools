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
- `OUTPUTS_DIR`: default `./outputs`
- `UPLOADS_DIR`: default `./uploads`
- `PRICE_PER_MILLION_TOKENS_CNY`: default `46`
- `REDIS_URL`: default `redis://localhost:6379/0`
- `RQ_QUEUE_NAME`: default `clipforge`
- `RQ_WORKER_COUNT`: default `3`
- `MAX_CLIP_WORKERS`: default `4`
- `JOB_RETRIES`: default `2`
- `JOB_RETRY_DELAY`: default `5`

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

## 2.0 Smoke Test

To verify the 2.0 gated storyboard workflow locally without calling external APIs:

```bash
python3 scripts/test_v2_workflow_smoke.py
```

This smoke test covers:

- 2.0 job creation
- prompt generation and prompt approval gate
- image generation gate
- image approval gate
- video approval gate
- publish confirmation gate
- single-clip regeneration route

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
2. Cloud Run local filesystem is temporary. `outputs/`, `uploads/`, and `data/` are acceptable for V1 testing only. Production should move to Cloud Storage and Cloud SQL.
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
- `client_secret.json`
- `youtube_token.json`
- generated video files
- SQLite database files

## Development Notes

- Seedance reference images must be public URLs
- uploaded local images are stored now but are not sent to Seedance directly
- `upload_to_object_storage(file_path)` is intentionally reserved for future Cloud Storage / S3 / R2 / TOS support
- background execution now uses Redis + RQ instead of in-process background threads
