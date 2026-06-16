from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_submission(submission_id: int) -> Optional[dict]:
    from clipforge_v3.repositories import take_repository

    return take_repository.get_generation_submission(submission_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover an existing paid Seedance submission without creating a new provider task.")
    parser.add_argument("--submission-id", type=int, required=True)
    parser.add_argument("--execute", action="store_true", help="Poll and recover the existing provider task.")
    args = parser.parse_args()

    import app
    from clipforge_v3.services.generation_service import recover_generation_submission

    app.on_startup()
    submission = _load_submission(args.submission_id)
    if not submission:
        print(f"Submission not found: {args.submission_id}")
        return 2
    print("RECOVERY MODE")
    print(f"EXISTING SUBMISSION ID: {submission['id']}")
    print(f"EXISTING PROVIDER TASK ID: {submission.get('provider_task_id') or ''}")
    print("NO NEW PROVIDER SUBMISSION")
    print(f"CURRENT LOCAL STATUS: {submission.get('submission_status')}")
    if not submission.get("provider_task_id"):
        print("Cannot recover without an existing provider task ID.")
        return 3
    if not args.execute:
        print("Preview only. Pass --execute to poll and recover this existing task.")
        return 0
    result = recover_generation_submission(args.submission_id)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
