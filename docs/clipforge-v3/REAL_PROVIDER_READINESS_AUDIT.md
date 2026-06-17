# Real Provider Readiness Audit

## 1. Executive Summary

ClipForge V3 has moved beyond the earlier Mock-only state. The current branch does have a real Ark submission path wired through `clipforge_v3/services/generation_service.py::submit_generation` and `::process_generation_submission`, and the provider adapter in `clipforge_v3/providers/seedance_ark.py` can build payloads, submit tasks, poll task state, extract a video URL, download the file, create a Take, and write a cost event.

The state-machine hardening pass resolved the three billing-safety findings that blocked the first paid provider check. A single manually authorized 5-second 720p task has now been submitted and the provider completed it successfully. The first local download failed with `403` because the signed result URL was sanitized before the downloader used it; the recovery pass now re-queries the existing provider task, preserves the runtime signed URL for download, and finalizes the existing submission without creating a new provider task. This does not make the branch production ready.

Resolved in state-machine hardening commit:

1. `unknown_submission_state` no longer re-enqueues or calls `submit_task()` automatically. It returns `manual_reconciliation_required` with `possible_charge` and `auto_retry_disabled`.
2. Provider success persistence is replay-safe. A generation submission can own only one Take through `v3_takes.generation_submission_id`, and video generation cost can be written only once through `v3_usage_events.event_key`.
3. New real-provider submissions run `_ensure_budget()` before `reserve_generation_submission()`, and old `reserved` rows without `budget_approved_at` cannot be submitted automatically.

Object storage integration has now been added behind `V3_STORAGE_BACKEND=r2` with `LocalStorage` preserved as the default. Real R2 public/private smoke validation has passed without Ark generation; the next storage task is private generated-video playback/download UI integration.

## 2. Current Repository Baseline

- Repository: `tony49279527/ClipForge-Tools`
- Branch: `clipforge-v3-real-provider-alpha`
- Current branch HEAD at start of download recovery pass: `7df78643fe33b1c6fc9fd1c58ecf97e1ee5a2eeb`
- Last verified functional baseline: `b18b6974fd63f748fe37a140644f8b83c212efc8`
- Latest completed commit before recovery pass: `7df7864 fix(v3): harden real provider submission state machine`

Current local verification in this audit run:

- `python -m pytest -q tests/v3` -> `101 passed`
- `python -m pytest -q tests/test_legacy_routes.py` -> `3 passed`
- `python -m pytest --collect-only -q tests/v3` -> not rerun in this recovery pass
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
  - `_ensure_budget()` runs before new `v3_generation_submissions` reservation rows are created
  - `v3_generation_submissions` reservation row with `budget_approved_at`
  - later `rq_job_id` update if queueing succeeds
- Paid risk: yes
- Tests:
  - `tests/v3/test_real_provider_alpha.py::test_paid_confirmation_required`
  - `::test_wrong_paid_confirmation_token_is_rejected`
  - `::test_idempotency_duplicate_click_reuses_submission`
  - `::test_budget_failure_does_not_create_submit_ready_reservation`
  - `::test_old_reserved_row_without_budget_approval_cannot_submit`
- Current usability: guarded alpha path; real paid test still not executed

### 4.7 Provider submit, polling, download, Take, cost, and shot status

- Worker wrapper: `task_queue.py::run_v3_generation_wrapper`
- Task entry: `clipforge_v3/tasks.py::run_generation_submission_task`
- Core state machine: `generation_service.process_generation_submission()`
- Submit:
  - `ArkSeedanceProvider.submit_task()`
  - only runs after `take_repository.claim_generation_submission_for_submit()` atomically moves `reserved` or `queued` rows with `budget_approved_at` to `submitting`
  - worker retries from `unknown_submission_state` or uncertain `submitting` without `provider_task_id` return manual reconciliation instead of submitting again
- Poll:
  - `ArkSeedanceProvider.get_task_status()`
  - loops until success/failure/cancel/poll timeout
- Download:
  - `generation_service._download_provider_video()`
  - saves to `outputs/{project_id}/shots/{shot_id}/takes/{take_number}/video.mp4`
  - uses the runtime result URL from the latest provider status response, including signed query parameters
  - a download failure records `download_failed` and can be recovered without creating another provider task
- DB writes on success:
  - `v3_takes` with `generation_submission_id`
  - `v3_generation_submissions.take_id`
  - `v3_usage_events` with `event_key = provider_generation:{submission_id}`
  - `v3_shots.status = generated`
- DB writes when provider succeeded but local download fails:
  - `v3_usage_events` is still recorded once, because provider generation cost has already occurred
  - `v3_generation_submissions.submission_status = download_failed`
  - no Take is created until the artifact is recovered
- Paid risk: yes
- Tests:
  - `tests/v3/test_real_provider_alpha.py::test_worker_retry_with_saved_task_id_only_polls`
  - `::test_timeout_enters_unknown_submission_state`
  - `::test_provider_success_replay_is_idempotent_after_worker_crash`
  - `::test_completed_provider_submission_can_be_replayed_without_duplicate_take_or_cost`
  - `::test_database_constraints_prevent_duplicate_take_and_usage_for_submission`
  - `::test_existing_provider_task_recovery_download_failure_does_not_submit_or_duplicate`
  - `::test_existing_provider_task_recovery_success_is_idempotent`
- Current usability: one real paid task succeeded and was recovered locally; production still blocked by object storage

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

Observations after state-machine hardening:

- There is no dedicated database table named "generation jobs". The durable state model is `v3_generation_submissions`; the queue job itself only lives in Redis/RQ and is referenced by `rq_job_id`.
- Submission-to-take linkage is now protected in both directions:
  - `v3_generation_submissions.take_id` stores the final Take ID
  - `v3_takes.generation_submission_id` is unique when present
- Real-provider idempotency protection now includes:
  - `v3_generation_submissions.idempotency_key UNIQUE`
  - `v3_generation_submissions.budget_approved_at`
  - `v3_takes.generation_submission_id UNIQUE`
  - `v3_usage_events.event_key UNIQUE`

State-model risks:

1. **Duplicate paid submit after unknown state**
   - Resolved in state-machine hardening commit.
   - `generation_service.requires_manual_reconciliation()` classifies `unknown_submission_state` as manual-only.
   - `submit_generation()` and `process_generation_submission()` return a manual reconciliation result instead of queueing or calling `submit_task()`.

2. **Duplicate Take or duplicate cost row after crash**
   - Resolved in state-machine hardening commit.
   - `take_repository.get_or_create_take_for_submission()` recovers existing Takes by `generation_submission_id`.
   - `project_repository.create_usage_event()` returns the existing row for repeated `event_key` writes.
   - Tests simulate crashes after download, Take creation, submission linking, and usage write.

3. **Budget check ordering bug**
   - Resolved in state-machine hardening commit.
   - New submissions run `_ensure_budget()` before `reserve_generation_submission()`.
   - Old `reserved` rows with no `budget_approved_at`, no `provider_task_id`, and no job are treated as manual reconciliation, not submit-ready work.

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

State-machine hardening changes:

- `can_enqueue_submission()`, `can_call_provider_submit()`, `can_poll_existing_task()`, `requires_manual_reconciliation()`, and `is_terminal_submission_state()` centralize submission-state decisions in `generation_service.py`.
- `claim_generation_submission_for_submit()` atomically claims only `reserved` or `queued` rows with `budget_approved_at`.
- `unknown_submission_state` and uncertain `submitting` without `provider_task_id` do not auto-retry; they require manual Ark console reconciliation.
- Existing `provider_task_id` rows can be replayed through polling and download/finalization without calling `submit_task()` again.

Long-run risks remain:

- If a worker crashes after provider accept but before persisting `provider_task_id`, the row becomes manual-only rather than auto-retried; a future reconciliation command is still needed.
- Poll errors do not back off beyond `min(poll_interval, 1.0)` sleep, which is fine for tests but not ideal for production.
- Download retry is now explicit for existing provider tasks through `generation_service.recover_generation_submission()` and `scripts/v3/recover_real_seedance_submission.py`.
- There is no provider-side reconciliation command for `unknown_submission_state`.

Download recovery finding from the first real task:

- Ark returned a temporary signed TOS video URL.
- The first implementation sanitized the status response before runtime extraction, stripping the query string needed for authorized download.
- Recovery now keeps the raw provider status response in memory, sanitizes only database/log persistence, and re-queries the existing task before download.
- The recovery script prints `NO NEW PROVIDER SUBMISSION` and does not contain a provider creation path.

## 9. Storage Gap Analysis

Current state:

- Asset uploads use `LocalStorage` by default.
- `clipforge_v3/services/storage_service.py` now provides `LocalStorage` and `R2Storage`.
- `V3_STORAGE_BACKEND=r2` supports dual-bucket R2 mode with `R2_PUBLIC_BUCKET_NAME` for product reference images and `R2_PRIVATE_BUCKET_NAME` for generated videos. The old `R2_BUCKET_NAME` single-bucket mode remains supported for compatibility.
- Product image uploads verify the public-bucket object through `head_object` and store `v3_assets.storage_backend`, `object_key`, `content_type`, `size_bytes`, and stable HTTPS `access_url`.
- Generated provider videos still download from Ark to a temporary local file first. In R2 mode, `_store_provider_video_artifact()` uploads that file to the private bucket, verifies the object, and stores `v3_takes.storage_backend`, `object_key`, `content_type`, and `size_bytes`.
- Generated videos use service-layer signed GET URLs through `R2Storage.get_download_url()` against the private bucket; presigned URLs are not persisted.

Remaining production blockers after object storage code integration:

- Real R2 smoke validation has passed for public upload/read/delete and private upload/presigned-read/delete using dedicated smoke-test objects.
- UI playback for private R2 videos still needs a signed-download endpoint or equivalent integration.
- Bucket lifecycle, retention, and cleanup policy are not automated.
- External authorization for private generated-video access is not implemented.

Minimal object storage options:

| Option | Pros | Cons | Fit |
| --- | --- | --- | --- |
| Cloudflare R2 | S3-compatible API, low storage cost, no egress charge, easy public HTTPS/custom domain, simple future signed URLs | Another vendor to add, public URL setup still needed | Best alpha fit |
| AWS S3 | Most mature SDK/docs, native signed URLs, broad ecosystem | Egress cost, more infrastructure overhead for a small alpha | Strong but heavier |
| Backblaze B2 / other S3-compatible storage | Lower cost than S3, S3-compatible enough for a simple adapter | More edge-case compatibility testing than S3/R2 | Acceptable fallback |

Implemented alpha path: **Cloudflare R2**. It matches the current code shape because the app only needs:

1. upload object
2. store stable object key
3. produce either a public HTTPS URL or a signed HTTPS URL that Ark can fetch
4. keep the same S3-style adapter abstraction for future portability

Detailed design and operator setup notes: `docs/clipforge-v3/OBJECT_STORAGE.md`.

## 10. Test Coverage Map

Current mapping by file:

- `test_buffing_wheel.py` (3): Product Truth extraction and prompt fact retention
- `test_execution_loop.py` (15): scheduling, mock generation loop, take lifecycle, assembly invalidation, review-driven retakes
- `test_productization.py` (7): UI surfaces, readiness secrecy, storage rejection, traversal blocking, workflow smoke
- `test_prompt_compiler.py` (11): prompt limits, templates, conflict detection, payload resolution field, error redaction
- `test_provider_and_preflight.py` (5): fail-open/fail-closed policy and provider capability checks
- `test_object_storage.py` (17): dual-bucket R2 configuration, single-bucket compatibility, key safety, mocked uploads, provider artifact upload recovery, signed URL generation, migration repeatability
- `test_real_provider_alpha.py` (37): paid confirmation, idempotency, HTTPS reference URLs, unknown state, worker polling reuse, crash replay, budget ordering, database constraints, download recovery, inspector safety
- `test_v3_routes.py` (9): V3 routing, migrations, project creation, invalidation behavior
- `test_v3_schemas.py` (14): schema validation and planner helper behavior

High-priority gaps resolved in state-machine hardening commit:

1. Crash after download before Take creation should not create duplicate Take/cost on rerun.
2. Crash after Take creation but before submission update should not create a second Take on rerun.
3. Crash after submission update but before `create_usage_event()` should not duplicate cost events.
4. `unknown_submission_state` must never call `submit_task()` again unless an operator explicitly reconciles provider state first.
5. Budget failure before reservation must not leave a reusable submit-ready row.

Remaining high-priority missing tests:

1. Successful local file serving through `/v3/storage/local/...` still needs a positive-path route test; `LocalStorage.base_dir` now exists.
2. Provider reconciliation command for `unknown_submission_state` is not implemented or tested.
3. Real provider download recovery has one successful live example, but URL lifetime behavior still needs longer-window evidence.
4. Object storage is covered by mock tests, and real R2 smoke behavior has been validated for upload/read/presigned-read/delete. Private-video UI playback integration remains open.
5. Long-running worker soak behavior is not covered.

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

Resolved in state-machine hardening commit:

1. `unknown_submission_state` can no longer lead to a second automatic paid submit.
2. Paid success persistence now has database-backed idempotency for Take and usage/cost writes.
3. Budget enforcement now occurs before new submission reservation, and old unapproved reservations cannot submit automatically.

Remaining high-risk gaps:

1. No operator reconciliation workflow exists yet for `unknown_submission_state`.
2. Only one real Ark task has been validated; broader product and provider-state coverage is still missing.
3. R2 code is implemented and smoke-validated against Cloudflare R2 public/private buckets.
4. Private R2 generated-video playback is not yet wired into the UI.

## 15. Recommended Development Order

1. Add UI integration for private R2 generated-video playback/download
2. Add long-running worker soak testing with R2 enabled
3. Implement operator reconciliation for `unknown_submission_state`
4. Perform long-running worker soak tests
5. Expand real-product validation batch coverage
6. Add external authentication/authorization
7. Prepare production deployment

## 16. Single Next Recommended Task

**Add UI integration for private R2 generated-video playback/download.**

Scope of that task:

- use explicit R2 credentials supplied through environment/secret storage, not chat
- validate one product-image object upload and public HTTPS read
- validate one generated-video style object upload and presigned GET
- validate delete/cleanup on the dedicated test bucket
- verify no Ark/Seedance submit path is invoked

That is the highest-leverage next step because real R2 endpoint behavior, bucket policy, public URL configuration, and signed URL semantics have passed smoke validation, while generated-video playback still lacks a user-facing signed-download path.
