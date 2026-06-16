# ClipForge 3.0 Current Status

## Current State

- Current development branch: `clipforge-v3-real-provider-alpha`
- Current branch HEAD at start of download recovery pass: `7df78643fe33b1c6fc9fd1c58ecf97e1ee5a2eeb`
- Last verified functional baseline: `b18b6974fd63f748fe37a140644f8b83c212efc8`
- ClipForge 3.0 is in Real Provider Alpha.
- Mock workflow is runnable.
- Ark Provider is wired.
- Paid confirmation is implemented.
- Idempotency protection against duplicate paid submissions is implemented and hardened.
- `unknown_submission_state` is implemented and now blocks automatic re-submit.
- HTTPS product reference images now enter the Ark Payload.
- Payload Inspector can safely construct a request without sending it.
- Take persistence is now idempotent per generation submission.
- Usage/cost persistence is now idempotent per generation submission.
- Budget check now runs before real-provider reservation for new submissions.
- New real-provider constraints include `v3_generation_submissions.budget_approved_at`, `v3_takes.generation_submission_id UNIQUE`, and `v3_usage_events.event_key UNIQUE`.
- One real paid Seedance task has been executed.
- The provider task succeeded; the first local download failed with `403` because a signed result URL was sanitized before download.
- Existing-task recovery now re-queries the provider task, preserves the runtime signed URL for download, and guarantees `NO NEW PROVIDER SUBMISSION`.
- The recovered real task downloaded successfully, created one Take, and recorded one provider generation usage/cost event.
- Real provider task count remains `1`.

## Verified Commands

```bash
python3 -m pytest -q tests/v3
python3 -m pytest -q tests/test_legacy_routes.py
python3 -m compileall clipforge_v3 scripts/v3
python3 scripts/v3/inspect_real_seedance_payload.py
```

Current recorded results:

- V3: `84 passed`
- V3 after download recovery hardening: `101 passed`
- Real Provider Alpha: `37 passed`
- Legacy routes: `3 passed`

## Safe Payload Inspection

```bash
export V3_REAL_TEST_IMAGE_URL="https://public-product-image-url"
python3 scripts/v3/inspect_real_seedance_payload.py
```

This inspector:

- Does not call paid APIs.
- Does not call `submit_task`.
- Does not call `requests.post`.

## Next Tasks

1. Review the readiness audit: `docs/clipforge-v3/REAL_PROVIDER_READINESS_AUDIT.md`
2. Start object storage integration for durable uploaded assets and generated videos.
3. Add local-image auto-upload to object storage after the storage adapter is in place.
4. Add long-running worker soak tests.
5. Do not deploy production before object storage is complete.

## Real Paid Test Protection

Required environment:

```bash
export V3_VIDEO_PROVIDER=ark
export V3_REAL_API_ENABLED=true
export V3_REAL_API_TEST_CONFIRM=I_UNDERSTAND_THIS_COSTS_MONEY
export V3_REAL_TEST_IMAGE_URL=https://...
```

The terminal must also receive:

```text
YES_PAY_SEEDANCE_ONCE
```

Automatic repeated tests are forbidden.

## Not Complete

- Object storage
- Local image auto-upload
- Batch real-product validation
- Long-running Worker tests
- External user authentication
- Production deployment
