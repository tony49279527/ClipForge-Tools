import json
import os
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

import env_bootstrap  # noqa: F401

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
# Fallback paths for local development if env vars are missing
CLIENT_SECRET_PATH = Path(
    os.getenv("YOUTUBE_CLIENT_SECRET_PATH", "/secrets/client_secret.json")
).resolve()
TOKEN_PATH = Path(os.getenv("YOUTUBE_TOKEN_PATH", "/secrets/youtube_token.json")).resolve()
YOUTUBE_ACCOUNTS_DIR = Path(
    os.getenv("YOUTUBE_ACCOUNTS_DIR", str(BASE_DIR / "secrets" / "youtube_accounts"))
).resolve()

DEFAULT_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

def get_credentials_from_env(account_id: Optional[str] = None) -> Credentials:
    """
    Constructs google.oauth2.credentials.Credentials from GOOGLE_* environment variables.
    """
    suffix = ""
    if account_id and account_id.startswith("google_env_vars"):
        suffix = account_id[len("google_env_vars"):]

    client_id = os.getenv(f"GOOGLE_CLIENT_ID{suffix}")
    client_secret = os.getenv(f"GOOGLE_CLIENT_SECRET{suffix}")
    refresh_token = os.getenv(f"GOOGLE_REFRESH_TOKEN{suffix}")
    scopes_raw = os.getenv(f"GOOGLE_YOUTUBE_SCOPES{suffix}")
    token_uri = os.getenv(f"GOOGLE_TOKEN_URI{suffix}", "https://oauth2.googleapis.com/token")

    # Log only presence, never token/secret fragments.
    logger.info(f"Checking environment variables for account suffix '{suffix}':")
    logger.info(f"  GOOGLE_CLIENT_ID{suffix}: {'PRESENT' if client_id else 'MISSING'}")
    logger.info(f"  GOOGLE_CLIENT_SECRET{suffix}: {'PRESENT' if client_secret else 'MISSING'}")
    logger.info(f"  GOOGLE_REFRESH_TOKEN{suffix}: {'PRESENT' if refresh_token else 'MISSING'}")

    if not all([client_id, client_secret, refresh_token]):
        missing = [k for k, v in {
            f"GOOGLE_CLIENT_ID{suffix}": client_id,
            f"GOOGLE_CLIENT_SECRET{suffix}": client_secret,
            f"GOOGLE_REFRESH_TOKEN{suffix}": refresh_token
        }.items() if not v]
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    scopes = scopes_raw.split(",") if scopes_raw else DEFAULT_SCOPES

    creds = Credentials(
        token=None,  # Initial token is None, will be refreshed
        refresh_token=refresh_token,
        token_uri=token_uri,
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes
    )

    # Force a refresh to validate credentials immediately and get an access token
    if not creds.valid:
        logger.info("Refreshing access token...")
        try:
            creds.refresh(Request())
            logger.info("Access token refreshed successfully.")
        except Exception as e:
            logger.error(f"Failed to refresh access token: {str(e)}")
            raise

    return creds

def get_youtube_service(account_id: Optional[str] = None):
    """
    Builds the YouTube API service. 
    Prioritizes environment variables.
    """
    try:
        # First try to get credentials from individual GOOGLE_* env vars
        creds = get_credentials_from_env(account_id)
        return build("youtube", "v3", credentials=creds)
    except ValueError as e:
        logger.warning(f"Could not initialize from GOOGLE_* env vars: {e}. Falling back to legacy methods.")
        
    # Legacy / File-based logic for backward compatibility in local dev
    if not account_id:
        accounts = list_youtube_accounts()
        if accounts:
            account_id = accounts[0]["account_id"]

    # Support full JSON string variables (Legacy)
    if account_id == "default_env" or (not account_id and os.getenv("YOUTUBE_TOKEN_JSON")):
        token_json = os.getenv("YOUTUBE_TOKEN_JSON")
        if token_json:
            token_data = json.loads(token_json)
            creds = Credentials.from_authorized_user_info(token_data, DEFAULT_SCOPES)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            return build("youtube", "v3", credentials=creds)

    # File based (Legacy)
    account = get_youtube_account_config(account_id)
    token_path = Path(account["token_path"]).resolve()
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), DEFAULT_SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        return build("youtube", "v3", credentials=creds)
    
    raise RuntimeError("No valid YouTube credentials found in environment or files.")

def list_youtube_accounts() -> List[Dict[str, str]]:
    """
    Lists available YouTube accounts.
    """
    accounts: List[Dict[str, str]] = []
    
    # 1. Check for individual GOOGLE env vars
    google_accounts = []
    for key in os.environ:
        if key.startswith("GOOGLE_CLIENT_ID"):
            suffix = key[len("GOOGLE_CLIENT_ID"):]
            if os.getenv(f"GOOGLE_CLIENT_SECRET{suffix}") and os.getenv(f"GOOGLE_REFRESH_TOKEN{suffix}"):
                display_name = os.getenv(f"GOOGLE_ACCOUNT_NAME{suffix}")
                if not display_name:
                    display_name = f"Env Account {suffix.strip('_')}" if suffix else "Default Env Account"
                
                google_accounts.append({
                    "account_id": f"google_env_vars{suffix}",
                    "display_name": display_name,
                    "channel_label": "Cloud Run",
                    "client_secret_path": "env",
                    "token_path": "env",
                })
    
    google_accounts.sort(key=lambda x: x["account_id"])
    accounts.extend(google_accounts)

    # 2. Legacy JSON env var
    if os.getenv("YOUTUBE_CLIENT_SECRET_JSON") and os.getenv("YOUTUBE_TOKEN_JSON"):
        accounts.append({
            "account_id": "default_env",
            "display_name": "Legacy Env JSON",
            "channel_label": "Env",
            "client_secret_path": "env",
            "token_path": "env",
        })

    # 3. File-based accounts
    if YOUTUBE_ACCOUNTS_DIR.exists():
        for account_dir in sorted(path for path in YOUTUBE_ACCOUNTS_DIR.iterdir() if path.is_dir()):
            meta_path = account_dir / "meta.json"
            meta = {}
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except:
                    pass
            accounts.append({
                "account_id": meta.get("account_id") or account_dir.name,
                "display_name": meta.get("display_name") or account_dir.name,
                "channel_label": meta.get("channel_label") or "",
                "client_secret_path": str(account_dir / "client_secret.json"),
                "token_path": str(account_dir / "youtube_token.json"),
            })
    
    return accounts

def get_youtube_account_config(account_id: Optional[str]) -> Dict[str, str]:
    if account_id:
        for account in list_youtube_accounts():
            if account["account_id"] == account_id:
                return account
    return {
        "account_id": "default",
        "display_name": "Default account",
        "channel_label": "",
        "client_secret_path": str(CLIENT_SECRET_PATH),
        "token_path": str(TOKEN_PATH),
    }

def test_youtube_credentials(account_id: Optional[str] = None):
    """
    Smoke test: verifies the OAuth credentials can refresh successfully.
    The default upload scope does not guarantee read access to mine=True channel APIs.
    """
    logger.info(f"Starting smoke test for account: {account_id or 'DEFAULT'}")
    try:
        service = get_youtube_service(account_id)
        if service:
            logger.info("Smoke test SUCCESSFUL: credentials refreshed and YouTube service built.")
            return True
        logger.warning("Smoke test failed: could not build YouTube service.")
        return False
    except HttpError as e:
        logger.error(f"Smoke test failed with HTTP error {e.resp.status}: {e.content}")
        return False
    except Exception as e:
        logger.error(f"Smoke test failed with error: {str(e)}")
        return False

def upload_youtube(
    video_path: Path,
    title: str,
    description: str,
    tags: List[str],
    privacy: str,
    account_id: Optional[str] = None,
) -> str:
    """
    Uploads a video to YouTube.
    """
    logger.info(f"Starting upload for video: {video_path}")
    logger.info(f"  Title: {title}")
    logger.info(f"  Privacy: {privacy}")
    logger.info(f"  Account ID: {account_id or 'DEFAULT'}")

    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    try:
        service = get_youtube_service(account_id)
        
        # YouTube API Limits: Title (100 chars), Description (5000 chars), Tags (500 chars total)
        safe_title = title[:97] + "..." if len(title) > 100 else title
        safe_description = description[:4997] + "..." if len(description) > 5000 else description
        
        safe_tags = []
        tags_len = 0
        for t in tags:
            # tag length + 1 for comma separator
            if tags_len + len(t) + 1 <= 450: 
                safe_tags.append(t)
                tags_len += len(t) + 1
            else:
                break

        body = {
            "snippet": {
                "title": safe_title,
                "description": safe_description,
                "tags": safe_tags,
                "categoryId": "22",  # People & Blogs
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(
            str(video_path), 
            mimetype="video/mp4",
            chunksize=-1, # Automatic chunking
            resumable=True
        )

        request = service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )

        logger.info("Beginning resumable upload...")
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(f"  Upload progress: {int(status.progress() * 100)}%")

        video_id = response["id"]
        youtube_url = f"https://www.youtube.com/watch?v={video_id}"
        logger.info(f"Upload SUCCESSFUL! Video URL: {youtube_url}")
        return youtube_url

    except HttpError as e:
        logger.error(f"YouTube API error {e.resp.status}: {e.content}")
        # Log specific helpful hints for common errors
        if e.resp.status == 401:
            logger.error("HINT: 401 Unauthorized usually means the GOOGLE_REFRESH_TOKEN is invalid or revoked.")
        elif e.resp.status == 403:
            logger.error("HINT: 403 Forbidden might mean the YouTube API is not enabled or you've hit a quota limit.")
        raise
    except Exception as e:
        logger.error(f"An unexpected error occurred during upload: {str(e)}")
        raise

# ==========================================
# USAGE INSTRUCTIONS (for Cloud Run):
# ==========================================
# 1. Set environment variables in Cloud Run:
#    GOOGLE_CLIENT_ID = ...
#    GOOGLE_CLIENT_SECRET = ...
#    GOOGLE_REFRESH_TOKEN = ...
#    GOOGLE_YOUTUBE_SCOPES = https://www.googleapis.com/auth/youtube.upload
#
# 2. To run the smoke test:
#    from youtube_core import test_youtube_credentials
#    test_youtube_credentials()
#
# 3. To upload a video:
#    from youtube_core import upload_youtube
#    from pathlib import Path
#    url = upload_youtube(
#        video_path=Path("/data/outputs/test.mp4"),
#        title="My Cool Video",
#        description="Check this out!",
#        tags=["test", "cool"],
#        privacy="private"
#    )
# ==========================================
