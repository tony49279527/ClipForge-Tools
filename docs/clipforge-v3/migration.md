# ClipForge 3.0 Migration

Migration entrypoint: `clipforge_v3/migrations.py`

Mechanism:

1. Ensure `schema_migrations` exists.
2. Apply each named migration once.
3. Use `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS`.
4. Record completion in `schema_migrations`.

Operational entrypoints:

- App startup calls `run_v3_migrations()`.
- Manual execution: `python scripts/v3/migrate_v3.py`

This keeps 3.0 schema work out of the legacy `init_db()` function while remaining safe to re-run.

Current migration set:

- `20260616_create_v3_core_tables`
- `20260616_expand_v3_director_tables`
