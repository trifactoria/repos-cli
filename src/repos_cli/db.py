# RepOS™ — Multi-Panel REPL-Based Developer Command Environment
# Copyright (c) 2025
# TriFactoria (Andrew Blankfield)
#
# Licensed under the Business Source License 1.1 (BSL 1.1).
# You may use, modify, and redistribute this file under the terms of the BSL.
# On the Change Date (2029-01-01), this file will be licensed under
# the Apache License, Version 2.0.

"""
Low-level database schema and core registry helpers for RepOS.

Handles:
- Schema creation and migration
- Core registry operations (project tracking)
- Table definitions and column management
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


def ensure_schema(db_path: Path) -> None:
    """Create or migrate database schema.

    Creates required tables if they don't exist:
    - aliases: panel-scoped command aliases
    - events: execution history
    - settings: persistent configuration
    - projects: core registry of known projects

    Handles migration from legacy schema by adding missing columns.

    Args:
        db_path: Path to SQLite database file

    This function is idempotent - safe to call multiple times.
    """
    # Ensure parent directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        # Create aliases table
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                panel TEXT NOT NULL,
                name TEXT NOT NULL,
                alias_key TEXT NOT NULL,
                command TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                UNIQUE(panel, name),
                UNIQUE(alias_key)
            )
            """
        )

        # Create events table
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                panel TEXT NOT NULL,
                raw_command TEXT NOT NULL,
                resolved_command TEXT NOT NULL,
                exit_code INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                duration_ms INTEGER,
                stdout TEXT,
                stderr TEXT,
                stdout_bytes_total INTEGER,
                stderr_bytes_total INTEGER,
                stdout_truncated INTEGER DEFAULT 0,
                stderr_truncated INTEGER DEFAULT 0
            )
            """
        )

        # Create settings table
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        # Create projects table (core registry)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT UNIQUE NOT NULL,
                project_name TEXT NOT NULL,
                origin_root_path TEXT NOT NULL,
                last_known_root_path TEXT NOT NULL,
                db_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL
            )
            """
        )

        # Migration: Add columns to events table if they don't exist
        try:
            conn.execute("SELECT started_at FROM events LIMIT 1")
        except sqlite3.OperationalError:
            # Columns don't exist, add them
            conn.execute(
                "ALTER TABLE events ADD COLUMN started_at TEXT"
            )
            conn.execute(
                "ALTER TABLE events ADD COLUMN duration_ms INTEGER"
            )
            conn.execute(
                "ALTER TABLE events ADD COLUMN stdout TEXT"
            )
            conn.execute(
                "ALTER TABLE events ADD COLUMN stderr TEXT"
            )
            conn.execute(
                "ALTER TABLE events "
                "ADD COLUMN stdout_bytes_total INTEGER"
            )
            conn.execute(
                "ALTER TABLE events "
                "ADD COLUMN stderr_bytes_total INTEGER"
            )
            conn.execute(
                "ALTER TABLE events "
                "ADD COLUMN stdout_truncated INTEGER DEFAULT 0"
            )
            conn.execute(
                "ALTER TABLE events "
                "ADD COLUMN stderr_truncated INTEGER DEFAULT 0"
            )

        # Migration: Handle projects table schema variations
        # Get current projects table schema
        cur = conn.execute("PRAGMA table_info(projects)")
        cols = {row[1] for row in cur.fetchall()}

        # Check if this is a legacy projects table
        # (has project_id but missing id)
        if "project_id" in cols and "id" not in cols:
            # Legacy schema: add id column
            # Create new table with correct schema
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS projects_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT UNIQUE NOT NULL,
                    project_name TEXT NOT NULL,
                    origin_root_path TEXT NOT NULL,
                    last_known_root_path TEXT NOT NULL,
                    db_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL
                )
                """
            )
            # Copy data from old table
            conn.execute(
                """
                INSERT INTO projects_new (
                    project_id, project_name, origin_root_path,
                    last_known_root_path, db_path, created_at, last_used_at
                )
                SELECT project_id, project_name, origin_root_path,
                       last_known_root_path, db_path, created_at, last_used_at
                FROM projects
                """
            )
            # Replace old table
            conn.execute("DROP TABLE projects")
            conn.execute("ALTER TABLE projects_new RENAME TO projects")

        # Check if this is a store-style projects table
        # (has name/path instead of project_id/project_name)
        elif "name" in cols and "path" in cols and "project_id" not in cols:
            # Store-style schema: replace with core registry schema
            # Rename old table
            conn.execute("ALTER TABLE projects RENAME TO projects_old")
            # Create new table with correct schema
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT UNIQUE NOT NULL,
                    project_name TEXT NOT NULL,
                    origin_root_path TEXT NOT NULL,
                    last_known_root_path TEXT NOT NULL,
                    db_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL
                )
                """
            )
            # Note: Cannot migrate data from store-style to
            # registry style automatically because store-style
            # doesn't have project_id or db_path.
            # Old data is preserved in projects_old table for
            # manual recovery if needed.
            # Drop the old table since it's incompatible
            conn.execute("DROP TABLE projects_old")

        conn.commit()
    finally:
        conn.close()


def register_project(
    core_db: Path,
    project_id: str,
    project_name: str,
    origin_root_path: Path,
    last_known_root_path: Path,
    project_db_path: Path,
) -> None:
    """Register or update a project in the core registry.

    Args:
        core_db: Path to core database
        project_id: Unique project identifier
        project_name: Human-readable project name
        origin_root_path: Original project directory
        last_known_root_path: Current/most recent project directory
        project_db_path: Path to project database
    """
    conn = sqlite3.connect(str(core_db))
    try:
        now = datetime.now().isoformat()

        # Check if project already exists
        cur = conn.execute(
            "SELECT id FROM projects WHERE project_id = ?",
            (project_id,),
        )
        exists = cur.fetchone() is not None

        if exists:
            # Update existing project
            conn.execute(
                """
                UPDATE projects
                SET project_name = ?,
                    last_known_root_path = ?,
                    db_path = ?,
                    last_used_at = ?
                WHERE project_id = ?
                """,
                (
                    project_name,
                    str(last_known_root_path),
                    str(project_db_path),
                    now,
                    project_id,
                ),
            )
        else:
            # Insert new project
            conn.execute(
                """
                INSERT INTO projects (
                    project_id, project_name,
                    origin_root_path, last_known_root_path,
                    db_path, created_at, last_used_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    project_name,
                    str(origin_root_path),
                    str(last_known_root_path),
                    str(project_db_path),
                    now,
                    now,
                ),
            )

        conn.commit()
    finally:
        conn.close()


def update_project_location(
    core_db: Path,
    project_id: str,
    new_root: Path,
) -> None:
    """Update the last known location of a project.

    Args:
        core_db: Path to core database
        project_id: Project identifier
        new_root: New project root directory
    """
    conn = sqlite3.connect(str(core_db))
    try:
        now = datetime.now().isoformat()

        conn.execute(
            """
            UPDATE projects
            SET last_known_root_path = ?,
                last_used_at = ?
            WHERE project_id = ?
            """,
            (str(new_root), now, project_id),
        )

        conn.commit()
    finally:
        conn.close()


def discover_project_dbs(core_db: Path) -> list[dict]:
    """Discover all registered project databases from the core registry.

    Queries the core registry and returns a list of project DB metadata.
    Only includes projects with existing database files.

    Args:
        core_db: Path to core database

    Returns:
        List of dicts with keys: project_id, project_name, root_path, db_path
    """
    try:
        conn = sqlite3.connect(str(core_db))
        try:
            cur = conn.execute(
                """
                SELECT project_id, project_name, last_known_root_path,
                       db_path, last_used_at
                FROM projects
                ORDER BY project_name, last_known_root_path,
                         last_used_at DESC
                """
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        results = []
        for (project_id, project_name, root_path,
             db_path_str, _last_used_at) in rows:
            # Skip broken rows or non-existent DB files
            if not db_path_str:
                continue

            db_path = Path(db_path_str)
            if not db_path.exists():
                continue

            results.append(
                {
                    "project_id": project_id,
                    "project_name": project_name,
                    "root_path": root_path,
                    "db_path": db_path,
                }
            )

        return results

    except Exception:
        # If core DB can't be read, return empty list
        return []


def lookup_project_metadata(
    core_db: Path, project_db_path: Path
) -> dict | None:
    """Look up project metadata by database path.

    Args:
        core_db: Path to core database
        project_db_path: Path to project database

    Returns:
        Dict with keys: project_name, root_path
        None if not found in registry
    """
    try:
        conn = sqlite3.connect(str(core_db))
        try:
            cur = conn.execute(
                """
                SELECT project_name, last_known_root_path
                FROM projects
                WHERE db_path = ?
                ORDER BY last_used_at DESC
                LIMIT 1
                """,
                (str(project_db_path),),
            )
            row = cur.fetchone()
        finally:
            conn.close()

        if row:
            return {
                "project_name": row[0],
                "root_path": row[1],
            }
        return None

    except Exception:
        return None
