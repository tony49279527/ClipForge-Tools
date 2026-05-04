import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

BASE_DIR = Path(__file__).resolve().parent
CLIENT_SECRET_PATH = Path(
    os.getenv("YOUTUBE_CLIENT_SECRET_PATH", "/secrets/client_secret.json")
).resolve()
TOKEN_PATH = Path(os.getenv("YOUTUBE_TOKEN_PATH", "/secrets/youtube_token.json")).resolve()
YOUTUBE_ACCOUNTS_DIR = Path(
    os.getenv("YOUTUBE_ACCOUNTS_DIR", str(BASE_DIR / "secrets" / "youtube_accounts"))
).resolve()
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def list_youtube_accounts() -> List[Dict[str, str]]:
    accounts: List[Dict[str, str]] = []
    
    # Check if env vars for a single default account are provided
    if os.getenv("YOUTUBE_CLIENT_SECRET_JSON") and os.getenv("YOUTUBE_TOKEN_JSON"):
        accounts.append({
            "account_id": "default_env",
            "display_name": "系统环境变量账号 (JSON)",
            "channel_label": "Env Configured",
            "client_secret_path": "env",
            "token_path": "env",
        })
        
    # Check if individual GOOGLE env vars are provided
    google_accounts = []
    for key, value in os.environ.items():
        if key.startswith("GOOGLE_CLIENT_ID"):
            suffix = key[len("GOOGLE_CLIENT_ID"):]
            if os.getenv(f"GOOGLE_CLIENT_SECRET{suffix}"):
                display_name = os.getenv(f"GOOGLE_ACCOUNT_NAME{suffix}")
                if not display_name:
                    display_name = f"环境变量账号 {suffix.strip('_')}" if suffix else "默认环境变量账号"
                
                google_accounts.append({
                    "account_id": f"google_env_vars{suffix}",
                    "display_name": display_name,
                    "channel_label": "Cloud Run Configured",
                    "client_secret_path": "env",
                    "token_path": "env",
                })
    
    # Sort to ensure deterministic order (e.g. google_env_vars, google_env_vars_1, etc.)
    google_accounts.sort(key=lambda x: x["account_id"])
    accounts.extend(google_accounts)

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
    if not account_id:
        accounts = list_youtube_accounts()
        if accounts:
            account_id = accounts[0]["account_id"]

    # Support individual GOOGLE_* variables
    if account_id and account_id.startswith("google_env_vars"):
        suffix = account_id[len("google_env_vars"):]
        client_id = os.getenv(f"GOOGLE_CLIENT_ID{suffix}")
        client_secret = os.getenv(f"GOOGLE_CLIENT_SECRET{suffix}")
        refresh_token = os.getenv(f"GOOGLE_REFRESH_TOKEN{suffix}")
        
        if not refresh_token:
            raise ValueError(f"Environment variable GOOGLE_REFRESH_TOKEN{suffix} is missing. Please run generate_youtube_token.py locally to get it.")
        
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES
        )
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        return build("youtube", "v3", credentials=creds)

    # Support full JSON string variables
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
