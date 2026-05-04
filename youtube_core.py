import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import Flow
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent
CLIENT_SECRET_PATH = Path(
    os.getenv("YOUTUBE_CLIENT_SECRET_PATH", "/secrets/client_secret.json")
).resolve()
TOKEN_PATH = Path(os.getenv("YOUTUBE_TOKEN_PATH", "/secrets/youtube_token.json")).resolve()
YOUTUBE_ACCOUNTS_DIR = Path(
    os.getenv("YOUTUBE_ACCOUNTS_DIR", str(BASE_DIR / "data" / "youtube_accounts"))
).resolve()
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def list_youtube_accounts() -> List[Dict[str, str]]:
    accounts: List[Dict[str, str]] = []
    
    # Check if env vars for a single default account are provided
    if os.getenv("YOUTUBE_CLIENT_SECRET_JSON") and os.getenv("YOUTUBE_TOKEN_JSON"):
        accounts.append({
            "account_id": "default_env",
            "display_name": "系统环境变量账号",
            "channel_label": "Env Configured",
            "client_secret_path": "env",
            "token_path": "env",
        })

    if not YOUTUBE_ACCOUNTS_DIR.exists():
        return accounts

    for account_dir in sorted(path for path in YOUTUBE_ACCOUNTS_DIR.iterdir() if path.is_dir()):
        meta_path = account_dir / "meta.json"
        meta: Dict[str, str] = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                meta = {}
        account_id = meta.get("account_id") or account_dir.name
        display_name = meta.get("display_name") or account_dir.name
        channel_label = meta.get("channel_label") or ""
        accounts.append(
            {
                "account_id": account_id,
                "display_name": display_name,
                "channel_label": channel_label,
                "client_secret_path": str(account_dir / "client_secret.json"),
                "token_path": str(account_dir / "youtube_token.json"),
            }
        )
    return accounts


def create_oauth_flow(redirect_uri: str, state: Optional[str] = None) -> Flow:
    client_secret_json = os.getenv("YOUTUBE_CLIENT_SECRET_JSON")
    if not client_secret_json:
        raise ValueError("Environment variable YOUTUBE_CLIENT_SECRET_JSON is not set.")
    
    client_config = json.loads(client_secret_json)
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        state=state
    )
    flow.redirect_uri = redirect_uri
    return flow


def save_authorized_account(creds: Credentials) -> str:
    YOUTUBE_ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    # Use timestamp as account ID
    account_id = f"account_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    account_dir = YOUTUBE_ACCOUNTS_DIR / account_id
    account_dir.mkdir(parents=True, exist_ok=True)

    # Save token
    token_path = account_dir / "youtube_token.json"
    token_path.write_text(creds.to_json(), encoding="utf-8")

    # Save meta
    meta_path = account_dir / "meta.json"
    meta_data = {
        "account_id": account_id,
        "display_name": f"YouTube Account {account_id[-6:]}",
        "channel_label": "Authorized via Web"
    }
    meta_path.write_text(json.dumps(meta_data, ensure_ascii=False, indent=2), encoding="utf-8")
    
    return account_id


def get_youtube_account_config(account_id: Optional[str]) -> Dict[str, str]:
    if account_id:
        for account in list_youtube_accounts():
            if account["account_id"] == account_id:
                return account
        raise FileNotFoundError(
            f"YouTube account '{account_id}' was not found under {YOUTUBE_ACCOUNTS_DIR}."
        )

    return {
        "account_id": "default",
        "display_name": "Default account",
        "channel_label": "",
        "client_secret_path": str(CLIENT_SECRET_PATH),
        "token_path": str(TOKEN_PATH),
    }


def get_youtube_service(account_id: Optional[str] = None):
    if account_id == "default_env" or (not account_id and os.getenv("YOUTUBE_TOKEN_JSON")):
        token_json = os.getenv("YOUTUBE_TOKEN_JSON")
        if not token_json:
            raise ValueError("Environment variable YOUTUBE_TOKEN_JSON is missing.")
        token_data = json.loads(token_json)
        # Handle google.oauth2.credentials.Credentials directly from dict
        creds = Credentials.from_authorized_user_info(token_data, SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # For env vars, we refresh in memory but cannot persist back to the env var.
            # This is fine as long as the refresh token is valid.
        return build("youtube", "v3", credentials=creds)

    account = get_youtube_account_config(account_id)
    client_secret_path = Path(account["client_secret_path"]).resolve()
    token_path = Path(account["token_path"]).resolve()
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
    if not creds or not creds.valid:
        if not client_secret_path.exists():
            raise FileNotFoundError(
                f"YouTube client secret file not found: {client_secret_path}. "
                "Set YOUTUBE_CLIENT_SECRET_PATH to a mounted secret path."
            )
        raise RuntimeError(
            f"YouTube token file not found or invalid: {token_path}. "
            "Generate youtube_token.json locally first, then mount it into Cloud Run."
        )
    return build("youtube", "v3", credentials=creds)


def upload_youtube(
    video_path: Path,
    title: str,
    description: str,
    tags: List[str],
    privacy: str,
    account_id: Optional[str] = None,
) -> str:
    service = get_youtube_service(account_id=account_id)
    request = service.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": "22",
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        },
        media_body=MediaFileUpload(str(video_path), chunksize=-1, resumable=True),
    )
    response = None
    while response is None:
        _, response = request.next_chunk()
    video_id = response["id"]
    return f"https://www.youtube.com/watch?v={video_id}"
