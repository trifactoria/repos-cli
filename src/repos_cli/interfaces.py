# RepOS™ — Multi-Panel REPL-Based Developer Command Environment
# Copyright (c) 2025
# TriFactoria (Andrew Blankfield)
#
# Licensed under the Business Source License 1.1 (BSL 1.1).
# You may use, modify, and redistribute this file under the terms of the BSL.
# On the Change Date (2029-01-01), this file will be licensed under
# the Apache License, Version 2.0.

"""
Protocol definitions for dependency injection.

These interfaces enable clean separation between kernel logic,
database operations, and command execution.
"""

from __future__ import annotations

from typing import Any, Protocol


class RepoStore(Protocol):
    """Protocol for persistent storage operations."""

    def add_alias(self, panel: str, name: str, command: str) -> None:
        """Add or update an alias in the database."""
        ...

    def find_alias(self, panel: str, name: str) -> str | None:
        """Find an alias and return its command, or None if not found."""
        ...

    def list_aliases(self, panel: str) -> list[dict[str, str]]:
        """List all active aliases for a panel, sorted by name."""
        ...

    def remove_alias(self, panel: str, name: str) -> None:
        """Remove an alias from the database."""
        ...

    def record_event(
        self,
        panel: str,
        raw_command: str,
        resolved_command: str,
        exit_code: int,
        stdout: str,
        stderr: str,
        started_at: str | None = None,
        duration_ms: int | None = None,
    ) -> tuple[bool, bool, int, int]:
        """Record an execution event with output capture.

        Returns:
            (stdout_truncated, stderr_truncated,
             stdout_bytes_total, stderr_bytes_total)
        """
        ...

    def get_history(self, panel: str) -> list[dict[str, Any]]:
        """Get compact execution history for a panel, newest first."""
        ...

    def get_history_detail(
        self, panel: str, index: int
    ) -> dict[str, Any] | None:
        """Get detailed execution history entry by 1-indexed position."""
        ...

    def get_setting(self, key: str, default: str) -> str:
        """Get a setting from persistent storage."""
        ...

    def set_setting(self, key: str, value: str) -> None:
        """Set a setting in persistent storage."""
        ...


class Executor(Protocol):
    """Protocol for command execution."""

    def run(self, command: str) -> tuple[int, str, str, str, int]:
        """Run a shell command and return results.

        Returns:
            (exit_code, stdout, stderr, started_at, duration_ms)
        """
        ...


class ConfigModel(Protocol):
    """Protocol for configuration access."""

    @property
    def panels(self) -> dict[str, dict[str, Any]]:
        """Panel configuration."""
        ...

    @property
    def commands(self) -> dict[str, Any]:
        """Command configuration."""
        ...

    @property
    def branding(self) -> dict[str, dict[str, str]]:
        """Branding configuration."""
        ...

    @property
    def system(self) -> dict[str, Any]:
        """System configuration."""
        ...
