# ClipForge 3.0 Current Status

## Current State

- Current development branch: `clipforge-v3-real-provider-alpha`
- Current baseline commit: `b18b6974fd63f748fe37a140644f8b83c212efc8`
- ClipForge 3.0 is in Real Provider Alpha.
- Mock workflow is runnable.
- Ark Provider is wired.
- Paid confirmation is implemented.
- Idempotency protection against duplicate paid submissions is implemented.
- `unknown_submission_state` is implemented.
- HTTPS product reference images now enter the Ark Payload.
- Payload Inspector can safely construct a request without sending it.
- Real paid Seedance testing has not been executed yet.

## Verified Commands

```bash
python3 -m pytest -q tests/v3
python3 -m pytest -q tests/test_legacy_routes.py
python3 -m compileall clipforge_v3 scripts/v3
python3 scripts/v3/inspect_real_seedance_payload.py
```

Current recorded results:

- V3: `84 passed`
- Real Provider Alpha: `20 passed`
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

1. Use a real public product image to run Payload Inspector.
2. Manually inspect the final Ark Payload format.
3. Run one 5-second, 720p, single-shot real paid test.
4. Check task ID, polling, download, Take, and cost records.
5. Consider object storage only after the single-shot test succeeds.
6. Do not deploy production before object storage is complete.

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
