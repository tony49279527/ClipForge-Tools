from __future__ import annotations

from collections.abc import Callable

from db import get_conn, utc_now


MigrationFn = Callable[[], None]
V3_TABLE_ALLOWLIST = {
    "v3_projects",
    "v3_product_truth",
    "v3_assets",
    "v3_shots",
    "v3_prompt_versions",
    "v3_takes",
    "v3_reviews",
    "v3_continuity_states",
    "v3_usage_events",
    "v3_preflight_checks",
    "v3_final_assemblies",
    "v3_retake_plans",
    "v3_generation_submissions",
}


def _ensure_schema_migrations_table() -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def _apply_create_v3_core_tables() -> None:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS v3_projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_name TEXT NOT NULL,
            product_name TEXT NOT NULL,
            product_category TEXT NOT NULL,
            target_market TEXT NOT NULL,
            target_audience TEXT NOT NULL,
            target_platform TEXT NOT NULL,
            aspect_ratio TEXT NOT NULL,
            total_duration INTEGER NOT NULL,
            default_clip_duration INTEGER NOT NULL,
            resolution TEXT NOT NULL,
            language TEXT NOT NULL,
            project_status TEXT NOT NULL,
            current_stage TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS v3_product_truth (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            source_description TEXT NOT NULL,
            immutable_geometry_json TEXT NOT NULL,
            dimensions_json TEXT NOT NULL,
            material_json TEXT NOT NULL,
            colors_json TEXT NOT NULL,
            components_json TEXT NOT NULL,
            installation_rules_json TEXT NOT NULL,
            working_surface_json TEXT NOT NULL,
            allowed_behaviors_json TEXT NOT NULL,
            forbidden_transformations_json TEXT NOT NULL,
            forbidden_materials_json TEXT NOT NULL,
            safety_constraints_json TEXT NOT NULL,
            confidence_json TEXT NOT NULL,
            user_approved INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES v3_projects(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS v3_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            asset_type TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            local_path TEXT,
            remote_url TEXT,
            mime_type TEXT,
            primary_role TEXT NOT NULL,
            secondary_role TEXT,
            must_transfer_json TEXT NOT NULL,
            must_not_transfer_json TEXT NOT NULL,
            applies_to_shots_json TEXT NOT NULL,
            is_identity_anchor INTEGER NOT NULL DEFAULT 0,
            user_approved INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES v3_projects(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS v3_shots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            shot_id TEXT NOT NULL,
            sequence_index INTEGER NOT NULL,
            purpose TEXT NOT NULL,
            mode TEXT NOT NULL,
            duration INTEGER NOT NULL,
            primary_spend TEXT NOT NULL,
            secondary_spend TEXT,
            economized_json TEXT NOT NULL,
            subject_action TEXT NOT NULL,
            start_state_json TEXT NOT NULL,
            end_state_json TEXT NOT NULL,
            camera_contract_json TEXT NOT NULL,
            lighting_contract_json TEXT NOT NULL,
            audio_contract_json TEXT NOT NULL,
            reference_roles_json TEXT NOT NULL,
            continuity_anchors_json TEXT NOT NULL,
            constraints_json TEXT NOT NULL,
            risk_codes_json TEXT NOT NULL,
            generation_strategy TEXT NOT NULL,
            depends_on_shot_id TEXT,
            status TEXT NOT NULL,
            user_approved INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES v3_projects(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS v3_prompt_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shot_id INTEGER NOT NULL,
            version INTEGER NOT NULL,
            mode TEXT NOT NULL,
            prompt_text TEXT NOT NULL,
            prompt_char_count INTEGER NOT NULL,
            prompt_language TEXT NOT NULL,
            role_map_json TEXT NOT NULL,
            compiler_warnings_json TEXT NOT NULL,
            validation_result_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(shot_id) REFERENCES v3_shots(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS v3_takes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shot_id INTEGER NOT NULL,
            take_number INTEGER NOT NULL,
            prompt_version_id INTEGER NOT NULL,
            seedance_task_id TEXT,
            status TEXT NOT NULL,
            local_path TEXT,
            remote_url TEXT,
            first_frame_path TEXT,
            last_frame_path TEXT,
            seed INTEGER,
            generation_settings_json TEXT NOT NULL,
            changed_variable TEXT,
            parent_take_id INTEGER,
            token_usage INTEGER NOT NULL DEFAULT 0,
            estimated_cost REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(shot_id) REFERENCES v3_shots(id),
            FOREIGN KEY(prompt_version_id) REFERENCES v3_prompt_versions(id),
            FOREIGN KEY(parent_take_id) REFERENCES v3_takes(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS v3_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            take_id INTEGER NOT NULL,
            verdict TEXT NOT NULL,
            product_identity_score INTEGER NOT NULL,
            mechanical_accuracy_score INTEGER NOT NULL,
            motion_realism_score INTEGER NOT NULL,
            camera_execution_score INTEGER NOT NULL,
            continuity_score INTEGER NOT NULL,
            commercial_usability_score INTEGER NOT NULL,
            error_codes_json TEXT NOT NULL,
            reviewer_notes TEXT,
            next_action TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(take_id) REFERENCES v3_takes(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS v3_continuity_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            shot_id INTEGER NOT NULL,
            product_state_json TEXT NOT NULL,
            machine_state_json TEXT NOT NULL,
            workpiece_state_json TEXT NOT NULL,
            camera_state_json TEXT NOT NULL,
            lighting_state_json TEXT NOT NULL,
            environment_state_json TEXT NOT NULL,
            action_state_json TEXT NOT NULL,
            sound_state_json TEXT NOT NULL,
            source_take_id INTEGER,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES v3_projects(id),
            FOREIGN KEY(shot_id) REFERENCES v3_shots(id),
            FOREIGN KEY(source_take_id) REFERENCES v3_takes(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS v3_usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            shot_id INTEGER,
            take_id INTEGER,
            stage TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            duration INTEGER,
            resolution TEXT,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            estimated_cost REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            raw_usage_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES v3_projects(id),
            FOREIGN KEY(shot_id) REFERENCES v3_shots(id),
            FOREIGN KEY(take_id) REFERENCES v3_takes(id)
        )
        """
    )

    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_product_truth_project_id ON v3_product_truth(project_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_assets_project_id ON v3_assets(project_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_shots_project_id ON v3_shots(project_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_prompt_versions_shot_id ON v3_prompt_versions(shot_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_takes_shot_id ON v3_takes(shot_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_reviews_take_id ON v3_reviews(take_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_continuity_project_id ON v3_continuity_states(project_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_usage_project_id ON v3_usage_events(project_id)")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_v3_shots_project_sequence ON v3_shots(project_id, sequence_index)")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_v3_prompt_versions_shot_version ON v3_prompt_versions(shot_id, version)")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_v3_takes_shot_take_number ON v3_takes(shot_id, take_number)")

    conn.commit()
    conn.close()


def _ensure_column(table_name: str, column_name: str, column_definition: str) -> None:
    if table_name not in V3_TABLE_ALLOWLIST:
        raise ValueError(f"Unsupported v3 migration table: {table_name}")
    conn = get_conn()
    cur = conn.cursor()
    if not cur.column_exists(table_name, column_name):
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")
    conn.commit()
    conn.close()


def _apply_expand_v3_director_tables() -> None:
    _ensure_column("v3_projects", "product_url", "TEXT DEFAULT ''")
    _ensure_column("v3_projects", "dimensions_input", "TEXT DEFAULT ''")
    _ensure_column("v3_projects", "materials_input", "TEXT DEFAULT ''")
    _ensure_column("v3_projects", "package_quantity", "TEXT DEFAULT ''")
    _ensure_column("v3_projects", "parts_summary", "TEXT DEFAULT ''")
    _ensure_column("v3_projects", "installation_method", "TEXT DEFAULT ''")
    _ensure_column("v3_projects", "working_surface_input", "TEXT DEFAULT ''")
    _ensure_column("v3_projects", "intended_for", "TEXT DEFAULT ''")
    _ensure_column("v3_projects", "not_for", "TEXT DEFAULT ''")
    _ensure_column("v3_projects", "safety_notes", "TEXT DEFAULT ''")
    _ensure_column("v3_projects", "director_plan_status", "TEXT DEFAULT 'not_started'")

    _ensure_column("v3_product_truth", "product_truth_json", "TEXT DEFAULT '{}'")
    _ensure_column("v3_product_truth", "invalidates_shots", "INTEGER DEFAULT 0")

    _ensure_column("v3_assets", "audit_report_json", "TEXT DEFAULT '{}'")

    _ensure_column("v3_shots", "commercial_beat", "TEXT DEFAULT ''")
    _ensure_column("v3_shots", "single_visible_beat", "TEXT DEFAULT ''")
    _ensure_column("v3_shots", "mode_decision_json", "TEXT DEFAULT '{}'")
    _ensure_column("v3_shots", "fidelity_json", "TEXT DEFAULT '{}'")
    _ensure_column("v3_shots", "locked_by_user", "INTEGER DEFAULT 0")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_assets_role ON v3_assets(project_id, primary_role)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_shots_status ON v3_shots(project_id, status)")
    conn.commit()
    conn.close()


def _apply_expand_v3_generation_tables() -> None:
    _ensure_column("v3_prompt_versions", "raw_draft_prompt", "TEXT DEFAULT ''")
    _ensure_column("v3_prompt_versions", "anti_slop_prompt", "TEXT DEFAULT ''")
    _ensure_column("v3_prompt_versions", "compressed_prompt", "TEXT DEFAULT ''")
    _ensure_column("v3_prompt_versions", "removed_items_json", "TEXT DEFAULT '[]'")
    _ensure_column("v3_prompt_versions", "provider_payload_json", "TEXT DEFAULT '{}'")
    _ensure_column("v3_prompt_versions", "allow_submit", "INTEGER DEFAULT 0")
    _ensure_column("v3_prompt_versions", "locked_by_user", "INTEGER DEFAULT 0")
    _ensure_column("v3_takes", "tier", "TEXT DEFAULT 'draft'")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS v3_preflight_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            shot_id INTEGER NOT NULL,
            prompt_version_id INTEGER,
            tier TEXT NOT NULL,
            allow_submit INTEGER NOT NULL DEFAULT 0,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES v3_projects(id),
            FOREIGN KEY(shot_id) REFERENCES v3_shots(id),
            FOREIGN KEY(prompt_version_id) REFERENCES v3_prompt_versions(id)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_preflight_shot_id ON v3_preflight_checks(shot_id)")
    conn.commit()
    conn.close()


def _apply_expand_v3_execution_tables() -> None:
    _ensure_column("v3_projects", "max_draft_takes", "INTEGER DEFAULT 5")
    _ensure_column("v3_projects", "max_production_takes", "INTEGER DEFAULT 3")
    _ensure_column("v3_projects", "max_cost_cny", "REAL DEFAULT 300")
    _ensure_column("v3_projects", "max_generation_seconds", "INTEGER DEFAULT 180")
    _ensure_column("v3_projects", "good_enough_definition", "TEXT DEFAULT 'identity>=8 and mechanical>=8'")
    _ensure_column("v3_projects", "final_assembly_valid", "INTEGER DEFAULT 0")
    _ensure_column("v3_shots", "continuity_group", "TEXT DEFAULT 'default'")
    _ensure_column("v3_shots", "selected_take_id", "INTEGER")
    _ensure_column("v3_shots", "max_draft_takes", "INTEGER")
    _ensure_column("v3_shots", "max_production_takes", "INTEGER")
    _ensure_column("v3_shots", "max_cost_cny", "REAL")
    _ensure_column("v3_shots", "max_generation_seconds", "INTEGER")
    _ensure_column("v3_shots", "good_enough_definition", "TEXT DEFAULT ''")
    _ensure_column("v3_takes", "previous_value", "TEXT")
    _ensure_column("v3_takes", "new_value", "TEXT")
    _ensure_column("v3_takes", "change_reason", "TEXT DEFAULT ''")
    _ensure_column("v3_takes", "source_asset_ids_json", "TEXT DEFAULT '[]'")
    _ensure_column("v3_takes", "qc_frame_paths_json", "TEXT DEFAULT '[]'")
    _ensure_column("v3_takes", "selected_by_user", "INTEGER DEFAULT 0")
    _ensure_column("v3_takes", "selected_at", "TEXT")
    _ensure_column("v3_takes", "uncontrolled_revision", "INTEGER DEFAULT 0")
    _ensure_column("v3_takes", "deleted_local_file", "INTEGER DEFAULT 0")
    _ensure_column("v3_takes", "restored_from_take_id", "INTEGER")
    _ensure_column("v3_takes", "review_summary_json", "TEXT DEFAULT '{}'")
    _ensure_column("v3_reviews", "material_accuracy_score", "INTEGER DEFAULT 0")
    _ensure_column("v3_reviews", "safety", "TEXT DEFAULT 'pass'")
    _ensure_column("v3_reviews", "ai_suggestion_json", "TEXT DEFAULT '{}'")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS v3_final_assemblies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            version INTEGER NOT NULL,
            status TEXT NOT NULL,
            output_path TEXT,
            assembly_take_ids_json TEXT NOT NULL DEFAULT '[]',
            invalidated INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES v3_projects(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS v3_retake_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            take_id INTEGER NOT NULL,
            verdict TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(take_id) REFERENCES v3_takes(id)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_shots_selected_take ON v3_shots(selected_take_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_final_assemblies_project ON v3_final_assemblies(project_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_retake_take_id ON v3_retake_plans(take_id)")
    conn.commit()
    conn.close()


def _apply_expand_v3_product_ops_tables() -> None:
    _ensure_column("v3_assets", "replaced_by_asset_id", "INTEGER")
    _ensure_column("v3_assets", "deleted_at", "TEXT")
    _ensure_column("v3_assets", "storage_backend", "TEXT DEFAULT 'local'")
    _ensure_column("v3_assets", "access_url", "TEXT")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS v3_operation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL,
            project_id INTEGER,
            shot_id INTEGER,
            take_id INTEGER,
            task_id TEXT,
            provider TEXT,
            stage TEXT NOT NULL,
            duration REAL DEFAULT 0,
            status TEXT NOT NULL,
            error_code TEXT,
            retry_count INTEGER DEFAULT 0,
            elapsed_time REAL DEFAULT 0,
            message TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES v3_projects(id),
            FOREIGN KEY(shot_id) REFERENCES v3_shots(id),
            FOREIGN KEY(take_id) REFERENCES v3_takes(id)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_projects_status_created ON v3_projects(project_status, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_product_truth_project_version ON v3_product_truth(project_id, version)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_assets_project_status ON v3_assets(project_id, user_approved, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_shots_project_sequence_status ON v3_shots(project_id, sequence_index, status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_prompt_versions_shot_created ON v3_prompt_versions(shot_id, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_takes_shot_status_created ON v3_takes(shot_id, status, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_takes_selected ON v3_takes(selected_by_user, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_reviews_take_created ON v3_reviews(take_id, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_continuity_project_shot ON v3_continuity_states(project_id, shot_id, version)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_usage_project_stage_created ON v3_usage_events(project_id, stage, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_operation_project_stage_created ON v3_operation_events(project_id, stage, created_at)")
    conn.commit()
    conn.close()


def _apply_v3_real_provider_alpha_tables() -> None:
    _ensure_column("v3_takes", "idempotency_key", "TEXT")
    _ensure_column("v3_takes", "submission_status", "TEXT")
    _ensure_column("v3_takes", "provider_task_id", "TEXT")
    _ensure_column("v3_takes", "provider_request_hash", "TEXT")
    _ensure_column("v3_takes", "submission_started_at", "TEXT")
    _ensure_column("v3_takes", "submission_completed_at", "TEXT")
    _ensure_column("v3_takes", "retry_count", "INTEGER DEFAULT 0")
    _ensure_column("v3_takes", "last_poll_at", "TEXT")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS v3_generation_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            shot_id INTEGER NOT NULL,
            prompt_version_id INTEGER NOT NULL,
            generation_tier TEXT NOT NULL,
            provider TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            provider_request_hash TEXT NOT NULL,
            provider_task_id TEXT,
            submission_status TEXT NOT NULL,
            paid_confirmed INTEGER NOT NULL DEFAULT 0,
            confirmation_token TEXT,
            request_payload_json TEXT NOT NULL DEFAULT '{}',
            response_json TEXT NOT NULL DEFAULT '{}',
            error_json TEXT NOT NULL DEFAULT '{}',
            take_id INTEGER,
            rq_job_id TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            last_poll_at TEXT,
            submission_started_at TEXT,
            submission_completed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES v3_projects(id),
            FOREIGN KEY(shot_id) REFERENCES v3_shots(id),
            FOREIGN KEY(prompt_version_id) REFERENCES v3_prompt_versions(id),
            FOREIGN KEY(take_id) REFERENCES v3_takes(id)
        )
        """
    )
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_v3_generation_submissions_idempotency ON v3_generation_submissions(idempotency_key)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_generation_submissions_status ON v3_generation_submissions(project_id, submission_status, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_takes_idempotency ON v3_takes(idempotency_key)")
    conn.commit()
    conn.close()


def _apply_v3_real_provider_state_machine_hardening() -> None:
    _ensure_column("v3_generation_submissions", "budget_approved_at", "TEXT")
    _ensure_column("v3_takes", "generation_submission_id", "INTEGER")
    _ensure_column("v3_usage_events", "event_key", "TEXT")
    _ensure_column("v3_usage_events", "source_type", "TEXT")
    _ensure_column("v3_usage_events", "source_id", "INTEGER")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT generation_submission_id, COUNT(*) AS count
        FROM v3_takes
        WHERE generation_submission_id IS NOT NULL
        GROUP BY generation_submission_id
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    )
    duplicate_take = cur.fetchone()
    if duplicate_take:
        conn.close()
        raise RuntimeError(
            "Cannot add unique v3_takes.generation_submission_id constraint; "
            f"submission {duplicate_take['generation_submission_id']} already has {duplicate_take['count']} takes."
        )
    cur.execute(
        """
        SELECT event_key, COUNT(*) AS count
        FROM v3_usage_events
        WHERE event_key IS NOT NULL
        GROUP BY event_key
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    )
    duplicate_usage = cur.fetchone()
    if duplicate_usage:
        conn.close()
        raise RuntimeError(
            "Cannot add unique v3_usage_events.event_key constraint; "
            f"event key {duplicate_usage['event_key']} already has {duplicate_usage['count']} rows."
        )
    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_v3_takes_generation_submission
        ON v3_takes(generation_submission_id)
        WHERE generation_submission_id IS NOT NULL
        """
    )
    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_v3_usage_events_event_key
        ON v3_usage_events(event_key)
        WHERE event_key IS NOT NULL
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_usage_events_source ON v3_usage_events(source_type, source_id)")
    conn.commit()
    conn.close()


def _apply_v3_object_storage_tables() -> None:
    _ensure_column("v3_assets", "object_key", "TEXT")
    _ensure_column("v3_assets", "content_type", "TEXT")
    _ensure_column("v3_assets", "size_bytes", "INTEGER")
    _ensure_column("v3_takes", "storage_backend", "TEXT DEFAULT 'local'")
    _ensure_column("v3_takes", "object_key", "TEXT")
    _ensure_column("v3_takes", "content_type", "TEXT")
    _ensure_column("v3_takes", "size_bytes", "INTEGER")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE v3_assets SET storage_backend = 'local' WHERE storage_backend IS NULL OR storage_backend = ''")
    cur.execute("UPDATE v3_assets SET content_type = mime_type WHERE content_type IS NULL AND mime_type IS NOT NULL")
    cur.execute("UPDATE v3_takes SET storage_backend = 'local' WHERE storage_backend IS NULL OR storage_backend = ''")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_assets_storage_object ON v3_assets(storage_backend, object_key)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v3_takes_storage_object ON v3_takes(storage_backend, object_key)")
    conn.commit()
    conn.close()


MIGRATIONS: list[tuple[str, MigrationFn]] = [
    ("20260616_create_v3_core_tables", _apply_create_v3_core_tables),
    ("20260616_expand_v3_director_tables", _apply_expand_v3_director_tables),
    ("20260616_expand_v3_generation_tables", _apply_expand_v3_generation_tables),
    ("20260616_expand_v3_execution_tables", _apply_expand_v3_execution_tables),
    ("20260616_expand_v3_product_ops_tables", _apply_expand_v3_product_ops_tables),
    ("20260616_v3_real_provider_alpha_tables", _apply_v3_real_provider_alpha_tables),
    ("20260616_v3_real_provider_state_machine_hardening", _apply_v3_real_provider_state_machine_hardening),
    ("20260617_v3_object_storage_tables", _apply_v3_object_storage_tables),
]


def get_applied_migrations() -> set[str]:
    _ensure_schema_migrations_table()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM schema_migrations")
    rows = {row["name"] for row in cur.fetchall()}
    conn.close()
    return rows


def run_v3_migrations() -> list[str]:
    _ensure_schema_migrations_table()
    applied = get_applied_migrations()
    just_applied: list[str] = []
    for name, migration in MIGRATIONS:
        if name in applied:
            continue
        migration()
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO schema_migrations (name, applied_at) VALUES (?, ?)",
            (name, utc_now()),
        )
        conn.commit()
        conn.close()
        just_applied.append(name)
    return just_applied


def ensure_v3_schema() -> None:
    run_v3_migrations()
