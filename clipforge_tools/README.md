# ClipForge Tools

ClipForge Tools is a FastAPI web app for generating multi-clip AI product videos, stitching them with FFmpeg, and optionally uploading the final result to YouTube.

## Features

- Create jobs from a simple web form
- Store job state and clip state in SQLite
- Run video generation in `FastAPI BackgroundTasks`
- Generate multiple short clips and stitch them into one final video
- Track status, logs, local paths, token usage, and estimated cost
- Optionally upload the final video using YouTube Data API `videos.insert`
- Save local uploads now and leave object storage integration ready for V2

## Project Structure

```text
clipforge_tools/
  app.py
  video_core.py
  youtube_core.py
  db.py
  templates/
    base.html
    index.html
    job.html
    jobs.html
  static/
    style.css
  data/
    clipforge.db
  uploads/
  outputs/
    {job_id}/
      clips/
        clip_01.mp4
        clip_02.mp4
      clips.txt
      final_video.mp4
  client_secret.json
  youtube_token.json
  requirements.txt
  README.md
```

## 1. Install Dependencies

```bash
cd /path/to/clipforge_tools
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Set `ARK_API_KEY`

```bash
export ARK_API_KEY="your_ark_api_key"
```

For production, put this in your shell profile or `systemd` environment configuration instead of hardcoding it in Python files.

## 3. Install FFmpeg

Ubuntu / Debian:

```bash
sudo apt update
sudo apt install -y ffmpeg
ffmpeg -version
```

macOS with Homebrew:

```bash
brew install ffmpeg
```

## 4. Put `client_secret.json` and `youtube_token.json` in the Project Directory

Place these files in the same directory as `app.py`.

- `client_secret.json`: Google OAuth client credentials
- `youtube_token.json`: Stored OAuth token after authorization

Notes:

- Do not place these files under `static/`
- New or unaudited YouTube API projects may force uploaded videos to `private` even when `unlisted` or `public` is requested

## 5. Start FastAPI

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

Then open:

```text
http://YOUR_SERVER_IP:8000
```

## 6. Deploy with Nginx + systemd

### Example `systemd` service

Create `/etc/systemd/system/clipforge.service`:

```ini
[Unit]
Description=ClipForge Tools FastAPI Service
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/srv/clipforge_tools
Environment="ARK_API_KEY=your_ark_api_key"
ExecStart=/srv/clipforge_tools/.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable clipforge
sudo systemctl start clipforge
sudo systemctl status clipforge
```

### Example Nginx reverse proxy

Create `/etc/nginx/sites-available/clipforge`:

```nginx
server {
    listen 80;
    server_name your-domain.com;

    client_max_body_size 50M;

    location /static/ {
        alias /srv/clipforge_tools/static/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable site:

```bash
sudo ln -s /etc/nginx/sites-available/clipforge /etc/nginx/sites-enabled/clipforge
sudo nginx -t
sudo systemctl reload nginx
```

## API Routes

- `GET /`: new job form
- `POST /jobs`: create job and start background work
- `GET /jobs`: list jobs
- `GET /jobs/{job_id}`: job detail page
- `GET /jobs/{job_id}/status`: JSON status for polling

## Notes About V1

- Seedance task request/response shapes can vary by account version. The current code is structured for easy endpoint and payload adjustment in `video_core.py`.
- Uploaded local images are saved, but they are not automatically transformed into public URLs yet.
- `upload_to_object_storage(file_path)` is reserved for future S3 / R2 / TOS integration.
- `FastAPI BackgroundTasks` works for V1, but queue systems like Celery or RQ are better for long-running production jobs.
- SQLite is suitable for V1 and should later be upgraded to MySQL or PostgreSQL if concurrency grows.

## Security Notes

- `ARK_API_KEY` is read from environment variables only
- OAuth files are not exposed through `static/`
- Upload types are limited to `.jpg`, `.jpeg`, `.png`, `.webp`
- Each job gets its own output directory
- Background job errors are captured and stored in `error_message`
