# tests/test_store.py
"""
Tests for SQLite implementation of RepoStore Protocol.

IMPORTANT ARCHITECTURE RULE (enforced here):
- repos.db is the *only* entry point that creates/ensures schema.
- repos.store (SQLiteStore) must NOT create schema. It assumes schema exists and
  only performs CRUD against existing tables.

These tests intentionally fail if SQLiteStore "helpfully" creates tables.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from repos_cli import db as repos_db
from repos_cli.store import MAX_STDOUT_BYTES, MAX_TOTAL_BYTES, SQLiteStore


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def ensured_db(tmp_db: Path) -> Path:
    """
    Ensure schema via repos.db (the sole schema authority).
    """
    repos_db.ensure_schema(tmp_db)
    return tmp_db


@pytest.fixture
def store(ensured_db: Path) -> SQLiteStore:
    """
    Create a SQLiteStore that assumes schema already exists.
    """
    return SQLiteStore(ensured_db)


# ----------------------------------------------------------------
# Schema authority / initialization boundaries
# ----------------------------------------------------------------


def _tables(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def test_store_does_not_create_schema_on_init(tmp_db: Path) -> None:
    """
    SQLiteStore must NOT create tables on initialization.
    Only repos.db.ensure_schema may do that.
    """
    # Create store without ensuring schema
    _ = SQLiteStore(tmp_db)

    # A DB file may exist, but tables must NOT be created by store.
    tables = _tables(tmp_db)
    assert "aliases" not in tables
    assert "events" not in tables
    assert "settings" not in tables
    assert "projects" not in tables


def test_store_operations_fail_without_schema(tmp_db: Path) -> None:
    """
    Without repos.db.ensure_schema, store CRUD should fail loudly
    (prevents silent schema creation in store).
    """
    s = SQLiteStore(tmp_db)

    with pytest.raises(sqlite3.OperationalError):
        _ = s.list_aliases("G")


def test_db_is_schema_authority_and_store_works_after_ensure_schema(ensured_db: Path) -> None:
    """
    Sanity: repos.db.ensure_schema creates tables, and then store can operate.
    """
    tables = _tables(ensured_db)
    assert "aliases" in tables
    assert "events" in tables
    assert "settings" in tables

    s = SQLiteStore(ensured_db)
    assert s.list_aliases("G") == []


# ----------------------------------------------------------------
# Alias operations
# ----------------------------------------------------------------


def test_add_alias_creates_db_entry(store: SQLiteStore) -> None:
    """add_alias must persist alias to database."""
    store.add_alias("G", "gs", "git status")

    result = store.find_alias("G", "gs")
    assert result == "git status"


def test_find_alias_returns_none_when_missing(store: SQLiteStore) -> None:
    """find_alias must return None for non-existent alias."""
    assert store.find_alias("G", "missing") is None


def test_list_aliases_returns_empty_list_initially(store: SQLiteStore) -> None:
    """list_aliases must return [] for panel with no aliases."""
    assert store.list_aliases("G") == []


def test_list_aliases_returns_sorted_list(store: SQLiteStore) -> None:
    """list_aliases must return aliases sorted by name."""
    store.add_alias("G", "zz", "cmd3")
    store.add_alias("G", "aa", "cmd1")
    store.add_alias("G", "mm", "cmd2")

    aliases = store.list_aliases("G")

    assert len(aliases) == 3
    assert aliases[0]["name"] == "aa"
    assert aliases[1]["name"] == "mm"
    assert aliases[2]["name"] == "zz"


def test_remove_alias_deletes_entry(store: SQLiteStore) -> None:
    """remove_alias must delete alias from database."""
    store.add_alias("G", "gs", "git status")
    store.remove_alias("G", "gs")

    assert store.find_alias("G", "gs") is None


def test_remove_alias_is_idempotent(store: SQLiteStore) -> None:
    """Removing non-existent alias should not error."""
    store.remove_alias("G", "missing")  # Should not raise


# ----------------------------------------------------------------
# Event recording
# ----------------------------------------------------------------


def test_record_event_stores_execution_data(store: SQLiteStore) -> None:
    """record_event must persist execution details."""
    stdout_trunc, stderr_trunc, stdout_total, stderr_total = store.record_event(
        panel="G",
        raw_command="gs",
        resolved_command="git status",
        exit_code=0,
        stdout="On branch main\n",
        stderr="",
        started_at="2025-12-14T10:00:00",
        duration_ms=150,
    )

    assert stdout_trunc is False
    assert stderr_trunc is False
    assert stdout_total > 0
    assert stderr_total == 0


def test_record_event_truncates_large_stdout(store: SQLiteStore) -> None:
    """record_event must truncate stdout exceeding MAX_STDOUT_BYTES."""
    big_stdout = "A" * (MAX_STDOUT_BYTES + 1000)

    stdout_trunc, _, stdout_total, _ = store.record_event(
        panel="G",
        raw_command="big",
        resolved_command="big",
        exit_code=0,
        stdout=big_stdout,
        stderr="",
    )

    assert stdout_trunc is True
    assert stdout_total == len(big_stdout.encode("utf-8"))


def test_record_event_prioritizes_stderr_in_total_limit(store: SQLiteStore) -> None:
    """When total bytes exceed limit, stdout is truncated to preserve stderr."""
    stderr = "E" * (MAX_TOTAL_BYTES - 100)  # Nearly fills total limit
    stdout = "S" * 1000

    stdout_trunc, stderr_trunc, _, _ = store.record_event(
        panel="G",
        raw_command="test",
        resolved_command="test",
        exit_code=0,
        stdout=stdout,
        stderr=stderr,
    )

    assert stderr_trunc is False  # stderr preserved
    assert stdout_trunc is True  # stdout truncated further


# ----------------------------------------------------------------
# History retrieval
# ----------------------------------------------------------------


def test_get_history_returns_empty_list_initially(store: SQLiteStore) -> None:
    """get_history must return [] for panel with no events."""
    assert store.get_history("G") == []


def test_get_history_returns_events_newest_first(store: SQLiteStore) -> None:
    """get_history must return events in reverse chronological order."""
    store.record_event("G", "cmd1", "cmd1", 0, "", "")
    store.record_event("G", "cmd2", "cmd2", 0, "", "")
    store.record_event("G", "cmd3", "cmd3", 0, "", "")

    history = store.get_history("G")

    assert len(history) == 3
    assert history[0]["raw_command"] == "cmd3"
    assert history[1]["raw_command"] == "cmd2"
    assert history[2]["raw_command"] == "cmd1"


def test_get_history_detail_returns_none_for_invalid_index(store: SQLiteStore) -> None:
    """get_history_detail must return None for out-of-range index."""
    assert store.get_history_detail("G", 1) is None


def test_get_history_detail_returns_full_event_data(store: SQLiteStore) -> None:
    """get_history_detail must return complete event including stdout/stderr."""
    store.record_event(
        panel="G",
        raw_command="test",
        resolved_command="echo test",
        exit_code=0,
        stdout="test output",
        stderr="test error",
        started_at="2025-12-14T10:00:00",
        duration_ms=100,
    )

    detail = store.get_history_detail("G", 1)

    assert detail is not None
    assert detail["raw_command"] == "test"
    assert detail["resolved_command"] == "echo test"
    assert detail["exit_code"] == 0
    assert detail["stdout"] == "test output"
    assert detail["stderr"] == "test error"
    assert detail["duration_ms"] == 100


# ----------------------------------------------------------------
# Settings operations
# ----------------------------------------------------------------


def test_get_setting_returns_default_when_missing(store: SQLiteStore) -> None:
    """get_setting must return default for non-existent key."""
    assert store.get_setting("missing_key", "default_value") == "default_value"


def test_set_setting_persists_value(store: SQLiteStore) -> None:
    """set_setting must persist setting across store instances."""
    store.set_setting("test_key", "test_value")

    # Create new store instance (schema already exists)
    new_store = SQLiteStore(store.db_path)
    assert new_store.get_setting("test_key", "default") == "test_value"


def test_set_setting_updates_existing_value(store: SQLiteStore) -> None:
    """set_setting must update value for existing key."""
    store.set_setting("key", "value1")
    store.set_setting("key", "value2")

    assert store.get_setting("key", "default") == "value2"
