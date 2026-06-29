# ClipForge V3 Codex Handoff

## Current Checkpoint

- Repository: `https://github.com/tony49279527/ClipForge-Tools.git`
- Branch: `clipforge-v3-real-provider-alpha`
- Remote HEAD before this handoff note: `e0c6a51fee4d68f886fff58626d1b9b4b25b8007`
- Last completed operation: production PostgreSQL cutover
- Working tree before this handoff note: clean

## Completed In Last Session

- Executed final production PostgreSQL cutover after operator confirmation `YES_CUTOVER_CLIPFORGE_TO_POSTGRES`.
- Routed traffic to SQLite maintenance revision `clipforge-tools-00106-gnn` and verified writes returned maintenance `503`.
- Created final SQLite snapshot through Cloud Run Job `clipforge-final-snap-20260619100340`.
- Exported final snapshot, rebuilt Cloud SQL PostgreSQL schema, imported 81 rows, and validated migration/schema compare with `PASS`.
- Switched production traffic to PostgreSQL-backed revision `clipforge-tools-pg-worker-ready`, then to non-maintenance PostgreSQL revision `clipforge-tools-00115-kay`.
- Verified production `/`, `/v3`, and `/v3/ready` returned `200`; `/v3/ready` reported database, Redis, worker, and R2 `ok`.
- Verified invalid JSON `POST /v3/projects` returned `422`, confirming maintenance middleware no longer blocks writes without creating a row.
- Confirmed V3 provider remains `mock` and real paid API remains disabled on the production revision.
- Stabilized `tests/v3/test_queue_worker_config.py::test_worker_service_health_fails_when_worker_process_exits` without relying on sandbox-blocked socket binding.
- Deployed Cloud Run tag `v3-ui-demo` at `0%` traffic.
- Verified the guided `/v3` mock demo flow through project creation, Product Truth save, demo image, prompt, mock generation, and result display.
- Confirmed provider is `mock`, cost remains `0`, and no Ark / Seedance paid request is made.
- Historical demo-session note: before final cutover, production traffic remained `100%` on SQLite revision `clipforge-tools-00104-hwm`.

## Demo Entry

- Production URL: `https://clipforge-tools-znaw4q4ldq-uc.a.run.app/v3`
- Demo tag URL: `https://v3-ui-demo---clipforge-tools-znaw4q4ldq-uc.a.run.app/v3`
- `/v3`: verified `200`
- `/v3/ready`: verified `200`
- Production revision: `clipforge-tools-00115-kay`
- Production traffic: `100%`

## Tests Passed

- `python3 -m pytest -q tests/v3/test_queue_worker_config.py::test_worker_service_health_fails_when_worker_process_exits`
- `python3 -m pytest -q tests/v3/test_queue_worker_config.py`
- `python3 -m pytest -q tests/v3/test_v3_demo_ui.py`
- `python3 -m pytest -q tests/v3`
- `python3 -m pytest -q tests/test_legacy_routes.py tests/test_secret_logging.py`
- `PYTHONPYCACHEPREFIX=/tmp/clipforge-pyc python3 -m compileall clipforge_v3 scripts/v3`
- `git diff --check`

## Known Boundaries

- Final PostgreSQL production cutover has been performed.
- No Ark / Seedance task was created.
- No paid video generation was executed.
- Production traffic is on `clipforge-tools-00115-kay`, which also retains the `v3-ui-demo` tag.
- Do not commit secrets, database files, generated videos, or local snapshots.

## Next Single Task

Ask the user to click through the live `/v3` page and report confusing labels, broken buttons, or unclear errors. Then continue front-end polish from that feedback. Do not enable real Ark/Seedance production generation without a separate paid-provider release procedure.

## Resume Commands

```bash
git fetch origin --prune
git checkout clipforge-v3-real-provider-alpha
git pull --ff-only origin clipforge-v3-real-provider-alpha
git status --short --branch
```
