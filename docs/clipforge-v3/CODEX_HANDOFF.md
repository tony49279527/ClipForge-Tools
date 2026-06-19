# ClipForge V3 Codex Handoff

## Current Checkpoint

- Repository: `https://github.com/tony49279527/ClipForge-Tools.git`
- Branch: `clipforge-v3-real-provider-alpha`
- Remote HEAD before this handoff note: `db8181fd79da6efab92b2a535c817e0fe1ffb9d9`
- Last completed commit: `ops(v3): deploy guided mock UI demo candidate`
- Working tree before this handoff note: clean

## Completed In Last Session

- Stabilized `tests/v3/test_queue_worker_config.py::test_worker_service_health_fails_when_worker_process_exits` without relying on sandbox-blocked socket binding.
- Deployed Cloud Run tag `v3-ui-demo` at `0%` traffic.
- Verified the guided `/v3` mock demo flow through project creation, Product Truth save, demo image, prompt, mock generation, and result display.
- Confirmed provider is `mock`, cost remains `0`, and no Ark / Seedance paid request is made.
- Confirmed production traffic remains `100%` on SQLite revision `clipforge-tools-00104-hwm`.

## Demo Entry

- Demo URL: `https://v3-ui-demo---clipforge-tools-znaw4q4ldq-uc.a.run.app/v3`
- `/v3`: verified `200`
- `/v3/ready`: verified `200`
- Demo revision: `clipforge-tools-00115-kay`
- Demo tag traffic: `0%`

## Tests Passed

- `python3 -m pytest -q tests/v3/test_queue_worker_config.py::test_worker_service_health_fails_when_worker_process_exits`
- `python3 -m pytest -q tests/v3/test_queue_worker_config.py`
- `python3 -m pytest -q tests/v3/test_v3_demo_ui.py`
- `python3 -m pytest -q tests/v3`
- `python3 -m pytest -q tests/test_legacy_routes.py tests/test_secret_logging.py`
- `PYTHONPYCACHEPREFIX=/tmp/clipforge-pyc python3 -m compileall clipforge_v3 scripts/v3`
- `git diff --check`

## Known Boundaries

- No final PostgreSQL production cutover was performed.
- No Ark / Seedance task was created.
- No paid video generation was executed.
- No production traffic was shifted to the demo tag.
- Do not commit secrets, database files, generated videos, or local snapshots.

## Next Single Task

Ask the user to click through the `v3-ui-demo` page and report confusing labels, broken buttons, or unclear errors. Then continue front-end polish from that feedback.

## Resume Commands

```bash
git fetch origin --prune
git checkout clipforge-v3-real-provider-alpha
git pull --ff-only origin clipforge-v3-real-provider-alpha
git status --short --branch
```
