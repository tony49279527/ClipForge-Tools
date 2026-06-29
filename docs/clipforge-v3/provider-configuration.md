# Provider Configuration

Seedance V3 has two separate execution modes:

```bash
# Default safe mode. No paid API call is made.
export V3_VIDEO_PROVIDER=mock
export V3_REAL_API_ENABLED=false
```

Real Ark testing requires both provider mode and paid API gate:

```bash
export V3_VIDEO_PROVIDER=ark
export V3_REAL_API_ENABLED=true
export ARK_API_KEY="..."
```

Do not set these in normal local demos or CI. `ARK_API_KEY` alone is not enough to trigger a real request.

Provider configuration:

```bash
export SEEDANCE_PROVIDER=ark
export SEEDANCE_MODEL="your-model"
export SEEDANCE_BASE_URL="https://..."
export SEEDANCE_DEFAULT_RESOLUTION=720p
export SEEDANCE_GENERATE_AUDIO=true
export SEEDANCE_WATERMARK=false
export SEEDANCE_PROMPT_MAX_CHARS=2000
```

The provider adapter validates mode, duration, ratio, resolution, reference roles, audio, watermark, and payload shape. Payload previews sanitize API keys, bearer tokens, credential paths, and secrets.

Paid generation guardrails:

- Web requests reserve an idempotency key before provider submission.
- Repeated clicks with the same project, shot, prompt version, tier, provider, duration, resolution, and reference asset version reuse the same submission.
- If a provider task ID already exists, workers only poll and never submit a second paid task.
- HTTP timeout enters `unknown_submission_state`; it is not retried automatically because the provider may have accepted and billed the task.
- Mock videos are marked with the `mock` provider and must not be treated as real Seedance output.

Manual one-shot real test:

```bash
export V3_VIDEO_PROVIDER=ark
export V3_REAL_API_ENABLED=true
export V3_REAL_API_TEST_CONFIRM=I_UNDERSTAND_THIS_COSTS_MONEY
python3 scripts/v3/test_real_seedance_single_shot.py
```

CI does not call paid APIs. Real provider checks must remain manual and explicitly confirmed.
