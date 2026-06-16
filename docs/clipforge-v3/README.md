# ClipForge 3.0

ClipForge 3.0 is the `/v3` product director and production system for hardware, power tools, automotive tools, RV tools, industrial supplies, and Amazon products with strict physical structure requirements.

It keeps V1 and V2 intact and adds a separate V3 architecture: Product Truth, Reference Assets, Director Plan, Shot Contracts, Prompt Compiler, Seedance Provider Adapter, Continuity Ledger, Take Review, Retake Protocol, Final Assembly, and Publish Gate.

Current status:

- Mock Alpha: supported. It exercises the director workflow, database state, prompt compiler, mock video generation, review, continuity, and final assembly without paid APIs.
- Real API Test: supported for one manually confirmed Seedance shot. It requires explicit environment gates and a second confirmation before submission.
- Production: not complete. Durable object storage, long-running paid task validation, external user authentication, broader real-product validation, and production security review remain required.

Enable it with:

```bash
export CLIPFORGE_V3_ENABLED=true
python3 -m uvicorn app:app --reload
```

Main routes:

- `/v3`
- `/v3/projects`
- `/v3/projects/new`
- `/v3/projects/{project_id}`
- `/v3/projects/{project_id}/status`
- `/v3/health`
- `/v3/ready`

Offline verification:

```bash
python3 scripts/v3/migrate_v3.py
python3 scripts/test_v3_workflow_smoke.py
python3 evaluation/run_evals.py
python3 -m pytest -q
```

Provider modes:

```bash
# Default and safe mode.
export V3_VIDEO_PROVIDER=mock
export V3_REAL_API_ENABLED=false

# Real paid test mode. Do not use without operator confirmation.
export V3_VIDEO_PROVIDER=ark
export V3_REAL_API_ENABLED=true
export ARK_API_KEY="..."
```

Real paid Seedance calls are blocked unless all of the following are true:

- `V3_VIDEO_PROVIDER=ark`
- `V3_REAL_API_ENABLED=true`
- the user confirms the paid generation for that shot
- the backend receives the expected confirmation token
- an idempotency reservation has been created before provider submission

Manual one-shot real API test:

```bash
export V3_VIDEO_PROVIDER=ark
export V3_REAL_API_ENABLED=true
export V3_REAL_API_TEST_CONFIRM=I_UNDERSTAND_THIS_COSTS_MONEY
python3 scripts/v3/test_real_seedance_single_shot.py
```

The script refuses to run by default. It prints provider, model, shot, duration, resolution, reference count, estimated cost, and idempotency key prefix before requiring terminal confirmation.
