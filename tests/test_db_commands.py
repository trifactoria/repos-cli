# RepOS™ — Multi-Panel REPL-Based Developer Command Environment
# Copyright (c) 2025
# TriFactoria (Andrew Blankfield)
#
# Licensed under the Business Source License 1.1 (BSL 1.1).
# You may use, modify, and redistribute this file under the terms of the BSL.
# On the Change Date (2029-01-01), this file will be licensed under
# the Apache License, Version 2.0.

"""
Tests for REP panel DB commands (DB, USE, WHERE, INFO).

Validates:
- DB command filters non-existent database files
- USE command updates active database labels correctly
- WHERE and INFO show correct project name/source/path
- Ambiguous USE <name> handling
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from repos_cli import db as db_module
from repos_cli.config import core_db_path, get_data_root
from repos_cli.kernel import Kernel
from repos_cli.store import SQLiteStore


class FakeExecutor:
    """Mock Executor for testing."""

    def run(self, command: str, cwd: str = None) -> tuple[int, str, str, str, int]:
        return (0, "output\n", "", "2025-12-17T10:00:00", 100)


class FakeConfig:
    """Mock ConfigModel for testing."""

    def __init__(self):
        self.panels = {
            "REP": {"entry": "REP", "name": "REP", "message": "Welcome to RepOS!"},
        }
        self.commands = {
            "help": {"triggers": ["?", "h"]},
            "base": {},
        }
        self.branding = {
            "REP": {"panel_color": "cyan", "caret_color": "pink"},
        }
        self.system = {"name": "RepOS", "entry_alias": "AA"}
        self.exit = {"entry": "ZZ", "message": "Love Ya - Bye!"}


@pytest.fixture
def temp_core_db(tmp_path, monkeypatch):
    """Create a temporary core database for testing."""
    # Mock get_data_root and core_db_path to return tmp_path
    def mock_get_data_root():
        return tmp_path

    def mock_core_db_path(data_root):
        return data_root / "repos" / "repos.db"

    monkeypatch.setattr("repos_cli.config.get_data_root", mock_get_data_root)
    monkeypatch.setattr("repos_cli.kernel.cfg_module.get_data_root", mock_get_data_root)
    monkeypatch.setattr("repos_cli.kernel.cfg_module.core_db_path", mock_core_db_path)

    core_path = tmp_path / "repos" / "repos.db"
    db_module.ensure_schema(core_path)

    return core_path


def test_db_filters_non_existent_db_files(temp_core_db, tmp_path):
    """DB command should only show databases that exist on disk."""
    core_path = temp_core_db

    # Register two projects in core registry
    # One with existing DB, one with missing DB
    existing_db = tmp_path / "repos" / "db" / "project1.db"
    existing_db.parent.mkdir(parents=True, exist_ok=True)
    db_module.ensure_schema(existing_db)

    missing_db = tmp_path / "repos" / "db" / "project2.db"

    # Register both projects
    db_module.register_project(
        core_path,
        "proj1",
        "Project 1",
        tmp_path / "proj1",
        tmp_path / "proj1",
        existing_db,
    )

    db_module.register_project(
        core_path,
        "proj2",
        "Project 2",
        tmp_path / "proj2",
        tmp_path / "proj2",
        missing_db,
    )

    # Create kernel with temp core DB
    store = SQLiteStore(core_path)
    executor = FakeExecutor()
    config = FakeConfig()
    kernel = Kernel(store=store, executor=executor, config=config)
    kernel.start()

    # Run DB command
    out = kernel.handle_command("DB")

    # Should show core and project1, but NOT project2
    assert "core" in out
    assert "Project 1" in out
    assert "Project 2" not in out


def test_use_updates_where_and_info_labels(temp_core_db, tmp_path):
    """USE command should update active database labels for WHERE and INFO."""
    core_path = temp_core_db

    # Register a project
    project_db = tmp_path / "repos" / "db" / "testproject.db"
    project_db.parent.mkdir(parents=True, exist_ok=True)
    db_module.ensure_schema(project_db)

    db_module.register_project(
        core_path,
        "test-proj",
        "TestProject",
        tmp_path / "testproject",
        tmp_path / "testproject",
        project_db,
    )

    # Create kernel
    store = SQLiteStore(core_path)
    executor = FakeExecutor()
    config = FakeConfig()
    kernel = Kernel(store=store, executor=executor, config=config)
    kernel.start()

    # Switch to project database using USE command
    kernel.handle_command("USE 2")  # ID 2 should be the project (ID 1 is core)

    # Check WHERE output
    where_out = kernel.handle_command("WHERE")
    assert "TestProject" in where_out
    assert str(tmp_path / "testproject") in where_out
    assert str(project_db) in where_out

    # Check INFO output
    info_out = kernel.handle_command("INFO")
    assert "TestProject" in info_out
    assert str(tmp_path / "testproject") in info_out
    assert str(project_db) in info_out


def test_startup_init_resolves_labels_from_registry(temp_core_db, tmp_path):
    """Startup should resolve project name/source from registry when initializing from path."""
    core_path = temp_core_db

    # Register a project
    project_db = tmp_path / "repos" / "db" / "myproject.db"
    project_db.parent.mkdir(parents=True, exist_ok=True)
    db_module.ensure_schema(project_db)

    db_module.register_project(
        core_path,
        "my-proj",
        "MyProject",
        tmp_path / "myproject",
        tmp_path / "myproject",
        project_db,
    )

    # Create kernel with project DB as active
    store = SQLiteStore(project_db)
    executor = FakeExecutor()
    config = FakeConfig()
    kernel = Kernel(store=store, executor=executor, config=config)

    # Kernel __post_init__ should have called _init_active_db_from_path
    # which should have looked up the project metadata
    assert kernel.active_db_name == "MyProject"
    assert kernel.active_db_source == str(tmp_path / "myproject")
    assert kernel.active_db_path == project_db


def test_ambiguous_use_name_fails_with_id_list(temp_core_db, tmp_path):
    """USE <name> should fail with ID list when multiple targets have same name."""
    core_path = temp_core_db

    # Register two projects with same name but different paths
    project1_db = tmp_path / "repos" / "db" / "project1.db"
    project1_db.parent.mkdir(parents=True, exist_ok=True)
    db_module.ensure_schema(project1_db)

    project2_db = tmp_path / "repos" / "db" / "project2.db"
    project2_db.parent.mkdir(parents=True, exist_ok=True)
    db_module.ensure_schema(project2_db)

    # Both projects have the same name "DuplicateName"
    db_module.register_project(
        core_path,
        "proj1",
        "DuplicateName",
        tmp_path / "proj1",
        tmp_path / "proj1",
        project1_db,
    )

    db_module.register_project(
        core_path,
        "proj2",
        "DuplicateName",
        tmp_path / "proj2",
        tmp_path / "proj2",
        project2_db,
    )

    # Create kernel
    store = SQLiteStore(core_path)
    executor = FakeExecutor()
    config = FakeConfig()
    kernel = Kernel(store=store, executor=executor, config=config)
    kernel.start()

    # Try to USE by name (should fail with ambiguity)
    out = kernel.handle_command("USE DuplicateName")

    assert "Ambiguous" in out or "ambiguous" in out.lower()
    assert "2" in out  # Should list ID 2
    assert "3" in out  # Should list ID 3


def test_use_by_id_works_when_name_is_ambiguous(temp_core_db, tmp_path):
    """USE <id> should work even when multiple targets have same name."""
    core_path = temp_core_db

    # Register two projects with same name
    project1_db = tmp_path / "repos" / "db" / "project1.db"
    project1_db.parent.mkdir(parents=True, exist_ok=True)
    db_module.ensure_schema(project1_db)

    project2_db = tmp_path / "repos" / "db" / "project2.db"
    project2_db.parent.mkdir(parents=True, exist_ok=True)
    db_module.ensure_schema(project2_db)

    db_module.register_project(
        core_path,
        "proj1",
        "SameName",
        tmp_path / "proj1",
        tmp_path / "proj1",
        project1_db,
    )

    db_module.register_project(
        core_path,
        "proj2",
        "SameName",
        tmp_path / "proj2",
        tmp_path / "proj2",
        project2_db,
    )

    # Create kernel
    store = SQLiteStore(core_path)
    executor = FakeExecutor()
    config = FakeConfig()
    kernel = Kernel(store=store, executor=executor, config=config)
    kernel.start()

    # USE by ID should work
    out = kernel.handle_command("USE 2")

    assert "Switched" in out or "switched" in out.lower()

    # Verify WHERE shows correct project
    where_out = kernel.handle_command("WHERE")
    assert "SameName" in where_out
    assert "proj1" in where_out


def test_db_omits_path_column(temp_core_db, tmp_path):
    """DB command should not display path column (only ID, ACTIVE, NAME, KEY, SOURCE)."""
    core_path = temp_core_db

    # Create kernel
    store = SQLiteStore(core_path)
    executor = FakeExecutor()
    config = FakeConfig()
    kernel = Kernel(store=store, executor=executor, config=config)
    kernel.start()

    # Run DB command
    out = kernel.handle_command("DB")

    # Should have headers including KEY
    assert "ID" in out
    assert "ACTIVE" in out
    assert "NAME" in out
    assert "KEY" in out
    assert "SOURCE" in out

    # Should NOT show "PATH" header (path is hidden in DB output)
    lines = out.split("\n")
    header_lines = [line for line in lines if "ID" in line and "NAME" in line]
    if header_lines:
        assert "PATH" not in header_lines[0]


def test_where_includes_path(temp_core_db, tmp_path):
    """WHERE command must include path line."""
    core_path = temp_core_db

    # Create kernel
    store = SQLiteStore(core_path)
    executor = FakeExecutor()
    config = FakeConfig()
    kernel = Kernel(store=store, executor=executor, config=config)
    kernel.start()

    # Run WHERE command
    out = kernel.handle_command("WHERE")

    # Should include path
    assert "Path:" in out or str(core_path) in out


def test_info_includes_path(temp_core_db, tmp_path):
    """INFO command must include path line."""
    core_path = temp_core_db

    # Create kernel
    store = SQLiteStore(core_path)
    executor = FakeExecutor()
    config = FakeConfig()
    kernel = Kernel(store=store, executor=executor, config=config)
    kernel.start()

    # Run INFO command
    out = kernel.handle_command("INFO")

    # Should include path
    assert "Path:" in out or str(core_path) in out


def test_unknown_db_fallback_shows_project_unknown(temp_core_db, tmp_path):
    """If project DB is not in registry, should show 'project/unknown' not 'project/project'."""
    core_path = temp_core_db

    # Create a project DB that's NOT registered in core
    unknown_db = tmp_path / "repos" / "db" / "unknown.db"
    unknown_db.parent.mkdir(parents=True, exist_ok=True)
    db_module.ensure_schema(unknown_db)

    # Create kernel with unregistered DB
    store = SQLiteStore(unknown_db)
    executor = FakeExecutor()
    config = FakeConfig()
    kernel = Kernel(store=store, executor=executor, config=config)

    # Should have fallen back to "project/unknown"
    assert kernel.active_db_name == "project"
    assert kernel.active_db_source == "unknown"


def test_slugify_and_make_project_db_filename(tmp_path):
    """Test slug generation and filename creation."""
    from repos_cli.config import slugify, make_project_db_filename

    # Test slugify with various inputs
    assert slugify("rep-os") == "rep-os"
    assert slugify("REP OS") == "rep-os"
    assert slugify("My Project!") == "my-project"
    assert slugify("Test___Project") == "test-project"
    assert slugify("   leading-trailing   ") == "leading-trailing"
    assert slugify("!!!") == "project"  # fallback to "project"
    assert slugify("") == "project"  # fallback to "project"

    # Test make_project_db_filename
    assert make_project_db_filename("rep-os", "b7881729") == "rep-os-b7881729.db"
    assert make_project_db_filename("My Project", "abc12345") == "my-project-abc12345.db"
    assert make_project_db_filename("Test Project", "deadbeef") == "test-project-deadbeef.db"


def test_new_project_uses_readable_filename(temp_core_db, tmp_path, monkeypatch):
    """New projects should create DB files with human-readable names."""
    from repos_cli import config as cfg_module

    core_path = temp_core_db

    # Create a new project DB path with project name
    project_id = "b7881729"
    project_name = "rep-os"

    project_db_path = cfg_module.project_db_path(tmp_path, project_id, project_name)

    # Should use new naming convention
    assert project_db_path.name == "rep-os-b7881729.db"
    assert project_db_path.parent == tmp_path / "repos" / "db"


def test_legacy_project_path_without_name(temp_core_db, tmp_path):
    """Legacy project paths (without name) should still work."""
    from repos_cli import config as cfg_module

    core_path = temp_core_db

    # Create a legacy project DB path without project name
    project_id = "b7881729"

    project_db_path = cfg_module.project_db_path(tmp_path, project_id, None)

    # Should use legacy naming convention
    assert project_db_path.name == "b7881729.db"
    assert project_db_path.parent == tmp_path / "repos" / "db"


def test_db_shows_key_column(temp_core_db, tmp_path):
    """DB command should include KEY column."""
    core_path = temp_core_db

    # Register a project
    project_db = tmp_path / "repos" / "db" / "testproject-abc12345.db"
    project_db.parent.mkdir(parents=True, exist_ok=True)
    db_module.ensure_schema(project_db)

    db_module.register_project(
        core_path,
        "abc12345",
        "TestProject",
        tmp_path / "testproject",
        tmp_path / "testproject",
        project_db,
    )

    # Create kernel
    store = SQLiteStore(core_path)
    executor = FakeExecutor()
    config = FakeConfig()
    kernel = Kernel(store=store, executor=executor, config=config)
    kernel.start()

    # Run DB command
    out = kernel.handle_command("DB")

    # Should have KEY column
    assert "KEY" in out
    # Should show project_id as key
    assert "abc12345" in out


def test_db_disambiguates_duplicates_with_key(temp_core_db, tmp_path):
    """DB command should help disambiguate duplicates with KEY column."""
    core_path = temp_core_db

    # Register two projects with same name but different IDs
    project1_db = tmp_path / "repos" / "db" / "myproject-11111111.db"
    project1_db.parent.mkdir(parents=True, exist_ok=True)
    db_module.ensure_schema(project1_db)

    project2_db = tmp_path / "repos" / "db" / "myproject-22222222.db"
    project2_db.parent.mkdir(parents=True, exist_ok=True)
    db_module.ensure_schema(project2_db)

    db_module.register_project(
        core_path,
        "11111111",
        "MyProject",
        tmp_path / "proj1",
        tmp_path / "proj1",
        project1_db,
    )

    db_module.register_project(
        core_path,
        "22222222",
        "MyProject",
        tmp_path / "proj2",
        tmp_path / "proj2",
        project2_db,
    )

    # Create kernel
    store = SQLiteStore(core_path)
    executor = FakeExecutor()
    config = FakeConfig()
    kernel = Kernel(store=store, executor=executor, config=config)
    kernel.start()

    # Run DB command
    out = kernel.handle_command("DB")

    # Should show both projects with different keys
    assert "MyProject" in out
    assert "11111111" in out
    assert "22222222" in out

    # Should be able to distinguish them by KEY
    lines = out.split("\n")
    myproject_lines = [line for line in lines if "MyProject" in line]
    assert len(myproject_lines) == 2  # Two entries with same name
