from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from clipforge_v3.services.project_service import run_migrations


def main() -> None:
    applied = run_migrations()
    if applied:
        print("Applied migrations:")
        for name in applied:
            print(f"- {name}")
    else:
        print("No new migrations were required.")


if __name__ == "__main__":
    main()
