# Real Provider Readiness Audit

## 1. Executive Summary

ClipForge V3 has moved beyond the earlier Mock-only state. The current branch does have a real Ark submission path wired through `clipforge_v3/services/generation_service.py::submit_generation` and `::process_generation_submission`, and the provider adapter in `clipforge_v3/providers/seedance_ark.py` can build payloads, submit tasks, poll task state, extract a video URL, download the file, create a Take, and write a cost event.

That said, the branch is not yet safe enough for a paid real-provider test without one more hardening pass. The biggest issue is not payload shape anymore. The biggest issue is state safety around duplicate billing and duplicate persistence:

1. `unknown_submission_state` is documented as "do not retry automatically", but the current code can re-enqueue and re-submit it because `submit_generation()` treats that state as queueable and `process_generation_submission()` will call `submit_task()` again if `provider_task_id` is still empty.
2. The success path is not crash-safe. `process_generation_submission()` downloads the video, creates a Take, updates `v3_generation_submissions`, and writes a `v3_usage_events` row as separate writes with no transaction or replay guard. A crash between those steps can create duplicate Takes or duplicate cost rows on rerun.
3. Idempotency reservation happens before budget enforcement. `take_repository.reserve_generation_submission()` commits a `reserved` row before `generation_service._ensure_budget()` runs. If the budget check fails after reservation, later retries can reuse the already-reserved row and skip the budget check.

Because of those gaps, the single next recommended task is not "run the paid test". The single next recommended task is: **make the real-provider submission state machine crash-safe and non-resubmitting before any paid Ark call is authorized**.

## 2. Current Repository Baseline

- Repository: `tony49279527/ClipForge-Tools`
- Branch: `clipforge-v3-real-provider-alpha`
- Current branch HEAD after sync: `30d1c6351e1f43d9da15c8b2c45b91bd8b108bfc`
- Last verified functional baseline: `b18b6974fd63f748fe37a140644f8b83c212efc8`
- Latest docs commit on branch: `30d1c63 docs(v3): add current development handoff status`

Current local verification in this audit run:

- `python -m pytest -q tests/v3` -> `84 passed`
- `python -m pytest -q tests/test_legacy_routes.py` -> `3 passed`
- `python -m pytest --collect-only -q tests/v3` -> `84 tests collected`
- Warning runs with `-W default` still pass, but expose Python 3.9, LibreSSL, FastAPI `on_event`, and Starlette templating deprecations

## 3. V3 Architecture Map

Top-level modules used by the real-provider path:

- Web entrypoint: `app.py`
  - Includes the V3 router with `app.include_router(v3_router)`
  - Runs V3 migrations on startup when `clipforge_v3.is_v3_enabled()` is true
- V3 router: `clipforge_v3/router.py`
  - Handles project creation, asset uploads, planning, compile, preflight, mock submit, and real submit routes
- Project orchestration: `clipforge_v3/services/project_service.py`
  - Creates the project, bootstraps Product Truth, assembles page detail state, and controls workflow stage
- Shot planning: `clipforge_v3/services/shot_service.py`
  - Builds and mutates the shot board and initial prompt versions
- Prompt compile and submit path: `clipforge_v3/services/generation_service.py`
  - Compiles the final provider payload
  - Creates preflight rows
  - Reserves a paid submission
  - Calls the provider, polls, downloads, creates a Take, and writes usage
- Provider adapter: `clipforge_v3/providers/seedance_ark.py`
  - Builds Ark payloads
  - Performs `requests.post()` for submit and `requests.get()` for polling
- Asset URL policy: `clipforge_v3/services/provider_asset_resolver.py`
  - Converts approved assets into Ark-usable public HTTPS references
- Persistence: `clipforge_v3/repositories/*.py`
  - SQLite row access for V3 tables
- Queue and worker: `task_queue.py`, `worker.py`, `clipforge_v3/tasks.py`
  - Redis/RQ queueing with a stable job ID per idempotency key

## 4. End-to-End Generation Flow

### 4.1 User create project

- Entry: `clipforge_v3/router.py::v3_create_project`
- Service: `clipforge_v3/services/project_service.py::create_project`
- DB writes:
  - `v3_projects`
  - `v3_product_truth` bootstrap version
  - `v3_usage_events` stage `project_intake`
  - `v3_operation_events` stage `project_create`
- Paid risk: none
- Tests: `tests/v3/test_v3_routes.py::test_v3_project_can_be_created`
- Current usability: yes

### 4.2 Add product assets

- Entry: `clipforge_v3/router.py::v3_upload_asset`
- Storage: `clipforge_v3/services/storage_service.py::LocalStorage.save_upload`
- Metadata write: `clipforge_v3/services/asset_service.py::create_asset`
- DB writes:
  - file under `uploads/v3/{project_id}/...`
  - `v3_assets`
  - `v3_operation_events` only for the route-level log event
- Paid risk: none
- Tests: `tests/v3/test_productization.py::test_storage_rejects_unsupported_upload`
- Current usability: local-only; not yet Ark-usable unless a public HTTPS `remote_url` or `access_url` is supplied later

### 4.3 Director plan and shot contracts

- Entry: `clipforge_v3/router.py::v3_generate_director_plan`, `::v3_confirm_shots`
- Services:
  - `project_service.generate_director_plan()`
  - `shot_service.regenerate_director_plan()`
  - `project_service.confirm_shot_contracts()`
- DB writes:
  - `v3_shots`
  - initial `v3_prompt_versions`
  - project status updates
- Paid risk: none
- Tests:
  - `tests/v3/test_v3_routes.py::test_product_truth_confirm_then_director_plan`
  - `tests/v3/test_productization.py::test_shot_board_copy_split_disable`
- Current usability: yes

### 4.4 Prompt compile

- Entry: `clipforge_v3/router.py::v3_compile_shot`
- Service: `clipforge_v3/services/generation_service.py::compile_prompt`
- Inputs:
  - project row
  - shot row
  - latest Product Truth
  - approved assets
  - continuity ledger
- Payload construction:
  - prompt template and linter in `clipforge_v3/compiler/prompt_compiler.py`
  - provider reference resolution in `provider_asset_resolver.py`
  - final Ark payload in `providers/seedance_ark.py::build_payload`
- DB writes:
  - new `v3_prompt_versions` row
- Paid risk: none
- Tests:
  - `tests/v3/test_prompt_compiler.py`
  - `tests/v3/test_buffing_wheel.py`
  - `tests/v3/test_real_provider_alpha.py::test_https_remote_url_enters_ark_payload`
- Current usability: yes

### 4.5 Preflight

- Entry: `clipforge_v3/router.py::v3_preflight`
- Service: `generation_service.preflight()`
- Rules: `clipforge_v3/compiler/preflight.py::run_preflight`
- DB writes:
  - `v3_preflight_checks`
- Paid risk: none
- Tests:
  - `tests/v3/test_provider_and_preflight.py`
  - `tests/v3/test_real_provider_alpha.py::test_ark_preflight_blocks_identity_asset_without_https_source`
- Current usability: yes

### 4.6 Paid confirmation and generation submission

- Confirmation API:
  - Entry: `clipforge_v3/router.py::v3_paid_confirmation`
  - Service: `generation_service.build_paid_confirmation()`
- Real submit:
  - Entry: `clipforge_v3/router.py::v3_submit_real_generation`
  - Service: `generation_service.submit_generation()`
- Guard conditions:
  - provider mode from `get_video_provider_mode()`
  - `real_api_enabled()`
  - `paid_confirmed`
  - exact confirmation token derived from the idempotency key prefix
- DB writes for real mode:
  - `v3_preflight_checks` again, because `submit_generation()` reruns preflight
  - `v3_generation_submissions` reservation row
  - later `rq_job_id` update if queueing succeeds
- Paid risk: yes
- Tests:
  - `tests/v3/test_real_provider_alpha.py::test_paid_confirmation_required`
  - `::test_wrong_paid_confirmation_token_is_rejected`
  - `::test_idempotency_duplicate_click_reuses_submission`
- Current usability: partially yes, but not safe enough yet for a paid run

### 4.7 Provider submit, polling, download, Take, cost, and shot status

- Worker wrapper: `task_queue.py::run_v3_generation_wrapper`
- Task entry: `clipforge_v3/tasks.py::run_generation_submission_task`
- Core state machine: `generation_service.process_generation_submission()`
- Submit:
  - `ArkSeedanceProvider.submit_task()`
  - sets submission to `submitting`, then `submitted`
- Poll:
  - `ArkSeedanceProvider.get_task_status()`
  - loops until success/failure/cancel/poll timeout
- Download:
  - `generation_service._download_provider_video()`
  - saves to `outputs/{project_id}/shots/{shot_id}/takes/{take_number}/video.mp4`
- DB writes on success:
  - `v3_takes`
  - `v3_generation_submissions.take_id`
  - `v3_usage_events`
  - `v3_shots.status = generated`
- Paid risk: yes
- Tests:
  - `tests/v3/test_real_provider_alpha.py::test_worker_retry_with_saved_task_id_only_polls`
  - `::test_timeout_enters_unknown_submission_state`
- Current usability: functionally implemented, but not yet safe enough for billing-sensitive use

## 5. Database and State Model Review

V3 schema is additive and lives in `clipforge_v3/migrations.py`. Relevant tables for real-provider readiness:

- `v3_projects`
- `v3_assets`
- `v3_shots`
- `v3_prompt_versions`
- `v3_preflight_checks`
- `v3_generation_submissions`
- `v3_takes`
- `v3_usage_events`
- `v3_operation_events`

Observations:

- There is no dedicated database table named "generation jobs". The durable state model is `v3_generation_submissions`; the queue job itself only lives in Redis/RQ and is referenced by `rq_job_id`.
- Submission-to-take linkage is one-way and late-bound:
  - reserve submission first
  - later create Take
  - later update submission with `take_id`
- The only strong uniqueness protection for real paid work is `v3_generation_submissions.idempotency_key UNIQUE`.

State-model risks:

1. **Duplicate paid submit after unknown state**
   - `submit_generation()` explicitly requeues submissions in `unknown_submission_state`.
   - `process_generation_submission()` resubmits if `provider_task_id` is empty.
   - Result: a timeout after provider acceptance can still be followed by a second paid `submit_task()` call.

2. **Duplicate Take or duplicate cost row after crash**
   - Success handling is not atomic.
   - `create_take()`, `update_generation_submission(take_id=...)`, and `create_usage_event()` are separate writes.
   - A worker crash after video download but before all writes complete can replay the success path and create duplicates.

3. **Budget check ordering bug**
   - Reservation is persisted before `_ensure_budget()`.
   - A failed budget check can leave a `reserved` row that later skips budget re-validation because `created` becomes false on retry.

4. **Status-history coverage is incomplete**
   - `v3_operation_events` exist, but generation worker transitions are not logged there.
   - The most important billing-sensitive transitions only live in `v3_generation_submissions` fields.

## 6. Ark Payload Review

Actual payload builder: `clipforge_v3/providers/seedance_ark.py::ArkSeedanceProvider.build_payload`

Actual payload shape today:

- `model`
- `mode`
- `content`
  - first entry is `{type: "text", text: prompt_text}`
  - reference entries are either:
    - `{type: "image_url", image_url: {url: ...}, role: "reference_image", label: "...", reference_role: "..."}`
    - or fallback `{type: "reference_role", ...}` when no public URL exists
- `ratio`
- `duration`
- `resolution`
- `generate_audio`
- `watermark`
- `reference_audit`

Readiness conclusions:

- Public HTTPS product images enter the payload through `provider_asset_resolver.resolve_provider_reference()`, which selects `asset.remote_url` first, then `asset.access_url`, and only accepts public HTTPS URLs.
- Local files cannot be sent directly because the provider builder never reads file bytes or data URLs; it only injects `image_url.url` values that pass `validate_public_https_url()`.
- Multi-image payloads are supported in structure because `reference_roles` is iterated in order and each resolved URL becomes another `content` entry.
- Image order is stable relative to `reference_roles_json`, and identity re-anchoring prepends `product_identity` when needed in `compile_prompt()`.
- Empty image, HTTP image, private-host image, or local path cases degrade into `reference_role` placeholders and fail Ark preflight for fail-closed roles.
- Prompt can be too long and be blocked; that path is tested.
- Prompt emptiness is not explicitly blocked by a minimum-length check in `validate_final_prompt()`, but in practice the compiled template always injects subject, timing, camera, lighting, endpoint, and constraints.
- The `5s`, `720p`, and single-shot setup is controlled by the shot fixture plus project resolution, not by a dedicated "single shot" flag.

Inspector parity:

- `scripts/v3/inspect_real_seedance_payload.py` and `scripts/v3/test_real_seedance_single_shot.py` share the same compile path:
  - create project
  - confirm Product Truth
  - inject remote identity asset
  - generate director plan
  - confirm shots
  - compile prompt
  - lock prompt
  - preflight
  - build paid confirmation
- The paid script adds the final guarded `submit_generation()` call.
- So the payload construction path is effectively identical before submission.

## 7. Paid Test Safety Review

Safety conditions and where they are enforced:

- `V3_VIDEO_PROVIDER=ark`
  - `generation_service.get_video_provider_mode()`
  - `scripts/v3/test_real_seedance_single_shot.py::_guard()`
- `V3_REAL_API_ENABLED=true`
  - `generation_service.real_api_enabled()`
  - `scripts/v3/test_real_seedance_single_shot.py::_guard()`
- `V3_REAL_API_TEST_CONFIRM=I_UNDERSTAND_THIS_COSTS_MONEY`
  - only enforced in the manual test script `_guard()`
  - not enforced in the web route or service layer
- `V3_REAL_TEST_IMAGE_URL=https://...`
  - only enforced in the manual script and inspector
- `YES_PAY_SEEDANCE_ONCE`
  - only enforced in `scripts/v3/test_real_seedance_single_shot.py::main()`
  - exact string match after `.strip()`

Important implication:

- The manual script has five gates.
- The backend service path has fewer gates. The service layer itself only requires:
  - provider mode
  - `V3_REAL_API_ENABLED=true`
  - `paid_confirmed=True`
  - correct confirmation token

That is acceptable for a web-triggered real submit flow, but it means the script-level guardrails are not backend invariants.

## 8. Worker and Polling Review

- Queue system: Redis + RQ in `task_queue.py`
- Queue job ID: `v3_generation_{idempotency_key}`
- RQ retry policy: `Retry(max=MAX_RETRIES, interval=[RETRY_DELAY_SEC])`
- Worker process model: multi-process launcher in `worker.py`
- Poll interval: `V3_PROVIDER_POLL_INTERVAL_SECONDS`, default `5`
- Poll timeout: `V3_PROVIDER_POLL_TIMEOUT_SECONDS`, default `900`

Short-term behavior is covered. Long-run risks remain:

- If a worker crashes after provider accept but before persisting `provider_task_id`, an RQ retry could submit again.
- Poll errors do not back off beyond `min(poll_interval, 1.0)` sleep, which is fine for tests but not ideal for production.
- Download retry semantics are not explicit.
- There is no provider-side reconciliation command for `unknown_submission_state`.

## 9. Storage Gap Analysis

Current state:

- Asset uploads use `LocalStorage`.
- Generated provider videos download to the local filesystem under `outputs/`.
- Database rows store local paths and local output paths.

Production blockers caused by missing object storage:

- Local uploaded reference images cannot become public HTTPS references for Ark.
- Generated videos are not durable across Cloud Run restarts, machine loss, or horizontal scaling.
- Absolute or resolved local paths are machine-dependent and unsuitable for multi-host production recovery.

Minimal object storage options:

| Option | Pros | Cons | Fit |
| --- | --- | --- | --- |
| Cloudflare R2 | S3-compatible API, low storage cost, no egress charge, easy public HTTPS/custom domain, simple future signed URLs | Another vendor to add, public URL setup still needed | Best alpha fit |
| AWS S3 | Most mature SDK/docs, native signed URLs, broad ecosystem | Egress cost, more infrastructure overhead for a small alpha | Strong but heavier |
| Backblaze B2 / other S3-compatible storage | Lower cost than S3, S3-compatible enough for a simple adapter | More edge-case compatibility testing than S3/R2 | Acceptable fallback |

Recommended alpha path: **Cloudflare R2**. It matches the current code shape because the app only needs:

1. upload object
2. store stable object key
3. produce either a public HTTPS URL or a signed HTTPS URL that Ark can fetch
4. keep the same S3-style adapter abstraction for future portability

## 10. Test Coverage Map

Current mapping by file:

- `test_buffing_wheel.py` (3): Product Truth extraction and prompt fact retention
- `test_execution_loop.py` (15): scheduling, mock generation loop, take lifecycle, assembly invalidation, review-driven retakes
- `test_productization.py` (7): UI surfaces, readiness secrecy, storage rejection, traversal blocking, workflow smoke
- `test_prompt_compiler.py` (11): prompt limits, templates, conflict detection, payload resolution field, error redaction
- `test_provider_and_preflight.py` (5): fail-open/fail-closed policy and provider capability checks
- `test_real_provider_alpha.py` (20): paid confirmation, idempotency, HTTPS reference URLs, unknown state, worker polling reuse, inspector safety
- `test_v3_routes.py` (9): V3 routing, migrations, project creation, invalidation behavior
- `test_v3_schemas.py` (14): schema validation and planner helper behavior

High-priority missing tests:

1. Crash after `create_take()` but before submission update should not create a second Take on rerun.
2. Crash after submission update but before `create_usage_event()` should not duplicate cost events.
3. `unknown_submission_state` must never call `submit_task()` again unless an operator explicitly reconciles provider state first.
4. Budget failure after submission reservation must not leave a reusable `reserved` row that bypasses budget checks.
5. Successful local file serving through `/v3/storage/local/...` needs a positive-path test; current route references `storage.base_dir`, but `LocalStorage` does not define it.

Medium-priority missing tests:

1. Queue enqueue failure path should leave submission state recoverable but not silently queueable forever.
2. Poll timeout path should preserve enough reconciliation data for operator recovery.
3. Duplicate provider success responses should not redownload the same video twice.
4. Real-provider success path should verify `v3_shots.status`, `v3_generation_submissions.status`, and `v3_takes.submission_status` stay consistent.
5. Multi-image payload ordering and stable label assignment are not directly tested.

## 11. Warning Analysis

Observed warning classes:

- Python 3.9 support warning from `google.api_core`
- LibreSSL / urllib3 warning
- FastAPI `@app.on_event("startup")` deprecation
- Starlette `TemplateResponse(name, context)` deprecation

Not observed in this run:

- Pydantic deprecation warnings
- SQLAlchemy warnings
- datetime timezone warnings

Impact:

- None of these warnings block current local development or a manual Ark dry run.
- The Python 3.9 and LibreSSL warnings do matter for future production hardening.
- The FastAPI and Starlette warnings should be handled during a Python 3.11/3.12 upgrade pass, not before the paid-state-machine hardening.

## 12. Security and Secret Handling

Positive findings:

- `.env`, `.venv`, `data/`, `uploads/`, `outputs/`, `*.db`, and video files are git-ignored in `.gitignore`.
- `clipforge_v3/services/observability_service.py::sanitize` redacts API-key-like fields and strips URL query strings.
- `ArkSeedanceProvider.submit_task()` sanitizes both the request and provider response before returning them to callers.
- Inspector output only prints summary fields, not the full payload or secrets.

Remaining concerns:

- Operation-event logging is route-driven, not worker-driven, so some failure details may only exist in database JSON blobs rather than structured event history.
- `response_json` is sanitized, but provider response schema changes should still be treated as untrusted log input.

## 13. V3 and Legacy Isolation

Isolation is mostly good:

- V3 routes are mounted under `/v3`.
- V3 tables use the `v3_` prefix and do not touch legacy `jobs`, `clips`, or `storyboard_frames`.
- Legacy smoke coverage is only three tests, so merge confidence is still shallow.
- `app.py` startup does run V3 migrations when enabled, but legacy routes still pass with V3 disabled and enabled.

Merge strategy recommendation:

1. keep V3 behind `CLIPFORGE_V3_ENABLED`
2. merge docs and safe schema additions first
3. merge paid-provider hardening before any production exposure
4. expand legacy smoke coverage before merging V3 into `main`

## 14. High-Risk Gaps

1. `unknown_submission_state` can still lead to a second paid submit.
2. Paid success persistence is not atomic and can duplicate Take/cost writes after worker interruption.
3. Budget enforcement occurs after submission reservation and can be bypassed on retry.

## 15. Recommended Development Order

1. Harden submission state machine for `unknown_submission_state` and crash-safe success persistence
2. Re-run Payload Inspector with a real public HTTPS image and inspect the exact Ark payload
3. Run one manually authorized 5-second 720p paid test
4. Verify polling, download, Take creation, usage event, and recovery behavior on the real provider
5. Implement object storage and public HTTPS asset URLs
6. Add local-image auto-upload to object storage
7. Perform long-running worker soak tests
8. Expand real-product validation batch coverage
9. Add external authentication/authorization
10. Prepare production deployment

## 16. Single Next Recommended Task

**Implement a paid-generation state-machine hardening pass before any real Ark test is authorized.**

Scope of that task:

- prevent any automatic re-submit from `unknown_submission_state`
- make the success path idempotent after provider success and before/after download
- move or repeat budget validation so a previously reserved row cannot bypass it
- add tests for crash/replay and duplicate-write prevention

That is the highest-leverage next step because it directly reduces the only failure mode that can both lose money and corrupt project state.
