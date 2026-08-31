"""Persistence layer.

    workflow_repository  THE SEAM — every caller imports this and nothing else
    sqlite_repository    the SQLite implementation (default)
    supabase_repository  the Supabase implementation, via PostgREST
    supabase_client      the shared, lazily-built Supabase connection
    database             SQLite connection, schema and idempotent migrations
    schema_supabase.sql  the Postgres schema, run by hand in the SQL editor

Which backend runs is decided by config.uses_supabase(): SUPABASE_URL plus a
key selects Supabase, anything else stays on SQLite.
"""
