from __future__ import annotations

import json
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipforge_v3.services.storage_service import R2Storage, StorageError


def _fetch(url: str) -> tuple[int | None, str | None, bytes, str | None]:
    request = Request(url, headers={"User-Agent": "ClipForge-R2-Smoke/1.0"})
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, response.headers.get("Content-Type"), response.read(), None
    except HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type"), exc.read(512), None
    except URLError as exc:
        return None, None, b"", type(exc.reason).__name__


def _safe_url_summary(url: str) -> dict:
    parsed = urlparse(url)
    return {
        "scheme": parsed.scheme,
        "host": parsed.netloc,
        "path": parsed.path,
        "has_query": bool(parsed.query),
    }


def main() -> int:
    storage = R2Storage()
    if storage.config.public_bucket_name == storage.config.private_bucket_name and storage.config.mode == "dual":
        raise StorageError("Dual-bucket smoke test requires distinct public and private buckets.")

    run_id = uuid.uuid4().hex
    public_key = f"smoke-tests/{run_id}/public-test.txt"
    private_key = f"smoke-tests/{run_id}/private-test.txt"
    public_body = f"clipforge public r2 smoke {run_id}\n".encode("utf-8")
    private_body = f"clipforge private r2 smoke {run_id}\n".encode("utf-8")
    results: dict[str, object] = {
        "mode": storage.config.mode,
        "public_bucket": storage.config.public_bucket_name,
        "private_bucket": storage.config.private_bucket_name,
        "public": {},
        "private": {},
        "cleanup": {},
        "safety": {
            "no_ark": True,
            "no_seedance": True,
            "no_database": True,
            "no_presigned_url_printed": True,
        },
    }

    public_deleted = False
    private_deleted = False
    try:
        with tempfile.TemporaryDirectory(prefix="clipforge-r2-smoke-") as tmpdir:
            public_file = Path(tmpdir) / "public-test.txt"
            private_file = Path(tmpdir) / "private-test.txt"
            public_file.write_bytes(public_body)
            private_file.write_bytes(private_body)

            public_stored = storage.save_public_asset(
                project_id=1,
                source_path=public_file,
                object_key=public_key,
                content_type="text/plain; charset=utf-8",
            )
            results["public"] = {
                "upload": True,
                "head": storage.exists(public_key, expected_size=len(public_body), visibility="public"),
                "url": _safe_url_summary(public_stored["access_url"]),
                "credential_query": bool(urlparse(public_stored["access_url"]).query),
            }
            public_status, public_type, public_read, public_error = _fetch(public_stored["access_url"])
            results["public"].update(
                {
                    "http_status": public_status,
                    "content_type": public_type,
                    "body_matches": public_read == public_body,
                    "fetch_error": public_error,
                }
            )

            private_stored = storage.save_private_video(
                project_id=1,
                source_path=private_file,
                object_key=private_key,
                content_type="text/plain; charset=utf-8",
            )
            unsigned_private_url = storage.get_public_url(private_key)
            unsigned_status, unsigned_type, _, unsigned_error = _fetch(unsigned_private_url)
            presigned = storage.get_download_url(private_key, expires_in=600)
            presigned_status, presigned_type, presigned_read, presigned_error = _fetch(presigned)
            results["private"] = {
                "upload": True,
                "head": storage.exists(private_key, expected_size=len(private_body), visibility="private"),
                "stored_access_url_is_none": private_stored["access_url"] is None,
                "unsigned_url": _safe_url_summary(unsigned_private_url),
                "unsigned_http_status": unsigned_status,
                "unsigned_content_type": unsigned_type,
                "unsigned_fetch_error": unsigned_error,
                "presigned_url": _safe_url_summary(presigned),
                "presigned_http_status": presigned_status,
                "presigned_content_type": presigned_type,
                "presigned_body_matches": presigned_read == private_body,
                "presigned_fetch_error": presigned_error,
            }
    finally:
        try:
            storage.delete(public_key, visibility="public")
            public_deleted = not storage.exists(public_key, visibility="public")
        except Exception as exc:
            results["cleanup"]["public_error"] = type(exc).__name__
        try:
            storage.delete(private_key, visibility="private")
            private_deleted = not storage.exists(private_key, visibility="private")
        except Exception as exc:
            results["cleanup"]["private_error"] = type(exc).__name__
        results["cleanup"].update(
            {
                "public_deleted": public_deleted,
                "private_deleted": private_deleted,
                "public_key": public_key,
                "private_key": private_key,
            }
        )

    public_ok = (
        results["public"].get("head")
        and results["public"].get("http_status") == 200
        and results["public"].get("body_matches")
        and results["public"].get("url", {}).get("scheme") == "https"
        and not results["public"].get("credential_query")
        and public_deleted
    )
    private_unsigned_denied = results["private"].get("unsigned_http_status") in {403, 404}
    private_ok = (
        results["private"].get("head")
        and results["private"].get("stored_access_url_is_none")
        and private_unsigned_denied
        and results["private"].get("presigned_http_status") == 200
        and results["private"].get("presigned_body_matches")
        and private_deleted
    )
    results["ok"] = bool(public_ok and private_ok)
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0 if results["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
