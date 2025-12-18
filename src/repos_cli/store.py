# RepOS™ — Multi-Panel REPL-Based Developer Command Environment
# Copyright (c) 2025
# TriFactoria (Andrew Blankfield)
#
# Licensed under the Business Source License 1.1 (BSL 1.1).
# You may use, modify, and redistribute this file under the terms of the BSL.
# On the Change Date (2029-01-01), this file will be licensed under
# the Apache License, Version 2.0.

"""
SQLite-backed storage implementation for RepOS.

Handles all database operations: aliases, events, settings, and projects.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

# Output capture limits for history safety
MAX_STDOUT_BYTES = 8_192
MAX_STDERR_BYTES = 8_192
MAX_TOTAL_BYTES = 16_384


class SQLiteStore:
    """SQLite implementation of RepoStore protocol."""

    def __init__(self, db_path: Path):
        """Initialize store with database path.

        Args:
            db_path: Path to SQLite database file (must have schema)

        Note:
            Store does NOT create schema. Schema must be created by
            db.ensure_schema() before constructing SQLiteStore.
        """
        self.db_path = db_path

        # Ensure parent directory exists
        db_path.parent.mkdir(parents=True, exist_ok=True)

    # ----------------------------------------------------------------
    # Alias operations
    # ----------------------------------------------------------------

    def add_alias(self, panel: str, name: str, command: str) -> None:
        """Add or update an alias in the database."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            now = datetime.now().isoformat()
            # Generate alias_key as panel (lowercase) + name
            alias_key = panel.lower() + name
            conn.execute(
                """
                INSERT OR REPLACE INTO aliases
                (panel, name, alias_key, command, created_at,
                 updated_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (panel, name, alias_key, command, now, now),
            )
            conn.commit()
        finally:
            conn.close()

    def find_alias(self, panel: str, name: str) -> str | None:
        """Find an alias and return its command, or None if not found."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            cur = conn.execute(
                """
                SELECT command FROM aliases
                WHERE panel = ? AND name = ? AND is_active = 1
                """,
                (panel, name),
            )
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def list_aliases(self, panel: str) -> list[dict[str, str]]:
        """List all active aliases for a panel, sorted by name."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            cur = conn.execute(
                """
                SELECT name, command FROM aliases
                WHERE panel = ? AND is_active = 1
                ORDER BY name
                """,
                (panel,),
            )
            rows = cur.fetchall()
            return [
                {"name": name, "command": command}
                for name, command in rows
            ]
        finally:
            conn.close()

    def remove_alias(self, panel: str, name: str) -> None:
        """Remove an alias from the database (hard delete)."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                "DELETE FROM aliases WHERE panel = ? AND name = ?",
                (panel, name),
            )
            conn.commit()
        finally:
            conn.close()

    # ----------------------------------------------------------------
    # Event recording
    # ----------------------------------------------------------------

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
        """Record an execution event with output capture/truncation.

        Returns:
            Tuple of (stdout_truncated, stderr_truncated,
                     stdout_bytes_total, stderr_bytes_total)
        """
        # Measure output sizes
        stdout_bytes = stdout.encode("utf-8") if stdout else b""
        stderr_bytes = stderr.encode("utf-8") if stderr else b""
        stdout_bytes_total = len(stdout_bytes)
        stderr_bytes_total = len(stderr_bytes)

        # Check total size first
        total_size = stdout_bytes_total + stderr_bytes_total
        stdout_truncated = False
        stderr_truncated = False

        if total_size > MAX_TOTAL_BYTES:
            # If total exceeds limit, prioritize stderr and truncate stdout
            remaining_for_stdout = MAX_TOTAL_BYTES - stderr_bytes_total
            if remaining_for_stdout < stdout_bytes_total:
                stdout_bytes = stdout_bytes[: max(0, remaining_for_stdout)]
                stdout = stdout_bytes.decode("utf-8", errors="replace")
                stdout_truncated = True
        else:
            # Apply individual truncation limits only if total is OK
            if stdout_bytes_total > MAX_STDOUT_BYTES:
                stdout_bytes = stdout_bytes[:MAX_STDOUT_BYTES]
                stdout = stdout_bytes.decode("utf-8", errors="replace")
                stdout_truncated = True

            if stderr_bytes_total > MAX_STDERR_BYTES:
                stderr_bytes = stderr_bytes[:MAX_STDERR_BYTES]
                stderr = stderr_bytes.decode("utf-8", errors="replace")
                stderr_truncated = True

        conn = sqlite3.connect(str(self.db_path))
        try:
            now = datetime.now().isoformat()
            if started_at is None:
                started_at = now

            conn.execute(
                """
                INSERT INTO events (
                    panel, raw_command, resolved_command, exit_code,
                    created_at, started_at, duration_ms, stdout, stderr,
                    stdout_bytes_total, stderr_bytes_total,
                    stdout_truncated, stderr_truncated
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    panel,
                    raw_command,
                    resolved_command,
                    exit_code,
                    now,
                    started_at,
                    duration_ms,
                    stdout,
                    stderr,
                    stdout_bytes_total,
                    stderr_bytes_total,
                    1 if stdout_truncated else 0,
                    1 if stderr_truncated else 0,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        return (
            stdout_truncated,
            stderr_truncated,
            stdout_bytes_total,
            stderr_bytes_total
        )

    # ----------------------------------------------------------------
    # History retrieval
    # ----------------------------------------------------------------

    def get_history(self, panel: str) -> list[dict[str, Any]]:
        """Get compact execution history for a panel, newest first."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            cur = conn.execute(
                """
                SELECT
                    id,
                    raw_command,
                    exit_code,
                    created_at,
                    stdout_bytes_total,
                    stderr_bytes_total,
                    stdout_truncated,
                    stderr_truncated
                FROM events
                WHERE panel = ?
                ORDER BY created_at DESC
                """,
                (panel,),
            )
            rows = cur.fetchall()

            return [
                {
                    "id": row[0],
                    "raw_command": row[1],
                    "exit_code": row[2],
                    "created_at": row[3],
                    "stdout_bytes_total": row[4] or 0,
                    "stderr_bytes_total": row[5] or 0,
                    "stdout_truncated": row[6] or 0,
                    "stderr_truncated": row[7] or 0,
                }
                for row in rows
            ]
        finally:
            conn.close()

    def get_history_detail(
        self,
        panel: str,
        index: int
    ) -> dict[str, Any] | None:
        """Get detailed execution history entry by 1-indexed position."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            cur = conn.execute(
                """
                SELECT
                    raw_command,
                    resolved_command,
                    exit_code,
                    created_at,
                    started_at,
                    duration_ms,
                    stdout,
                    stderr,
                    stdout_bytes_total,
                    stderr_bytes_total,
                    stdout_truncated,
                    stderr_truncated
                FROM events
                WHERE panel = ?
                ORDER BY created_at DESC
                """,
                (panel,),
            )
            rows = cur.fetchall()

            if index < 1 or index > len(rows):
                return None

            # Get the requested entry (1-indexed)
            row = rows[index - 1]

            return {
                "raw_command": row[0],
                "resolved_command": row[1],
                "exit_code": row[2],
                "created_at": row[3],
                "started_at": row[4],
                "duration_ms": row[5],
                "stdout": row[6] or "",
                "stderr": row[7] or "",
                "stdout_bytes_total": row[8] or 0,
                "stderr_bytes_total": row[9] or 0,
                "stdout_truncated": row[10] or 0,
                "stderr_truncated": row[11] or 0,
            }
        finally:
            conn.close()

    # ----------------------------------------------------------------
    # Settings operations
    # ----------------------------------------------------------------

    def get_setting(self, key: str, default: str) -> str:
        """Get a setting from persistent storage."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            cur = conn.execute(
                "SELECT value FROM settings WHERE key = ?",
                (key,),
            )
            row = cur.fetchone()
            return row[0] if row else default
        finally:
            conn.close()

    def set_setting(self, key: str, value: str) -> None:
        """Set a setting in persistent storage."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            now = datetime.now().isoformat()
            conn.execute(
                """
                INSERT OR REPLACE INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                """,
                (key, value, now),
            )
            conn.commit()
        finally:
            conn.close()
