# ClipForge 3.0 Data Model

Core tables:

- `v3_projects`: project profile and current workflow stage.
- `v3_product_truth`: versioned structured product facts.
- `v3_assets`: identity anchors and approved references.
- `v3_shots`: full shot contract rows.
- `v3_prompt_versions`: compiled generation prompts per shot.
- `v3_takes`: generated take registry.
- `v3_reviews`: review verdicts and error codes.
- `v3_continuity_states`: per-shot continuity checkpoints.
- `v3_usage_events`: stage-level provider usage and cost trace.

The 3.0 schema is additive. Existing `jobs`, `clips`, `storyboard_frames`, `frame_image_versions`, and `usage_events` remain unchanged for 1.0 and 2.0.

Additional fields added during the director-planning phase:

- `v3_projects`: product URL and structured brief inputs for dimensions, materials, parts, installation, working surface, suitability, and safety.
- `v3_product_truth`: `product_truth_json` and `invalidates_shots`.
- `v3_assets`: `audit_report_json`.
- `v3_shots`: `commercial_beat`, `single_visible_beat`, `mode_decision_json`, `fidelity_json`, and `locked_by_user`.
