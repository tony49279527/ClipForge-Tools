# ClipForge 3.0 Acceptance Audit

Audit date: 2026-06-16  
Audited workspace: `/Users/liangxile/Documents/Codex/2026-05-18/tony49279527-clipforge-tools-https-github-com/repo`  
Rule: no production feature code was modified during audit. This report file is the only intended repository change from the audit.

## 1. Executive Summary

ClipForge 3.0 is substantially implemented in the local working tree and passes the local Mock test suite, but it is not committed to the current GitHub-tracked commit. `HEAD` is `3285d1130eaf0ef5d1f415a54e5e759a1e5240ab` on `main`, matching `origin/main`; all V3 code is currently dirty/untracked local state. Therefore the GitHub repository at the current commit does not yet contain the completed V3 implementation.

Local working-tree V3 status: Mock Alpha is runnable.  
Committed GitHub status: No-Go, because V3 is not part of `origin/main`.

Key blockers:

- P0: V3 exists only as local uncommitted/untracked files; GitHub deployments from `origin/main` will not include V3.
- P1: V3 generation route uses local mock MP4 generation instead of `ArkSeedanceProvider.submit_task`; real paid generation is not wired into the V3 submit path.
- P1: Product Truth extraction missed explicit `1-inch thickness` and `1/2-inch center hole` fields in the Buffing Wheel audit despite source input containing them.
- P1: Director Evals runner is mostly a simulated rule checker, not a true invocation of the V3 director pipeline.

## 2. Repository State

Commands executed:

```bash
git status --short
git status --branch --porcelain=v1
git branch --all
git log --oneline --decorate -30
git rev-parse --abbrev-ref HEAD
git rev-parse HEAD
```

Results:

- Current branch: `main`
- Current commit: `3285d1130eaf0ef5d1f415a54e5e759a1e5240ab`
- Branch tracking: `## main...origin/main`
- Remote branches: `origin/main`, `origin/dev`
- Latest commit: `3285d11 (HEAD -> main, origin/main, origin/HEAD) feat: product URL scraper + auto prompt builder + 15 category templates + CTA end card`
- Modified tracked files: `README.md`, `app.py`, `db.py`, `requirements.txt`, `task_queue.py`, `templates/base.html`
- Untracked V3-related paths: `.github/`, `clipforge_v3/`, `docs/`, `evaluation/`, `scripts/`, `static/v3/`, `templates/v3/`, `tests/`

Conclusion:

- Not fully in GitHub `main`.
- Not in another local branch.
- V3 is local working-tree implementation only.
- Local implementation is partial-to-strong Mock Alpha.

## 3. Test Environment

- Python command: `python3`
- Python version observed: `Python 3.9.6`
- FFmpeg present enough for mock video generation and final concat tests.
- External paid APIs were not called.
- Warnings observed:
  - `urllib3` NotOpenSSLWarning due LibreSSL.
  - Google API warning for unsupported Python 3.9.
  - FastAPI `on_event` deprecation.
  - Starlette `TemplateResponse` argument order deprecation.

## 4. Test Commands and Results

Commands executed:

```bash
python3 -m compileall app.py db.py task_queue.py worker.py clipforge_v3 evaluation scripts tests
python3 -m pytest -q tests/test_legacy_routes.py tests/v3/test_v3_routes.py tests/v3/test_prompt_compiler.py tests/v3/test_provider_and_preflight.py tests/v3/test_execution_loop.py tests/v3/test_productization.py tests/v3/test_buffing_wheel.py tests/v3/test_v3_schemas.py
python3 scripts/test_v3_workflow_smoke.py
python3 evaluation/run_evals.py
python3 scripts/v3/migrate_v3.py
```

Results:

- Compile/import: exit `0`
- Pytest: exit `0`, `66 passed`, `0 failed`, `0 skipped`, `134 warnings`
- V3 smoke: exit `0`; created 4 shots, 4 selected takes, final assembly MP4
- Director evals: exit `0`; `20` cases, `0` failures
- Migrations: exit `0`; current DB applied `20260616_expand_v3_product_ops_tables`

## 5. Feature Matrix

| Area | Status | Evidence |
|---|---:|---|
| V1/V2 compatibility | PASS | `tests/test_legacy_routes.py`, `/`, `/jobs`, `/v2`, `/v2/jobs` routes present |
| V3 route isolation | PASS local | `clipforge_v3/router.py`, `APIRouter(prefix="/v3")`; tests verify disabled V3 does not break V1/V2 |
| Product Truth | PARTIAL | `product_truth_service.extract_product_truth_payload`; Pydantic validation exists, but Buffing Wheel `thickness` and `center_hole` extracted empty |
| Assets | PARTIAL | `asset_service.create_asset`, `storage_service.LocalStorage`; image/video/audio/document type support exists, but audit is shallow |
| Director System | PARTIAL | `mode_router`, `fidelity_allocator`, `shot_planner`; planner emits fixed 4-beat structure |
| Shot Contracts | PASS local / PARTIAL quality | `v3_shots` populated and used; generated plan is rule-based |
| Prompt Compiler | PARTIAL | real staged functions exist; linter coverage is incomplete |
| Provider Adapter | PARTIAL | `ArkSeedanceProvider` implements interface; V3 submit path does not call `submit_task` |
| Preflight | PASS local | `compiler/preflight.py`, `generation_service.preflight`; blocking errors stop submit |
| Continuity | PARTIAL | ledger rows written/read; semantic content is template-derived, not visual-QA-derived |
| Take/Review/Retake | PASS local / PARTIAL quality | tables and tests pass; edit/extend semantics not truly provider-backed |
| Final Assembly | PASS local | `assembly_service.rebuild_final_video` uses selected takes and FFmpeg concat |
| UI | PARTIAL | V3 console routes/actions exist; advanced interactions are basic forms, no true drag/drop/sync playback |
| Cost | PARTIAL | estimates recorded; not provider-billing accurate |
| Security | PARTIAL | path/MIME/log sanitization exists; remote URL SSRF handling is not implemented because remote download is not enabled |
| Storage | PARTIAL | LocalStorage real; CloudStorage is interface/NotImplemented |
| Tests | PASS local | 66 passing tests |
| Evals | PARTIAL | 20 cases but runner simulates expected behavior instead of invoking actual director pipeline |
| CI | PARTIAL | workflow files exist locally but are untracked; not active on GitHub until committed |
| Docs | PARTIAL | docs exist locally; some statements overstate real Seedance readiness |

## 6. Buffing Wheel End-to-End Result

Mock flow executed with no paid API:

Create project -> confirm Product Truth -> upload identity image -> Director Plan -> confirm Shot Contracts -> Prompt Compile -> Preflight -> Mock Draft -> Take 1 -> simulated M001 Review -> Retake Plan -> Take 2 -> Select Take -> Continuity -> Mock Production -> first/last frames -> Final Assembly -> Publish Gate.

Result: completed locally.

Tables written:

- `v3_projects`: 1
- `v3_product_truth`: 2
- `v3_assets`: 1
- `v3_shots`: 4
- `v3_prompt_versions`: 8
- `v3_takes`: 6
- `v3_reviews`: 5
- `v3_continuity_states`: 6
- `v3_usage_events`: 7
- `v3_preflight_checks`: 10
- `v3_retake_plans`: 1
- `v3_final_assemblies`: 1

Files created:

- Mock MP4 per Take
- `first_frame.jpg`
- `last_frame.jpg`
- final assembly MP4

Actual Product Truth JSON excerpt:

```json
{
  "immutable_geometry": {
    "shape": "circular wheel",
    "diameter": "6-inch",
    "thickness": "",
    "center_hole": "",
    "component_count": "1 wheel, approximately 70 ply",
    "other": ["6-inch diameter", "1-inch thickness", "1/2-inch center hole"]
  },
  "materials": {
    "correct": ["natural off-white cotton"],
    "forbidden": ["wool", "felt", "synthetic fur", "grinding stone"]
  },
  "working_surface": {
    "correct": ["outer cotton circumference"]
  }
}
```

First fracture point: Product Truth failed to place `1-inch thickness` and `1/2-inch center hole` into their dedicated geometry fields.

Actual Shot Plan:

| Shot | Purpose | Mode | Duration | Dependency | Prompt Chars |
|---|---|---:|---:|---|---:|
| S01 | `product_structure_proof` | I2V | 5 | none | 1605 |
| S02 | `installation_relationship_proof` | I2V | 5 | S01 | 1743 |
| S03 | `working_surface_proof` | I2V | 5 | S02 | 1713 |
| S04 | `result_proof` | I2V | 5 | S03 | 1724 |

Mock Buffing Wheel Payload excerpt:

```json
{
  "model": "audit-mock-seedance",
  "mode": "I2V",
  "ratio": "16:9",
  "duration": 5,
  "resolution": "1080p",
  "generate_audio": true,
  "watermark": false,
  "content": [
    {"type": "text", "text": "Preserve image-anchored product identity..."},
    {"type": "reference_role", "label": "Asset1", "role": "product_identity"}
  ]
}
```

API key leak check: false in payload.

Retake result:

- Simulated error: `M001`
- Retake Planner output: `verdict=REWRITE`, `changed_variable=one_prompt_clause`
- Selected takes: `[2, 3, 4, 5]`
- Publish Gate: `allow_publish=true`

## 7. P0 / P1 / P2 / P3

### P0

ID: P0-001  
Impact: GitHub deployments do not include V3.  
Evidence: `git status --short` shows `clipforge_v3/`, `.github/`, `docs/`, `evaluation/`, `scripts/`, `templates/v3/`, `tests/` as untracked; `HEAD` equals `origin/main`.  
Files/functions: repository state.  
Repro: run `git status --short && git rev-parse HEAD`.  
Fix direction: commit/push V3 implementation after review.  
Blocks real use: yes.

### P1

ID: P1-001  
Impact: Real Seedance generation is not wired into V3 submit flow.  
Evidence: `clipforge_v3/services/generation_service.py::_build_mock_video` and `submit_generation` create local MP4 and mock task ID; `ArkSeedanceProvider.submit_task` exists but is not called from V3 submit.  
Repro: run V3 smoke and observe `mock-*` task IDs.  
Fix direction: add provider-backed async task path with explicit paid API gate.  
Blocks real use: yes.

ID: P1-002  
Impact: Product Truth misses important geometry fields.  
Evidence: Buffing Wheel audit output has `thickness=""` and `center_hole=""` while source includes `1-inch thickness` and `1/2-inch center hole`.  
Files/functions: `clipforge_v3/services/product_truth_service.py::extract_product_truth_payload`, `director/intake.py::detect_measurement`.  
Repro: run Buffing Wheel audit flow.  
Fix direction: harden extraction and add exact assertions for dedicated fields.  
Blocks real hardware accuracy: yes.

ID: P1-003  
Impact: Director Evals can pass while real director code is wrong.  
Evidence: `evaluation/run_evals.py::simulate_director` returns expected modes via hardcoded keyword matching.  
Fix direction: invoke actual V3 director and compiler services in eval runner.  
Blocks production-quality validation: yes.

### P2

ID: P2-001  
Impact: Prompt linter is incomplete.  
Evidence: `prompt_compiler.py` checks only limited patterns: `"overall geometry"`, forbidden material regex, `" and "` in beat, comma/and in camera. Many requested checks are not exhaustive.  
Blocks real use: partially.

ID: P2-002  
Impact: Shot planner is fixed 4-shot template.  
Evidence: `shot_planner.default_beats` always emits structure, installation, working surface, result.  
Blocks real use: partially.

ID: P2-003  
Impact: Cloud storage is not implemented.  
Evidence: `storage_service.CloudStorage` raises `StorageError`; abstract methods have `NotImplementedError`.  
Blocks production deployment: yes for durable storage.

ID: P2-004  
Impact: CI is not active in GitHub.  
Evidence: `.github/workflows/ci.yml` is untracked local file.  
Blocks merge confidence: yes.

ID: P2-005  
Impact: UI advanced features are basic forms, not full studio interactions.  
Evidence: `templates/v3/project_detail.html` uses basic forms for copy/split/disable/review; no true drag/drop or synchronized playback JS.  
Blocks internal alpha: no.

### P3

ID: P3-001  
Impact: Deprecation warnings.  
Evidence: FastAPI `on_event` and Starlette `TemplateResponse` warnings during pytest.  
Fix direction: migrate to lifespan and new TemplateResponse signature.

ID: P3-002  
Impact: README still contains old object-storage development note that can confuse V3 status.  
Evidence: README mentions reserved `upload_to_object_storage`.  
Fix direction: clarify V1/V2 vs V3 storage paths.

## 8. Dead Code and Placeholders

- `clipforge_v3/providers/base.py`: `pass` in simple model classes; acceptable as Pydantic-like data containers if intentional, but minimal.
- `clipforge_v3/providers/base_adapter.py`: abstract `NotImplementedError`; acceptable interface.
- `clipforge_v3/services/storage_service.py::CloudStorage`: explicit placeholder; production cloud storage not implemented.
- `clipforge_v3/services/generation_service.py::_build_mock_video`: intentionally mock; currently the active V3 generation path.
- `evaluation/run_evals.py::simulate_director`: hardcoded eval simulation, not actual pipeline.

## 9. Broken Routes and Buttons

Registered V3 routes were enumerated from FastAPI. Template form `action` and `formaction` values in `templates/v3/project_detail.html` were matched against registered routes after substituting sample IDs.

Result: no broken V3 form actions found in the audit script.

Limitations:

- Some UI capabilities are simplified forms, not full interactive widgets.
- `Take compare` has an API route, but the page only displays first two takes side-by-side rather than operator-selected synchronized playback.

## 10. Database Findings

All expected V3 tables exist in a fresh migrated DB:

- `v3_projects`: PASS
- `v3_product_truth`: PASS schema / PARTIAL extraction quality
- `v3_assets`: PASS schema / PARTIAL audit depth
- `v3_shots`: PASS
- `v3_prompt_versions`: PASS
- `v3_takes`: PASS
- `v3_reviews`: PASS
- `v3_continuity_states`: PASS
- `v3_usage_events`: PASS
- `v3_preflight_checks`: PASS
- `v3_retake_plans`: PASS
- `v3_final_assemblies`: PASS
- `v3_operation_events`: PASS
- `schema_migrations`: PASS

Migration names observed:

- `20260616_create_v3_core_tables`
- `20260616_expand_v3_director_tables`
- `20260616_expand_v3_execution_tables`
- `20260616_expand_v3_generation_tables`
- `20260616_expand_v3_product_ops_tables`

Indexes exist for high-frequency fields including project, status, sequence, shot, take, selected take, and created timestamps.

## 11. Prompt and Seedance Findings

Prompt Compiler flow exists in `generation_service.compile_prompt`:

Product Truth -> Shot -> Role Map -> Continuity -> `prompt_compiler` stages -> `v3_prompt_versions` -> provider payload.

2000-character audit:

- 1999 chars: PASS
- 2000 chars: PASS
- 2001 chars: blocking error `prompt_over_budget`
- No direct truncation observed in compression test.

Anti-slop words are removed by `GENERIC_QUALITY_WORDS`: `cinematic`, `epic`, `stunning`, `masterpiece`, `beautiful`, `professional quality`, `highly dynamic`, `8k`, `award-winning`.

Seedance payload:

- `model`, `content`, `ratio`, `duration`, `resolution`, `generate_audio`, `watermark` are present.
- `resolution` enters payload.
- `generate_audio` and `watermark` come from config.
- API key not in payload preview.

Critical gap:

- V3 submit path does not call real Seedance provider. It writes mock MP4 and mock task IDs.

## 12. Continuity and Retake Findings

Continuity:

- `scheduling_service` computes dependencies and detects cycles.
- Preflight blocks downstream if dependency has no selected take.
- `continuity_service` writes ledger rows after take creation and reads them for compile.
- Identity re-anchor interval is tested with `V3_IDENTITY_REANCHOR_INTERVAL=2`.

Limitations:

- Ledger content is derived from contract/end state, not actual AI video visual analysis.
- Original identity image re-anchor is role injection, not visual verification.

Retake:

- Reviews write structured scores into `v3_reviews`.
- `plan_retake` handles `M001`, `M002`, `R002`, `A008`, repeated errors.
- Single-variable rule is represented by `changed_variable`; uncontrolled multi-change can be flagged.
- `REROLL` same prompt and `REWRITE` new prompt version are tested.

Limitations:

- `EDIT`/`extend` are protocol-level, not real provider-backed edits.

## 13. Security and Duplicate-Charge Risks

Security PASS/PARTIAL:

- Local upload path traversal blocked in `storage_service.LocalStorage`.
- MIME allowlist exists.
- File size limit via `V3_MAX_UPLOAD_BYTES`.
- Local file serving route validates filename and project directory.
- Payload/log sanitization exists in `observability_service.sanitize`.
- Health/Ready do not reveal API key value.
- FFmpeg called with arg arrays, not shell strings.

Remaining risks:

- Remote URL download/SSRF controls are not implemented because remote download is not enabled.
- MIME validation relies on upload content type plus PIL for images; non-image deep validation is shallow.
- Duplicate paid charge protection is incomplete for future real provider path. Current mock path creates new takes on repeated submit; no idempotency lock around paid provider call.
- RQ retry behavior for future V3 paid jobs is not fully specified.

## 14. V1/V2 Regression

PASS local.

Evidence:

- Routes `/`, `/jobs`, `/v2`, `/v2/jobs` exist.
- `tests/test_legacy_routes.py` passed.
- `tests/v3/test_v3_routes.py::test_v3_disable_flag_does_not_break_v1_v2` verifies V3 disabled returns 404 and V1/V2 still return 200.
- `test_v3_creation_does_not_write_v2_storyboard_frames` verifies V3 project creation does not write V2 `storyboard_frames`.

## 15. Documentation Accuracy

Docs are broad and useful, but overstate readiness in places unless read with the Mock/Local caveats.

Accurate:

- V3 architecture, routes, local run, evals, security notes.

Needs correction before external use:

- Make explicit that V3 production generation is mock until provider-backed submit is wired.
- Make explicit that evals are simulated, not actual director pipeline tests.
- Make explicit that uncommitted local files are not deployed from GitHub.

## 16. 30项 Scorecard

| Item | Score |
|---|---:|
| V1/V2 兼容 | 8 |
| V3 架构 | 7 |
| 数据库 | 7 |
| Product Truth | 5 |
| Assets | 6 |
| Mode Router | 5 |
| Fidelity Allocation | 5 |
| Shot Contract | 6 |
| Prompt Compiler | 6 |
| 2000 字符控制 | 8 |
| Linter | 5 |
| Provider | 5 |
| Payload | 7 |
| Preflight | 7 |
| Continuity | 6 |
| 调度 | 6 |
| Take | 7 |
| Review | 6 |
| Retake | 5 |
| 错误码 | 5 |
| Final Assembly | 7 |
| UI | 6 |
| 成本 | 5 |
| 安全 | 6 |
| Storage | 5 |
| Tests | 8 |
| Evals | 4 |
| CI | 6 local / 0 remote-active |
| Docs | 7 |
| 生产准备度 | 4 |

Average working-tree score: about `5.9/10`.  
Committed GitHub score: much lower for V3 because files are not committed.

## 17. Final Grade

Working tree grade: `B- / Mock Alpha`.  
GitHub repository grade at current `origin/main`: `F for V3 availability`, because V3 files are not committed.

Overall audit grade for requested GitHub acceptance: `C` because local implementation is meaningful and tested, but not committed and not real-provider production-ready.

## 18. Go / No-Go

- 可以继续内部开发: Go
- 可以开始 Mock Alpha: Go, local working tree only
- 可以开始少量真实 Seedance 测试: No-Go until V3 submit path calls provider with explicit paid gate/idempotency
- 可以给内部操作人员使用: No-Go for real production; Go only for guided Mock workflow
- 可以给外部客户使用: No-Go
- 可以作为收费产品: No-Go

## 19. Recommended Repair Order

1. Commit V3 files to a review branch or push to GitHub so CI can run against actual repo state.
2. Wire V3 `submit_generation` to provider-backed async task execution behind explicit paid API confirmation.
3. Add idempotency locks and retry semantics for paid generation to avoid duplicate charges.
4. Fix Product Truth geometry extraction and add exact Buffing Wheel assertions for thickness and center hole.
5. Replace eval simulation with actual director pipeline invocation.
6. Expand Prompt Linter to cover all requested mechanical, reference, unsafe-action, and provider-capability checks.
7. Add real cloud storage adapter or clearly disable production file persistence claims.
8. Harden non-image MIME validation and remote asset policy before remote URL ingestion.
9. Update docs to clearly separate Mock Alpha from production.
10. Migrate FastAPI startup and TemplateResponse deprecations.

## 20. Honest Final Conclusion

ClipForge 3.0 is not just documentation or empty files: the local working tree contains real routes, tables, repositories, services, templates, migrations, tests, mock generation, review, retake, continuity, and final assembly. The local Mock workflow is demonstrably runnable.

It is not production-ready. The biggest reasons are: V3 is not committed to GitHub, real Seedance generation is not used by the V3 submit path, Product Truth extraction is not reliable enough for hardware products, evals are not true pipeline evals, and paid-task idempotency is not implemented.

The correct next milestone is not external deployment. The correct next milestone is a committed Mock Alpha branch plus provider-backed real Seedance dry-run infrastructure with strict cost/idempotency controls.
