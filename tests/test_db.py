# tests/test_db.py
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from repos_cli import config, db


@pytest.fixture
def repos_data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "repos_data_home"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REPOS_DATA_HOME", str(data))
    return data


def _cols(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cur.fetchall()}
    finally:
        conn.close()


def _tables(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def test_ensure_schema_creates_required_tables(repos_data_home: Path) -> None:
    """
    db.ensure_schema must create required tables for a fresh DB.
    """
    data_root = config.get_data_root()
    core = config.core_db_path(data_root)

    db.ensure_schema(core)

    assert core.exists()
    tables = _tables(core)
    assert "aliases" in tables
    assert "events" in tables
    assert "settings" in tables
    assert "projects" in tables  # core registry


def test_ensure_schema_is_idempotent(repos_data_home: Path) -> None:
    """
    Running ensure_schema twice should not error and should preserve tables.
    """
    data_root = config.get_data_root()
    core = config.core_db_path(data_root)

    db.ensure_schema(core)
    db.ensure_schema(core)

    assert core.exists()
    assert "panel" in _cols(core, "events")


def test_migration_adds_expected_events_columns(repos_data_home: Path) -> None:
    """
    If events table exists in legacy form, ensure_schema must migrate it by
    adding required columns.
    """
    data_root = config.get_data_root()
    core = config.core_db_path(data_root)
    core.parent.mkdir(parents=True, exist_ok=True)

    # Create legacy minimal schema (missing newer columns)
    conn = sqlite3.connect(str(core))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                panel TEXT NOT NULL,
                raw_command TEXT NOT NULL,
                resolved_command TEXT NOT NULL,
                exit_code INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    db.ensure_schema(core)

    cols = _cols(core, "events")
    for c in (
        "started_at",
        "duration_ms",
        "stdout",
        "stderr",
        "stdout_bytes_total",
        "stderr_bytes_total",
        "stdout_truncated",
        "stderr_truncated",
    ):
        assert c in cols


def test_projects_migration_upgrades_legacy_table_missing_id(
    repos_data_home: Path, tmp_path: Path
) -> None:
    """
    Regression: older core.db may have a 'projects' table without the 'id' column.
    ensure_schema must migrate it so db.register_project can SELECT id.
    """
    data_root = config.get_data_root()
    core = config.core_db_path(data_root)
    core.parent.mkdir(parents=True, exist_ok=True)

    # Legacy projects table: no `id` column
    conn = sqlite3.connect(str(core))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
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
        conn.commit()
    finally:
        conn.close()

    # Must upgrade schema (including projects) and remain callable
    db.ensure_schema(core)

    cols = _cols(core, "projects")
    assert "id" in cols  # critical column used by register_project


def test_projects_migration_upgrades_store_style_projects_table(
    repos_data_home: Path, tmp_path: Path
) -> None:
    """
    Regression: some DBs may have the 'store-style' projects schema (name/path/etc).
    ensure_schema must migrate/replace it with the core registry schema.
    """
    data_root = config.get_data_root()
    core = config.core_db_path(data_root)
    core.parent.mkdir(parents=True, exist_ok=True)

    # Store-style projects table (wrong schema for core registry)
    conn = sqlite3.connect(str(core))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(path)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    db.ensure_schema(core)

    cols = _cols(core, "projects")
    required = {
        "id",
        "project_id",
        "project_name",
        "origin_root_path",
        "last_known_root_path",
        "db_path",
        "created_at",
        "last_used_at",
    }
    assert required.issubset(cols)


def test_register_project_inserts_or_updates_registry(
    repos_data_home: Path, tmp_path: Path
) -> None:
    """
    db.register_project must create/update a row in core projects registry.
    """
    data_root = config.get_data_root()
    core = config.core_db_path(data_root)
    db.ensure_schema(core)

    project_id = "6f2a9c1e"
    project_name = "rep-os"
    origin = tmp_path / "origin"
    origin.mkdir(parents=True, exist_ok=True)

    db_path = config.project_db_path(data_root, project_id)

    db.register_project(
        core_db=core,
        project_id=project_id,
        project_name=project_name,
        origin_root_path=origin,
        last_known_root_path=origin,
        project_db_path=db_path,
    )

    conn = sqlite3.connect(str(core))
    try:
        row = conn.execute(
            """
            SELECT
                project_id, project_name,
                origin_root_path, last_known_root_path,
                db_path
            FROM projects
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == project_id
        assert row[1] == project_name
        assert Path(row[2]) == origin
        assert Path(row[3]) == origin
        assert Path(row[4]) == db_path
    finally:
        conn.close()


def test_update_project_location_updates_last_known_root_path(
    repos_data_home: Path, tmp_path: Path
) -> None:
    """
    db.update_project_location must update last_known_root_path for project_id.
    """
    data_root = config.get_data_root()
    core = config.core_db_path(data_root)
    db.ensure_schema(core)

    project_id = "6f2a9c1e"
    origin = tmp_path / "origin"
    moved = tmp_path / "moved"
    origin.mkdir(parents=True, exist_ok=True)
    moved.mkdir(parents=True, exist_ok=True)

    db_path = config.project_db_path(data_root, project_id)

    db.register_project(
        core_db=core,
        project_id=project_id,
        project_name="rep-os",
        origin_root_path=origin,
        last_known_root_path=origin,
        project_db_path=db_path,
    )

    db.update_project_location(core_db=core, project_id=project_id, new_root=moved)

    conn = sqlite3.connect(str(core))
    try:
        row = conn.execute(
            "SELECT last_known_root_path FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        assert row is not None
        assert Path(row[0]) == moved
    finally:
        conn.close()
