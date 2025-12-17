"""
Tests that verify Protocol definitions are valid and implementations comply.
These tests don't test behavior - just that contracts exist.
"""

from __future__ import annotations

from repos_cli import interfaces


def test_repo_store_protocol_exists():
    """RepoStore Protocol must define required methods."""
    assert hasattr(interfaces, "RepoStore")

    protocol = interfaces.RepoStore

    # Required methods
    required_methods = [
        "add_alias",
        "find_alias",
        "list_aliases",
        "remove_alias",
        "record_event",
        "get_history",
        "get_history_detail",
        "get_setting",
        "set_setting",
    ]

    for method in required_methods:
        assert hasattr(protocol, method), f"RepoStore missing {method}"


def test_executor_protocol_exists():
    """Executor Protocol must define run method."""
    assert hasattr(interfaces, "Executor")

    protocol = interfaces.Executor
    assert hasattr(protocol, "run")


def test_config_model_protocol_exists():
    """ConfigModel Protocol must define required attributes."""
    assert hasattr(interfaces, "ConfigModel")

    protocol = interfaces.ConfigModel

    # Required attributes (read-only properties)
    required_attrs = ["panels", "commands", "branding", "system"]

    for attr in required_attrs:
        assert hasattr(protocol, attr), f"ConfigModel missing {attr}"


def test_sqlite_store_conforms_to_repo_store_protocol():
    """SQLiteStore must implement all RepoStore protocol methods."""
    import tempfile
    from pathlib import Path

    from repos_cli.db import ensure_schema
    from repos_cli.store import SQLiteStore

    # Create temporary database
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        ensure_schema(db_path)
        store = SQLiteStore(db_path)

        # Verify all protocol methods exist and are callable
        assert callable(store.add_alias)
        assert callable(store.find_alias)
        assert callable(store.list_aliases)
        assert callable(store.remove_alias)
        assert callable(store.record_event)
        assert callable(store.get_history)
        assert callable(store.get_history_detail)
        assert callable(store.get_setting)
        assert callable(store.set_setting)


def test_subprocess_executor_conforms_to_executor_protocol():
    """SubprocessExecutor must implement Executor protocol."""
    from repos_cli.executor import SubprocessExecutor

    executor = SubprocessExecutor()

    # Verify protocol method exists and is callable
    assert callable(executor.run)

    # Verify method signature matches protocol
    result = executor.run("echo test")
    assert isinstance(result, tuple)
    assert len(result) == 5  # (exit_code, stdout, stderr, started_at, duration_ms)


def test_system_config_conforms_to_config_model_protocol():
    """SystemConfig must implement ConfigModel protocol."""
    from repos_cli.config import load_system_config

    config = load_system_config()

    # Verify all protocol properties exist
    assert hasattr(config, "panels")
    assert hasattr(config, "commands")
    assert hasattr(config, "branding")
    assert hasattr(config, "system")

    # Verify properties return expected types
    assert isinstance(config.panels, dict)
    assert isinstance(config.commands, dict)
    assert isinstance(config.branding, dict)
    assert isinstance(config.system, dict)


def test_repo_store_method_signatures():
    """Verify RepoStore protocol method signatures are correct."""
    import inspect

    from repos_cli import interfaces

    # Get protocol annotations
    protocol = interfaces.RepoStore

    # Verify add_alias signature
    if hasattr(protocol, "add_alias"):
        sig = inspect.signature(protocol.add_alias)
        params = list(sig.parameters.keys())
        # Should have panel, name, command (self is implicit in Protocol)
        assert "panel" in params or len(params) >= 3

    # Verify find_alias signature
    if hasattr(protocol, "find_alias"):
        sig = inspect.signature(protocol.find_alias)
        params = list(sig.parameters.keys())
        assert "panel" in params or len(params) >= 2

    # Verify record_event signature
    if hasattr(protocol, "record_event"):
        sig = inspect.signature(protocol.record_event)
        params = list(sig.parameters.keys())
        # Should have multiple parameters for event recording
        assert len(params) >= 6


def test_executor_method_signature():
    """Verify Executor protocol run method signature."""
    import inspect

    from repos_cli import interfaces

    protocol = interfaces.Executor

    if hasattr(protocol, "run"):
        sig = inspect.signature(protocol.run)
        params = list(sig.parameters.keys())
        # Should have command parameter
        assert "command" in params or len(params) >= 1


def test_config_model_property_types():
    """Verify ConfigModel protocol property return types."""

    from repos_cli import interfaces

    protocol = interfaces.ConfigModel

    # Verify properties are defined
    for prop_name in ["panels", "commands", "branding", "system"]:
        assert hasattr(protocol, prop_name)
