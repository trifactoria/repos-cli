# RepOS™ — Multi-Panel REPL-Based Developer Command Environment
# Copyright (c) 2025
# TriFactoria (Andrew Blankfield)
#
# Licensed under the Business Source License 1.1 (BSL 1.1).
# You may use, modify, and redistribute this file under the terms of the BSL.
# On the Change Date (2029-01-01), this file will be licensed under
# the Apache License, Version 2.0.

"""
RepOS kernel.

Core implementation of RepOS:
- panel navigation
- alias execution + event logging
- history + rerun
- shell-backed multi-panel REPL engine

Important boundary:
- Kernel does not load YAML or discover defaults.
- Kernel consumes the injected ConfigModel.

Streaming / TTY upgrades:
- If executor supports run_stream(), kernel can stream stdout/stderr
  in real time via injected output callbacks (output_fn / error_fn).
- If executor supports run_pty(), shell passthrough commands starting
  with '!' can be run in a PTY mode for more "real terminal" behavior
  (foundation for future AI/automation).
"""

from __future__ import annotations

import os
import shlex
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config as cfg_module
from .config import ANSI_COLORS, TAG_COLORS, UI_CLEAR
from .interfaces import ConfigModel, Executor, RepoStore
from .store import MAX_STDERR_BYTES, MAX_STDOUT_BYTES, SQLiteStore
from .utils import (
    extract_kwargs_and_posargs,
    format_table,
    parse_alias_script,
    substitute_placeholders,
)


def write_crash_log(
    error: Exception,
    panel: str = "",
    raw_command: str = "",
    resolved_command: str = "",
    db_name: str = "",
    db_path: Path | None = None,
) -> None:
    """Write an entry to the crash log.

    Logs unhandled exceptions or critical failures.
    Only creates the log directory when actually needed.
    Appends to crash.log (never overwrites).
    """
    try:
        # Get data root
        data_root = cfg_module.get_data_root()
        logs_dir = data_root / "repos" / "logs"

        # Create logs directory only when we need to write
        logs_dir.mkdir(parents=True, exist_ok=True)

        crash_log_path = logs_dir / "crash.log"

        # Format crash log entry
        timestamp = datetime.now().isoformat()
        lines = [
            f"{timestamp}",
            f"panel={panel}",
        ]

        if raw_command:
            lines.append(f"raw={raw_command}")
        if resolved_command:
            lines.append(f"resolved={resolved_command}")
        if db_name:
            lines.append(f"db={db_name}")
        if db_path:
            lines.append(f"db_path={db_path}")

        lines.append(f"error={type(error).__name__}: {error}")
        lines.append("traceback:")
        lines.append(traceback.format_exc())
        lines.append("----")

        # Append to crash log
        with crash_log_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    except Exception:
        # If we can't write the crash log, fail silently
        # (we're already in an error state)
        pass


@dataclass
class Kernel:
    """RepOS session engine."""

    store: RepoStore
    executor: Executor
    config: ConfigModel

    history: list[str] = field(default_factory=list)
    running: bool = False

    # Current panel is the *entry* string (e.g. "REP", "G", "OS")
    panel: str = "REP"
    panel_stack: list[str] = field(default_factory=lambda: ["REP"])

    # Derived from config
    branding: dict[str, dict[str, str]] = field(default_factory=dict)
    base_commands: dict[str, Any] = field(default_factory=dict)
    command_triggers: dict[str, str] = field(default_factory=dict)
    documented_commands: list[str] = field(default_factory=list)

    # Derived maps
    _entry_to_panel: dict[str, tuple[str, dict[str, Any]]] = field(
        default_factory=dict
    )
    _panel_entries: set[str] = field(default_factory=set)

    _last_alias_by_panel: dict[str, str] = field(default_factory=dict)

    # Alias execution recursion tracking
    _alias_expansion_stack: list[str] = field(default_factory=list)
    _max_alias_depth: int = 10

    # Working directory tracking for shell_fallback panels
    cwd: str = field(default_factory=os.getcwd)
    prev_cwd: str | None = None

    # DB target tracking (for REP panel commands: DB, USE, WHERE)
    active_db_path: Path | None = None
    active_db_name: str = ""
    active_db_source: str = ""

    # Output settings
    show_run: bool = True
    show_exit: bool = True
    show_stdout: bool = True
    show_stderr: bool = True
    force_color: bool = True

    # Welcome setting
    welcome: bool = True

    # ---- Streaming hooks (wired by UI/CLI) ----
    # If set, kernel will stream process output through these while executing.
    output_fn: Callable[[str], None] | None = None
    error_fn: Callable[[str], None] | None = None

    def __post_init__(self) -> None:
        # Branding comes from config boundary
        self.branding = self.config.branding

        # Base commands / triggers come from config
        self.base_commands = self.config.commands.get("base", {})
        self.command_triggers = {}
        for action_name, cfg in self.base_commands.items():
            for trig in cfg.get("triggers", []) or []:
                self.command_triggers[trig] = action_name

        # Panel entry maps come from config (this is the
        # "no invented grammar" core)
        self._entry_to_panel = {}
        self._panel_entries = set()

        for panel_name, panel_cfg in self.config.panels.items():
            entry = panel_cfg.get("entry")
            if not entry:
                continue
            self._entry_to_panel[entry] = (panel_name, panel_cfg)
            self._panel_entries.add(entry)

        # Documented commands list (help display)
        self.documented_commands = []
        sys_cfg = getattr(self.config, "system", {}) or {}
        entry_alias = sys_cfg.get("entry_alias")
        if entry_alias:
            self.documented_commands.append(entry_alias)

        exit_cfg = (
            self.config.get("exit", {})
            if hasattr(self.config, "get")
            else {}
        )
        if isinstance(exit_cfg, dict) and exit_cfg.get("entry"):
            self.documented_commands.append(exit_cfg["entry"])

        self.documented_commands.append("Z")
        self.documented_commands.extend(sorted(self._panel_entries))

        # Start in root panel entry (config-defined)
        root_panel = sys_cfg.get("root_panel", "REP")
        self.panel = self.config.panels.get(
            root_panel, {}
        ).get("entry", root_panel)
        self.panel_stack = [self.panel]

        # Initialize active DB tracking from current store
        if hasattr(self.store, "db_path") and self.store.db_path:
            self._init_active_db_from_path(self.store.db_path)

    # -----------------------
    # UI helper hooks
    # -----------------------

    def get_reserved_triggers(self) -> set[str]:
        """Get all reserved command triggers.

        These triggers cannot be used as alias names.

        Returns:
            Set of reserved trigger strings (base commands +
                special builtins)
        """
        reserved = set()

        # Collect all base command triggers from config
        for _cmd_name, cmd_cfg in self.base_commands.items():
            triggers = cmd_cfg.get("triggers", []) or []
            reserved.update(triggers)

        # Collect help triggers
        help_cfg = self.config.commands.get("help", {})
        help_triggers = help_cfg.get("triggers", []) or []
        reserved.update(help_triggers)

        # Add special built-ins
        reserved.update({"Z", "ZZ", "cls"})  # Panel navigation and clear

        # Add REP panel commands (belt and suspenders)
        reserved.update({"DB", "USE", "WHERE", "INFO"})

        # Add the switch command (typically "REP")
        sys_cfg = getattr(self.config, "system", {}) or {}
        switch_cmd = sys_cfg.get("switch_command", "REP")
        reserved.add(switch_cmd)

        return reserved

    def list_alias_completions(self) -> list[dict[str, str]]:
        """Used by prompt_toolkit UI for completion menus.

        Returns:
            [{"key": "<alias>", "expanded": "<command>"}...]
        """
        items: list[dict[str, str]] = []
        try:
            aliases = self.store.list_aliases(self.panel)
        except Exception:
            return items

        for a in aliases or []:
            if isinstance(a, dict):
                key = str(a.get("name") or a.get("entry") or "").strip()
                expanded = str(a.get("command") or "").strip()
                if key:
                    items.append({"key": key, "expanded": expanded})
        return items

    def expand_alias(self, token: str) -> str | None:
        """Used by UI bottom toolbar to preview expansion."""
        if not token:
            return None
        try:
            cmd = self.store.find_alias(self.panel, token.strip())
        except Exception:
            return None
        return cmd

    def current_panel_has_shell_fallback(self) -> bool:
        """Check if the current panel has shell_fallback enabled."""
        if self.panel in self._entry_to_panel:
            _panel_name, panel_cfg = self._entry_to_panel[self.panel]
            return bool(panel_cfg.get("shell_fallback", False))
        return False

    # -----------------------
    # Session
    # -----------------------

    def start(self, include_prompt: bool = True) -> str:
        """Start a RepOS session."""
        self.running = True

        # Reset panel stack to root panel entry
        sys_cfg = getattr(self.config, "system", {}) or {}
        root_panel_key = sys_cfg.get("root_panel", "REP")
        root_entry = self.config.panels.get(
            root_panel_key, {}
        ).get("entry", root_panel_key)
        self.panel = root_entry
        self.panel_stack = [root_entry]

        # Load welcome flag from store
        welcome_value = self.store.get_setting("welcome", "true")
        self.welcome = (
            welcome_value.lower() in ["true", "1", "yes"]
        )

        out: list[str] = []

        if self.welcome:
            # Brand the word "RepOS" using REP colors (canonical wordmark)
            rep_branding = self.branding.get("REP", {})
            panel_color_name = rep_branding.get("panel_color", "cyan")
            caret_color_name = rep_branding.get("caret_color", "pink")

            panel_color = ANSI_COLORS.get(
                panel_color_name, ANSI_COLORS["cyan"]
            )
            caret_color = ANSI_COLORS.get(
                caret_color_name, ANSI_COLORS["pink"]
            )
            reset = ANSI_COLORS["reset"]
            branded_repos = f"{panel_color}Rep{caret_color}OS{reset}"

            # 1) system-level welcome (what RepOS is) —
            # with branding applied
            sys_welcome = (
                (sys_cfg.get("welcome") or {})
                if isinstance(sys_cfg, dict)
                else {}
            )
            if isinstance(sys_welcome, dict):
                msg = sys_welcome.get("message")
                if isinstance(msg, str) and msg.strip():
                    out.append(msg.strip().replace("RepOS", branded_repos))

        if include_prompt:
            out.append(f"{self.panel}>")

        return "\n\n".join([s for s in out if s])

    def prompt(self) -> str:
        """Return the current prompt string with ANSI colors."""
        panel_branding = self.branding.get(self.panel, {})
        panel_color_name = panel_branding.get("panel_color", "reset")
        caret_color_name = panel_branding.get("caret_color", "reset")

        panel_color = ANSI_COLORS.get(panel_color_name, ANSI_COLORS["reset"])
        caret_color = ANSI_COLORS.get(caret_color_name, ANSI_COLORS["reset"])
        reset = ANSI_COLORS["reset"]

        return f"{panel_color}{self.panel}{reset}{caret_color}>{reset}"

    # -----------------------
    # Command handling
    # -----------------------

    def handle_command(self, command: str) -> str:
        """Handle a single command line."""
        self.history.append(command)
        stripped = command.strip()

        # Clear screen behavior: config-driven triggers +
        # legacy compatibility
        clear_cfg = (
            self.base_commands.get("clear", {})
            if isinstance(self.base_commands, dict)
            else {}
        )
        clear_triggers = set(clear_cfg.get("triggers", []) or [])
        clear_triggers.update({"cls"})
        if stripped in clear_triggers or command == "\x0c":
            return UI_CLEAR

        # Raw shell commands
        if command.startswith("!"):
            raw_shell_cmd = command[1:]
            return self._execute_raw_shell(command, raw_shell_cmd)

        # Help triggers (skip in REP panel - REP has its own help)
        help_cfg = self.config.commands.get("help", {})
        help_triggers = help_cfg.get("triggers", [])
        if command in help_triggers and self.panel != "REP":
            return self._generate_help()

        # Exit entry (config-driven, default ZZ)
        exit_cfg = (
            self.config.get("exit", {})
            if hasattr(self.config, "get")
            else {}
        )
        exit_entry = "ZZ"
        if (isinstance(exit_cfg, dict) and
                isinstance(exit_cfg.get("entry"), str)):
            exit_entry = exit_cfg["entry"]

        if stripped == exit_entry:
            self.running = False
            if isinstance(exit_cfg, dict) and "message" in exit_cfg:
                return f"Exiting RepOS. {exit_cfg['message']}"
            return "Exiting RepOS."

        # Pop panel stack (“Z” is intentionally a stable built-in)
        if stripped == "Z":
            if len(self.panel_stack) > 1:
                self.panel_stack.pop()
            self.panel = self.panel_stack[-1]
            return ""

        # History (H or H <n>)
        if stripped == "H" or stripped.startswith("H "):
            parts_h = stripped.split(maxsplit=1)
            if len(parts_h) == 1:
                return self._handle_history()
            try:
                index = int(parts_h[1])
                return self._handle_history_detail(index)
            except ValueError:
                return "Usage: H [index]"

        # Use shlex.split to properly handle quoted arguments
        try:
            parts = shlex.split(stripped)
        except ValueError:
            # shlex can fail on unmatched quotes
            parts = stripped.split()

        if not parts:
            return "Unknown command"

        cmd = parts[0]

        # SET command
        if cmd == "SET":
            return self._handle_set_command(parts)

        # Switch command (config-driven, default REP)
        sys_cfg = getattr(self.config, "system", {}) or {}
        switch_cmd = sys_cfg.get("switch_command", "REP")

        # "REP" -> go to root (entry)
        if len(parts) == 1 and parts[0] == switch_cmd:
            root_key = sys_cfg.get("root_panel", "REP")
            root_entry = self.config.panels.get(
                root_key, {}
            ).get("entry", root_key)
            self.panel = root_entry
            self.panel_stack = [root_entry]
            return ""

        # "REP X" -> switch to panel by entry token
        if len(parts) == 2 and parts[0] == switch_cmd:
            entry = parts[1]
            if entry in self._entry_to_panel:
                _panel_name, panel_cfg = self._entry_to_panel[entry]
                self.panel_stack.append(entry)
                self.panel = entry
                msg = panel_cfg.get("message", "")
                return msg if isinstance(msg, str) else ""
            return ""

        # REP panel special handling (no aliases, only REP commands)
        if self.panel == "REP":
            return self._handle_rep_command(cmd, parts, stripped)

        # Base command triggers via mapping (CHECK BEFORE ALIASES)
        action = self.command_triggers.get(cmd)

        if action == "list" and len(parts) == 1:
            return self._handle_list_aliases()

        if action == "add" and len(parts) > 1:
            return self._handle_new_alias(parts, stripped)

        if action == "remove":
            return self._handle_remove_alias(parts)

        if action == "rerun" and len(parts) == 1:
            return self._handle_rerun_alias()

        # Alias lookup with arguments
        alias_name = parts[0]
        alias_cmd = self.store.find_alias(self.panel, alias_name)
        if alias_cmd is not None:
            # Parse the invocation to extract kwargs and posargs
            invocation_args = parts[1:]  # Everything after alias name
            self._last_alias_by_panel[self.panel] = alias_name
            return self._execute_alias_with_args(
                alias_name, alias_cmd, invocation_args, stripped
            )

        # Bare panel switching: typing the entry token
        if stripped in self._panel_entries:
            self.panel_stack.append(stripped)
            self.panel = stripped
            return ""

        # Builtins for shell_fallback panels (cd, pwd)
        if self.current_panel_has_shell_fallback():
            if cmd == "cd":
                return self._handle_cd(parts)
            if cmd == "pwd":
                return self._handle_pwd()

        # Shell fallback if enabled for current panel
        if self.panel in self._entry_to_panel:
            _panel_name, panel_cfg = self._entry_to_panel[self.panel]
            if panel_cfg.get("shell_fallback", False):
                return self._execute_raw_shell(command, command)

        return "Unknown command"

    # -----------------------
    # Execution + formatting
    # -----------------------

    def _format_truncation_warning(
        self,
        stdout_truncated: bool,
        stderr_truncated: bool,
        stdout_bytes_total: int,
        stderr_bytes_total: int,
    ) -> str:
        hist_color = ANSI_COLORS[TAG_COLORS["HIST"]]
        yellow = ANSI_COLORS["yellow"]
        dim = ANSI_COLORS["dim"]
        reset = ANSI_COLORS["reset"]

        parts: list[str] = []
        if stdout_truncated:
            parts.append(
                f"stdout {stdout_bytes_total:,}B {dim}→{reset} "
                f"{MAX_STDOUT_BYTES:,}B"
            )
        if stderr_truncated:
            parts.append(
                f"stderr {stderr_bytes_total:,}B {dim}→{reset} "
                f"{MAX_STDERR_BYTES:,}B"
            )

        detail = ", ".join(parts)
        return (
            f"{hist_color}[HIST]{reset} {yellow}⚠{reset} "
            f"output not fully captured ({detail})"
        )

    def _can_stream(self) -> bool:
        return (
            hasattr(self.executor, "run_stream")
            and callable(self.executor.run_stream)
            and (self.output_fn is not None or self.error_fn is not None)
        )

    def _can_pty(self) -> bool:
        return (
            hasattr(self.executor, "run_pty")
            and callable(self.executor.run_pty)
            and (self.output_fn is not None)
        )

    def _can_tty(self) -> bool:
        """Check if executor supports TTY passthrough mode."""
        return (
            hasattr(self.executor, "run_tty") and
            callable(self.executor.run_tty)
        )

    def _should_use_tty(
        self, resolved_command: str, raw_command: str = ""
    ) -> bool:
        """Decide if a command should use TTY passthrough.

        Based on YAML config.

        Args:
            resolved_command: The actual command to execute
            raw_command: The raw command entered by user
                (for force_prefix detection)

        Returns:
            True if command should use TTY mode, False for
                capture mode
        """
        # Get TTY apps config from YAML
        exec_cfg = getattr(self.config, "execution", {}) or {}
        tty_apps = (
            exec_cfg.get("tty_apps", {})
            if isinstance(exec_cfg, dict)
            else {}
        )

        # If disabled or missing, never use TTY
        if (not isinstance(tty_apps, dict) or
                not tty_apps.get("enabled", False)):
            return False

        # Check force_prefix for raw shell commands (e.g., "!tty ls")
        force_prefix = tty_apps.get("force_prefix", "!tty ")
        if force_prefix and raw_command.startswith(force_prefix):
            return True

        # Extract first token (argv0) from resolved command
        resolved = resolved_command.strip()
        if not resolved:
            return False

        argv0 = resolved.split()[0] if resolved.split() else ""

        # Check argv0 matches
        argv0_list = tty_apps.get("argv0", [])
        if isinstance(argv0_list, list) and argv0 in argv0_list:
            return True

        # Check prefix matches
        prefixes = tty_apps.get("prefixes", [])
        if isinstance(prefixes, list):
            for prefix in prefixes:
                if isinstance(prefix, str) and resolved.startswith(prefix):
                    return True

        # Check substring matches
        contains = tty_apps.get("contains", [])
        if isinstance(contains, list):
            for substring in contains:
                if isinstance(substring, str) and substring in resolved:
                    return True

        return False

    def _execute_alias_with_args(
        self, alias_name: str, alias_script: str,
        invocation_args: list[str], raw_command: str
    ) -> str:
        """Execute an alias with argument parsing and chaining support.

        Args:
            alias_name: Name of the alias being invoked
            alias_script: The alias body script
            invocation_args: Arguments from the invocation (already tokenized)
            raw_command: The full raw command string for history

        Returns:
            Output string
        """
        # Check recursion depth
        if len(self._alias_expansion_stack) >= self._max_alias_depth:
            stack_str = ' -> '.join(self._alias_expansion_stack)
            return (
                f"Error: Max alias expansion depth "
                f"({self._max_alias_depth}) exceeded. Stack: {stack_str}"
            )

        # Check for cycles
        if alias_name in self._alias_expansion_stack:
            cycle_chain = " -> ".join(
                self._alias_expansion_stack + [alias_name]
            )
            return (
                f"Error: Alias expansion cycle detected: {cycle_chain}"
            )

        # Push to stack
        self._alias_expansion_stack.append(alias_name)

        try:
            # Execute the alias script with args
            return self._execute_alias_script(
                alias_name, alias_script, invocation_args, raw_command
            )
        finally:
            # Pop from stack
            self._alias_expansion_stack.pop()

    def _execute_alias_script(
        self, alias_name: str, alias_script: str,
        invocation_args: list[str], raw_command: str
    ) -> str:
        """Execute an alias script with kwargs/posargs and chaining support.

        Args:
            alias_name: Name of the alias
            alias_script: The alias body script
            invocation_args: Arguments from invocation
            raw_command: Full raw command for history

        Returns:
            Output string
        """
        # Extract kwargs and posargs from invocation
        kwargs, posargs = extract_kwargs_and_posargs(invocation_args)

        # Substitute placeholders in the script
        try:
            rendered_script, _errors = substitute_placeholders(
                alias_script, kwargs
            )
        except ValueError as e:
            return f"Error: {e}"

        # Parse the script for alias chaining
        segments = parse_alias_script(rendered_script)

        # If no segments, nothing to execute
        if not segments:
            return ""

        # Execute segments sequentially
        outputs: list[str] = []

        for segment in segments:
            if segment.type == "literal":
                # Execute literal shell script with posargs
                output = self._execute_script_segment(
                    segment.content, posargs, raw_command,
                    rendered_script
                )
                outputs.append(output)
                # TODO: track exit code if needed
            elif segment.type == "alias":
                # Recursively execute alias
                chained_alias_name = segment.content
                chained_args = segment.args

                # Look up the alias
                chained_script = self.store.find_alias(
                    self.panel, chained_alias_name
                )
                if chained_script is None:
                    outputs.append(
                        f"Error: Alias '@{chained_alias_name}' not found"
                    )
                    continue

                # Recursively execute
                chained_output = self._execute_alias_with_args(
                    chained_alias_name,
                    chained_script,
                    chained_args,
                    f"@{chained_alias_name} "
                    f"{' '.join(chained_args)}".strip(),
                )
                outputs.append(chained_output)

        return "\n".join([o for o in outputs if o])

    def _execute_script_segment(
        self, script: str, posargs: list[str], raw_command: str,
        full_resolved: str
    ) -> str:
        """Execute a single script segment with positional arguments.

        Args:
            script: Shell script to execute
            posargs: Positional arguments
            raw_command: Raw command for history
            full_resolved: Full resolved command for display

        Returns:
            Output string
        """
        # Use argv-based execution if we have an argv method and posargs
        has_argv_method = (
            hasattr(self.executor, "run_argv") or
            hasattr(self.executor, "run_argv_stream")
        )

        # Prefer argv-based execution for positional arg support
        if has_argv_method and (posargs or "$" in script):
            # Use argv-based execution
            if (self._can_stream() and
                    hasattr(self.executor, "run_argv_stream")):
                return self._execute_script_argv_streaming(
                    script, posargs, raw_command, full_resolved
                )
            elif hasattr(self.executor, "run_argv"):
                return self._execute_script_argv(
                    script, posargs, raw_command, full_resolved
                )

        # Fallback to old method (for backward compat if executor
        # doesn't have argv)
        return self._execute_alias(raw_command, script)

    def _execute_script_argv(
        self, script: str, posargs: list[str], raw_command: str,
        full_resolved: str
    ) -> str:
        """Execute script using argv-based execution (buffered)."""
        exit_code, stdout, stderr, started_at, duration_ms = (
            self.executor.run_argv(script, posargs)
        )

        record_event_failed = False
        try:
            (stdout_truncated, stderr_truncated,
             stdout_bytes_total, stderr_bytes_total) = (
                self.store.record_event(
                    self.panel,
                    raw_command,
                    full_resolved,
                    exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    started_at=started_at,
                    duration_ms=duration_ms,
                )
            )
        except Exception as e:
            write_crash_log(
                e,
                panel=self.panel,
                raw_command=raw_command,
                resolved_command=full_resolved,
                db_name=self.active_db_name,
                db_path=self.active_db_path,
            )
            (stdout_truncated, stderr_truncated,
             stdout_bytes_total, stderr_bytes_total) = (
                False,
                False,
                0,
                0,
            )
            record_event_failed = True

        lines: list[str] = []
        run_color = ANSI_COLORS[TAG_COLORS["RUN"]]
        exit_color = ANSI_COLORS[TAG_COLORS["EXIT"]]
        err_color = ANSI_COLORS[TAG_COLORS["ERR"]]
        reset = ANSI_COLORS["reset"]

        if record_event_failed:
            lines.append(
                f"{err_color}[ERROR]{reset} "
                f"failed to record event to database"
            )

        if self.show_run:
            if self.show_stdout:
                lines.append(
                    f"{run_color}[RUN]{reset} {raw_command} => "
                    f"{full_resolved}"
                )
            else:
                lines.append(f"{run_color}[RUN]{reset} {raw_command}")

        if self.show_exit:
            lines.append(f"{exit_color}[EXIT]{reset} {exit_code}")

        if self.show_stdout and stdout:
            lines.append(stdout.rstrip())

        if self.show_stderr and stderr:
            lines.append(f"{err_color}[ERR]{reset}")
            lines.append(stderr.rstrip())

        if stdout_truncated or stderr_truncated:
            lines.append(
                self._format_truncation_warning(
                    stdout_truncated,
                    stderr_truncated,
                    stdout_bytes_total,
                    stderr_bytes_total,
                )
            )

        return "\n".join(lines)

    def _execute_script_argv_streaming(
        self, script: str, posargs: list[str], raw_command: str,
        full_resolved: str
    ) -> str:
        """Execute script using argv-based execution (streaming)."""
        run_color = ANSI_COLORS[TAG_COLORS["RUN"]]
        exit_color = ANSI_COLORS[TAG_COLORS["EXIT"]]
        reset = ANSI_COLORS["reset"]

        def _out(s: str) -> None:
            if self.output_fn:
                self.output_fn(s)

        def _err(s: str) -> None:
            if self.error_fn:
                self.error_fn(s)
            elif self.output_fn:
                self.output_fn(s)

        if self.show_run and self.output_fn:
            if self.show_stdout:
                self.output_fn(
                    f"{run_color}[RUN]{reset} {raw_command} => "
                    f"{full_resolved}\n"
                )
            else:
                self.output_fn(f"{run_color}[RUN]{reset} {raw_command}\n")

        result = self.executor.run_argv_stream(
            script, posargs, on_stdout=_out, on_stderr=_err
        )

        record_event_failed = False
        try:
            (stdout_truncated, stderr_truncated,
             stdout_bytes_total, stderr_bytes_total) = (
                self.store.record_event(
                    self.panel,
                    raw_command,
                    full_resolved,
                    result.exit_code,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    started_at=result.started_at,
                    duration_ms=result.duration_ms,
                )
            )
        except Exception as e:
            write_crash_log(
                e,
                panel=self.panel,
                raw_command=raw_command,
                resolved_command=full_resolved,
                db_name=self.active_db_name,
                db_path=self.active_db_path,
            )
            (stdout_truncated, stderr_truncated,
             stdout_bytes_total, stderr_bytes_total) = (
                False,
                False,
                0,
                0,
            )
            record_event_failed = True

        lines: list[str] = []

        if record_event_failed:
            err_color = ANSI_COLORS[TAG_COLORS["ERR"]]
            reset = ANSI_COLORS["reset"]
            lines.append(
                f"{err_color}[ERROR]{reset} "
                f"failed to record event to database"
            )

        if self.show_exit:
            lines.append(f"{exit_color}[EXIT]{reset} {result.exit_code}")

        if stdout_truncated or stderr_truncated:
            lines.append(
                self._format_truncation_warning(
                    stdout_truncated,
                    stderr_truncated,
                    stdout_bytes_total,
                    stderr_bytes_total,
                )
            )

        return "\n".join(lines)

    def _execute_alias(self, raw_command: str, resolved_command: str) -> str:
        # Use TTY mode only if command matches YAML config patterns
        if (self._can_tty() and
                self._should_use_tty(resolved_command, raw_command)):
            return self._execute_alias_tty(raw_command, resolved_command)

        # Prefer streaming if wired (default: capture output)
        if self._can_stream():
            return self._execute_alias_streaming(raw_command, resolved_command)

        # Fallback: buffered (legacy behavior)
        exit_code, stdout, stderr, started_at, duration_ms = (
            self.executor.run(resolved_command)
        )

        record_event_failed = False
        try:
            (stdout_truncated, stderr_truncated,
             stdout_bytes_total, stderr_bytes_total) = (
                self.store.record_event(
                    self.panel,
                    raw_command,
                    resolved_command,
                    exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    started_at=started_at,
                    duration_ms=duration_ms,
                )
            )
        except Exception as e:
            write_crash_log(
                e,
                panel=self.panel,
                raw_command=raw_command,
                resolved_command=resolved_command,
                db_name=self.active_db_name,
                db_path=self.active_db_path,
            )
            # Set defaults so we can continue
            (stdout_truncated, stderr_truncated,
             stdout_bytes_total, stderr_bytes_total) = (
                False,
                False,
                0,
                0,
            )
            record_event_failed = True

        lines: list[str] = []
        run_color = ANSI_COLORS[TAG_COLORS["RUN"]]
        exit_color = ANSI_COLORS[TAG_COLORS["EXIT"]]
        err_color = ANSI_COLORS[TAG_COLORS["ERR"]]
        reset = ANSI_COLORS["reset"]

        if record_event_failed:
            lines.append(
                f"{err_color}[ERROR]{reset} "
                f"failed to record event to database"
            )

        if self.show_run:
            if self.show_stdout:
                lines.append(
                    f"{run_color}[RUN]{reset} {raw_command} => "
                    f"{resolved_command}"
                )
            else:
                lines.append(f"{run_color}[RUN]{reset} {raw_command}")

        if self.show_exit:
            lines.append(f"{exit_color}[EXIT]{reset} {exit_code}")

        if self.show_stdout and stdout:
            lines.append(stdout.rstrip())

        if self.show_stderr and stderr:
            lines.append(f"{err_color}[ERR]{reset}")
            lines.append(stderr.rstrip())

        if stdout_truncated or stderr_truncated:
            lines.append(
                self._format_truncation_warning(
                    stdout_truncated,
                    stderr_truncated,
                    stdout_bytes_total,
                    stderr_bytes_total,
                )
            )

        return "\n".join(lines)

    def _execute_alias_streaming(
        self, raw_command: str, resolved_command: str
    ) -> str:
        run_color = ANSI_COLORS[TAG_COLORS["RUN"]]
        exit_color = ANSI_COLORS[TAG_COLORS["EXIT"]]
        reset = ANSI_COLORS["reset"]

        # Stream process output live through callbacks, but return only
        # RUN/EXIT summary
        def _out(s: str) -> None:
            if self.output_fn:
                self.output_fn(s)

        def _err(s: str) -> None:
            if self.error_fn:
                self.error_fn(s)
            elif self.output_fn:
                # If no dedicated stderr sink, send to main output sink.
                self.output_fn(s)

        if self.show_run and self.output_fn:
            if self.show_stdout:
                self.output_fn(
                    f"{run_color}[RUN]{reset} {raw_command} => "
                    f"{resolved_command}\n"
                )
            else:
                self.output_fn(f"{run_color}[RUN]{reset} {raw_command}\n")

        # Execute (streaming)
        result = self.executor.run_stream(
            resolved_command, on_stdout=_out, on_stderr=_err
        )

        record_event_failed = False
        try:
            (stdout_truncated, stderr_truncated,
             stdout_bytes_total, stderr_bytes_total) = (
                self.store.record_event(
                    self.panel,
                    raw_command,
                    resolved_command,
                    result.exit_code,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    started_at=result.started_at,
                    duration_ms=result.duration_ms,
                )
            )
        except Exception as e:
            write_crash_log(
                e,
                panel=self.panel,
                raw_command=raw_command,
                resolved_command=resolved_command,
                db_name=self.active_db_name,
                db_path=self.active_db_path,
            )
            (stdout_truncated, stderr_truncated,
             stdout_bytes_total, stderr_bytes_total) = (
                False,
                False,
                0,
                0,
            )
            record_event_failed = True

        # Return only EXIT (and trunc warning) to avoid double printing
        # stdout/stderr
        lines: list[str] = []

        if record_event_failed:
            err_color = ANSI_COLORS[TAG_COLORS["ERR"]]
            reset = ANSI_COLORS["reset"]
            lines.append(
                f"{err_color}[ERROR]{reset} "
                f"failed to record event to database"
            )

        if self.show_exit:
            lines.append(f"{exit_color}[EXIT]{reset} {result.exit_code}")

        if stdout_truncated or stderr_truncated:
            lines.append(
                self._format_truncation_warning(
                    stdout_truncated,
                    stderr_truncated,
                    stdout_bytes_total,
                    stderr_bytes_total,
                )
            )

        return "\n".join(lines)

    def _execute_alias_tty(
        self, raw_command: str, resolved_command: str
    ) -> str:
        """Execute an alias with full TTY control.

        For pagers and interactive tools.
        """
        run_color = ANSI_COLORS[TAG_COLORS["RUN"]]
        exit_color = ANSI_COLORS[TAG_COLORS["EXIT"]]
        reset = ANSI_COLORS["reset"]

        # Print RUN tag before handing off to TTY
        lines: list[str] = []
        if self.show_run:
            if self.show_stdout:
                run_msg = (
                    f"{run_color}[RUN]{reset} {raw_command} => "
                    f"{resolved_command}"
                )
            else:
                run_msg = f"{run_color}[RUN]{reset} {raw_command}"
            lines.append(run_msg)
            # Print immediately before TTY handoff
            if self.output_fn:
                self.output_fn(run_msg + "\n")

        # Signal UI to prepare for TTY handoff (print newline to
        # separate from prompt)
        if self.output_fn and hasattr(self.output_fn, "__self__"):
            ui = self.output_fn.__self__
            if hasattr(ui, "prepare_tty_handoff"):
                ui.prepare_tty_handoff()

        # Execute with full terminal control
        result = self.executor.run_tty(resolved_command)

        # After TTY program exits, record history (with empty stdout/stderr)
        record_event_failed = False
        try:
            self.store.record_event(
                self.panel,
                raw_command,
                resolved_command,
                result.exit_code,
                stdout="",  # No output captured in TTY mode
                stderr="",
                started_at=result.started_at,
                duration_ms=result.duration_ms,
            )
        except Exception as e:
            write_crash_log(
                e,
                panel=self.panel,
                raw_command=raw_command,
                resolved_command=resolved_command,
                db_name=self.active_db_name,
                db_path=self.active_db_path,
            )
            record_event_failed = True

        # Signal UI to restore after TTY handoff
        if self.output_fn and hasattr(self.output_fn, "__self__"):
            ui = self.output_fn.__self__
            if hasattr(ui, "restore_after_tty"):
                ui.restore_after_tty()

        # Return only EXIT tag (no output since TTY mode printed
        # directly to terminal)
        exit_lines: list[str] = []

        if record_event_failed:
            err_color = ANSI_COLORS[TAG_COLORS["ERR"]]
            reset = ANSI_COLORS["reset"]
            exit_lines.append(
                f"{err_color}[ERROR]{reset} "
                f"failed to record event to database"
            )

        if self.show_exit:
            exit_lines.append(f"{exit_color}[EXIT]{reset} {result.exit_code}")

        return "\n".join(exit_lines)

    def _execute_raw_shell(
        self, raw_command: str, resolved_command: str
    ) -> str:
        # Check if force_prefix is used (e.g., "!tty ls") and strip it
        exec_cfg = getattr(self.config, "execution", {}) or {}
        tty_apps = (
            exec_cfg.get("tty_apps", {})
            if isinstance(exec_cfg, dict)
            else {}
        )
        force_prefix = (
            tty_apps.get("force_prefix", "!tty ")
            if isinstance(tty_apps, dict)
            else "!tty "
        )

        # If raw command starts with force_prefix, strip it from
        # resolved command
        actual_resolved = resolved_command
        if force_prefix and raw_command.startswith(force_prefix):
            # Strip the force_prefix from the raw command to get clean command
            # raw_command is "!tty ls", resolved is "ls" (! already stripped)
            # force_prefix is "!tty ", so we need to strip "tty " from resolved
            prefix_without_bang = force_prefix.lstrip("!")
            if actual_resolved.startswith(prefix_without_bang):
                actual_resolved = actual_resolved[
                    len(prefix_without_bang):
                ].lstrip()

        # Use TTY mode only if command matches YAML config patterns
        if (self._can_tty() and
                self._should_use_tty(actual_resolved, raw_command)):
            return self._execute_raw_shell_tty(raw_command, actual_resolved)

        # Else prefer PTY (captures output but still provides PTY)
        if self._can_pty():
            return self._execute_raw_shell_pty(
                raw_command, actual_resolved
            )

        # Else prefer streaming
        if self._can_stream():
            return self._execute_raw_shell_streaming(
                raw_command, actual_resolved
            )

        # Fallback: buffered (legacy behavior)
        # Pass cwd if shell_fallback is enabled
        cwd = (
            self.cwd if self.current_panel_has_shell_fallback() else None
        )
        exit_code, stdout, stderr, started_at, duration_ms = self.executor.run(
            actual_resolved, cwd=cwd
        )

        record_event_failed = False
        try:
            (stdout_truncated, stderr_truncated,
             stdout_bytes_total, stderr_bytes_total) = (
                self.store.record_event(
                    self.panel,
                    raw_command,
                    resolved_command,
                    exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    started_at=started_at,
                    duration_ms=duration_ms,
                )
            )
        except Exception as e:
            write_crash_log(
                e,
                panel=self.panel,
                raw_command=raw_command,
                resolved_command=resolved_command,
                db_name=self.active_db_name,
                db_path=self.active_db_path,
            )
            (stdout_truncated, stderr_truncated,
             stdout_bytes_total, stderr_bytes_total) = (
                False,
                False,
                0,
                0,
            )
            record_event_failed = True

        lines: list[str] = []
        run_color = ANSI_COLORS[TAG_COLORS["RUN"]]
        exit_color = ANSI_COLORS[TAG_COLORS["EXIT"]]
        err_color = ANSI_COLORS[TAG_COLORS["ERR"]]
        reset = ANSI_COLORS["reset"]

        if record_event_failed:
            lines.append(
                f"{err_color}[ERROR]{reset} "
                f"failed to record event to database"
            )

        if self.show_run:
            lines.append(f"{run_color}[RUN]{reset} {resolved_command}")

        if self.show_exit:
            lines.append(f"{exit_color}[EXIT]{reset} {exit_code}")

        if self.show_stdout and stdout:
            lines.append(stdout.rstrip())

        if self.show_stderr and stderr:
            lines.append(f"{err_color}[ERR]{reset}")
            lines.append(stderr.rstrip())

        if stdout_truncated or stderr_truncated:
            lines.append(
                self._format_truncation_warning(
                    stdout_truncated,
                    stderr_truncated,
                    stdout_bytes_total,
                    stderr_bytes_total,
                )
            )

        return "\n".join(lines)

    def _execute_raw_shell_streaming(
        self, raw_command: str, resolved_command: str
    ) -> str:
        run_color = ANSI_COLORS[TAG_COLORS["RUN"]]
        exit_color = ANSI_COLORS[TAG_COLORS["EXIT"]]
        reset = ANSI_COLORS["reset"]

        def _out(s: str) -> None:
            if self.output_fn:
                self.output_fn(s)

        def _err(s: str) -> None:
            if self.error_fn:
                self.error_fn(s)
            elif self.output_fn:
                self.output_fn(s)

        if self.show_run and self.output_fn:
            msg = f"{run_color}[RUN]{reset} {resolved_command}\n"
            self.output_fn(msg)

        # Pass cwd if shell_fallback is enabled
        cwd = (
            self.cwd if self.current_panel_has_shell_fallback() else None
        )
        result = self.executor.run_stream(
            resolved_command, on_stdout=_out, on_stderr=_err, cwd=cwd
        )

        record_event_failed = False
        try:
            (stdout_truncated, stderr_truncated,
             stdout_bytes_total, stderr_bytes_total) = (
                self.store.record_event(
                    self.panel,
                    raw_command,
                    resolved_command,
                    result.exit_code,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    started_at=result.started_at,
                    duration_ms=result.duration_ms,
                )
            )
        except Exception as e:
            write_crash_log(
                e,
                panel=self.panel,
                raw_command=raw_command,
                resolved_command=resolved_command,
                db_name=self.active_db_name,
                db_path=self.active_db_path,
            )
            (stdout_truncated, stderr_truncated,
             stdout_bytes_total, stderr_bytes_total) = (
                False,
                False,
                0,
                0,
            )
            record_event_failed = True

        lines: list[str] = []

        if record_event_failed:
            err_color = ANSI_COLORS[TAG_COLORS["ERR"]]
            reset = ANSI_COLORS["reset"]
            lines.append(
                f"{err_color}[ERROR]{reset} "
                f"failed to record event to database"
            )

        if self.show_exit:
            lines.append(f"{exit_color}[EXIT]{reset} {result.exit_code}")

        if stdout_truncated or stderr_truncated:
            lines.append(
                self._format_truncation_warning(
                    stdout_truncated,
                    stderr_truncated,
                    stdout_bytes_total,
                    stderr_bytes_total,
                )
            )

        return "\n".join(lines)

    def _execute_raw_shell_pty(
        self, raw_command: str, resolved_command: str
    ) -> str:
        run_color = ANSI_COLORS[TAG_COLORS["RUN"]]
        exit_color = ANSI_COLORS[TAG_COLORS["EXIT"]]
        reset = ANSI_COLORS["reset"]

        def _out(s: str) -> None:
            if self.output_fn:
                self.output_fn(s)

        if self.show_run and self.output_fn:
            self.output_fn(f"{run_color}[RUN]{reset} {resolved_command}\n")

        # Pass cwd if shell_fallback is enabled
        cwd = (
            self.cwd if self.current_panel_has_shell_fallback() else None
        )
        result = self.executor.run_pty(
            resolved_command, on_output=_out, cwd=cwd
        )

        # In PTY mode, result.stdout contains captured text;
        # stderr typically empty
        record_event_failed = False
        try:
            (stdout_truncated, stderr_truncated,
             stdout_bytes_total, stderr_bytes_total) = (
                self.store.record_event(
                    self.panel,
                    raw_command,
                    resolved_command,
                    result.exit_code,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    started_at=result.started_at,
                    duration_ms=result.duration_ms,
                )
            )
        except Exception as e:
            write_crash_log(
                e,
                panel=self.panel,
                raw_command=raw_command,
                resolved_command=resolved_command,
                db_name=self.active_db_name,
                db_path=self.active_db_path,
            )
            (stdout_truncated, stderr_truncated,
             stdout_bytes_total, stderr_bytes_total) = (
                False,
                False,
                0,
                0,
            )
            record_event_failed = True

        lines: list[str] = []

        if record_event_failed:
            err_color = ANSI_COLORS[TAG_COLORS["ERR"]]
            reset = ANSI_COLORS["reset"]
            lines.append(
                f"{err_color}[ERROR]{reset} "
                f"failed to record event to database"
            )

        if self.show_exit:
            lines.append(f"{exit_color}[EXIT]{reset} {result.exit_code}")

        if stdout_truncated or stderr_truncated:
            lines.append(
                self._format_truncation_warning(
                    stdout_truncated,
                    stderr_truncated,
                    stdout_bytes_total,
                    stderr_bytes_total,
                )
            )

        return "\n".join(lines)

    def _execute_raw_shell_tty(
        self, raw_command: str, resolved_command: str
    ) -> str:
        """Execute raw shell command with full TTY control."""
        run_color = ANSI_COLORS[TAG_COLORS["RUN"]]
        exit_color = ANSI_COLORS[TAG_COLORS["EXIT"]]
        reset = ANSI_COLORS["reset"]

        # Print RUN tag before handing off to TTY
        if self.show_run and self.output_fn:
            run_msg = f"{run_color}[RUN]{reset} {resolved_command}"
            self.output_fn(run_msg + "\n")

        # Signal UI to prepare for TTY handoff
        if self.output_fn and hasattr(self.output_fn, "__self__"):
            ui = self.output_fn.__self__
            if hasattr(ui, "prepare_tty_handoff"):
                ui.prepare_tty_handoff()

        # Execute with full terminal control
        # Pass cwd if shell_fallback is enabled
        cwd = (
            self.cwd if self.current_panel_has_shell_fallback() else None
        )
        result = self.executor.run_tty(resolved_command, cwd=cwd)

        # Record history (with empty stdout/stderr)
        record_event_failed = False
        try:
            self.store.record_event(
                self.panel,
                raw_command,
                resolved_command,
                result.exit_code,
                stdout="",
                stderr="",
                started_at=result.started_at,
                duration_ms=result.duration_ms,
            )
        except Exception as e:
            write_crash_log(
                e,
                panel=self.panel,
                raw_command=raw_command,
                resolved_command=resolved_command,
                db_name=self.active_db_name,
                db_path=self.active_db_path,
            )
            record_event_failed = True

        # Signal UI to restore after TTY handoff
        if self.output_fn and hasattr(self.output_fn, "__self__"):
            ui = self.output_fn.__self__
            if hasattr(ui, "restore_after_tty"):
                ui.restore_after_tty()

        # Return only EXIT tag
        exit_lines: list[str] = []

        if record_event_failed:
            err_color = ANSI_COLORS[TAG_COLORS["ERR"]]
            reset = ANSI_COLORS["reset"]
            exit_lines.append(
                f"{err_color}[ERROR]{reset} "
                f"failed to record event to database"
            )

        if self.show_exit:
            exit_lines.append(f"{exit_color}[EXIT]{reset} {result.exit_code}")

        return "\n".join(exit_lines)

    # -----------------------
    # Base command handlers
    # -----------------------

    def _handle_list_aliases(self) -> str:
        aliases = self.store.list_aliases(self.panel)

        hist_color = ANSI_COLORS[TAG_COLORS['HISTORY']]
        header_tag = f"{hist_color}[{self.panel}]{ANSI_COLORS['reset']}"

        panel_branding = self.branding.get(self.panel, {})
        panel_color_name = panel_branding.get("panel_color", "reset")
        panel_color = ANSI_COLORS.get(
            panel_color_name, ANSI_COLORS["reset"]
        )

        reset = ANSI_COLORS["reset"]
        dim = ANSI_COLORS["dim"]
        green = ANSI_COLORS["green"]
        cyan = ANSI_COLORS["cyan"]
        pink = ANSI_COLORS["pink"]

        dash = f"{green}-{reset}"
        arrow = f"{cyan}-{reset}{pink}>{reset}"

        lines = [f"{header_tag} aliases:"]
        if aliases:
            for alias in aliases:
                name = alias["name"]
                command = alias["command"]
                lines.append(
                    f"  {dash} {panel_color}{name}{reset} {arrow} "
                    f"{dim}{command}{reset}"
                )
        else:
            lines.append("  (none)")

        return "\n".join(lines)

    def _handle_new_alias(self, parts: list[str], raw_input: str) -> str:
        """Handle alias creation.

        Preserves raw shell text in the alias body.

        Args:
            parts: Tokenized command parts (for extracting trigger
                and alias name)
            raw_input: Raw input string to preserve quotes and
                backslashes in alias body

        Returns:
            Success or error message
        """
        if len(parts) < 3:
            return "Usage: N <name> <command...>"

        # Extract the trigger and alias name from parts (already tokenized)
        trigger = parts[0]  # e.g., "N"
        name = parts[1]

        # Find where the alias name ends in the raw input
        # to extract the body while preserving all formatting
        trigger_len = len(trigger)
        after_trigger = raw_input[trigger_len:].lstrip()

        # Now find where the name ends
        # The name should be the first token after the trigger
        name_end = len(name)
        if not after_trigger.startswith(name):
            # This shouldn't happen, but handle edge case
            return "Error parsing alias name"

        # Extract everything after the name as the raw body
        raw_body = after_trigger[name_end:].lstrip()

        if not raw_body:
            return "Usage: N <name> <command...>"

        # Check if alias name is a reserved trigger
        reserved = self.get_reserved_triggers()
        if name in reserved:
            # Find which command owns this trigger
            owner = "unknown"
            for cmd_name, cmd_cfg in self.base_commands.items():
                triggers = cmd_cfg.get("triggers", []) or []
                if name in triggers:
                    owner = cmd_name
                    break

            if owner == "unknown":
                help_cfg = self.config.commands.get("help", {})
                help_triggers = help_cfg.get("triggers", []) or []
                if name in help_triggers:
                    owner = "help"

            return (
                f"Cannot create alias '{name}': "
                f"reserved trigger for '{owner}' command."
            )

        # Store the raw body exactly as entered (preserving quotes,
        # backslashes, etc.)
        self.store.add_alias(self.panel, name, raw_body)
        return f"Added alias '{name}' in panel {self.panel}."

    def _handle_remove_alias(self, parts: list[str]) -> str:
        if len(parts) < 2:
            return "Usage: RM <name>"

        name = parts[1]
        self.store.remove_alias(self.panel, name)
        return f"Removed alias '{name}' from panel {self.panel}."

    def _handle_rerun_alias(self) -> str:
        last_alias = self._last_alias_by_panel.get(self.panel)
        if last_alias is None:
            return f"No previous alias to re-run in panel {self.panel}."

        alias_cmd = self.store.find_alias(self.panel, last_alias)
        if alias_cmd is not None:
            return self._execute_alias(last_alias, alias_cmd)
        return f"No previous alias to re-run in panel {self.panel}."

    # -----------------------
    # Builtins (cd, pwd)
    # -----------------------

    def _handle_cd(self, parts: list[str]) -> str:
        """Handle cd command in shell_fallback panels."""
        if len(parts) == 1:
            # cd with no args -> go to home
            target = os.path.expanduser("~")
        elif parts[1] == "-":
            # cd - -> toggle to previous directory
            if self.prev_cwd is None:
                return "cd: no previous directory"
            target = self.prev_cwd
        else:
            # cd <path>
            target = os.path.expanduser(" ".join(parts[1:]))

        # Resolve relative to current cwd
        if not os.path.isabs(target):
            target = os.path.join(self.cwd, target)

        # Normalize and resolve
        target = os.path.normpath(target)

        # Check if directory exists
        if not os.path.isdir(target):
            error_msg = f"cd: no such directory: {target}"
            # Record in history with error
            self.store.record_event(
                self.panel,
                " ".join(parts),
                " ".join(parts),
                exit_code=1,
                stdout="",
                stderr=error_msg,
                started_at=datetime.now().isoformat(),
                duration_ms=0,
            )
            return error_msg

        # Success - update cwd
        self.prev_cwd = self.cwd
        self.cwd = target

        # Record in history (empty stdout on success)
        self.store.record_event(
            self.panel,
            " ".join(parts),
            " ".join(parts),
            exit_code=0,
            stdout="",
            stderr="",
            started_at=datetime.now().isoformat(),
            duration_ms=0,
        )

        return ""

    def _handle_pwd(self) -> str:
        """Handle pwd command in shell_fallback panels."""
        # Record in history
        self.store.record_event(
            self.panel,
            "pwd",
            "pwd",
            exit_code=0,
            stdout=self.cwd,
            stderr="",
            started_at=datetime.now().isoformat(),
            duration_ms=0,
        )

        return self.cwd

    # -----------------------
    # History
    # -----------------------

    def _handle_history(self) -> str:
        rows = self.store.get_history(self.panel)
        if not rows:
            return "No history for this panel yet."

        hist_color = ANSI_COLORS[TAG_COLORS["HISTORY"]]
        cyan = ANSI_COLORS["cyan"]
        magenta = ANSI_COLORS["magenta"]
        green = ANSI_COLORS["green"]
        red = ANSI_COLORS["red"]
        yellow = ANSI_COLORS["yellow"]
        reset = ANSI_COLORS["reset"]

        lines = [f"{hist_color}[HISTORY]{reset} panel {self.panel}", ""]

        # Store returns newest → oldest, but we display oldest → newest
        # Assign indices (1 = newest, N = oldest), then reverse for display
        rows_with_idx = [(idx + 1, row) for idx, row in enumerate(rows)]
        rows_with_idx = list(reversed(rows_with_idx))

        for idx, row in rows_with_idx:
            raw_cmd = row["raw_command"]
            exit_code = row["exit_code"]
            created_at = row["created_at"]
            stdout_total = row["stdout_bytes_total"]
            stderr_total = row["stderr_bytes_total"]
            stdout_trunc = row["stdout_truncated"]
            stderr_trunc = row["stderr_truncated"]

            index_str = f"{cyan}{idx:<3}{reset}"

            if len(raw_cmd) > 18:
                cmd_display = raw_cmd[:16] + ".."
            else:
                cmd_display = raw_cmd.ljust(18)

            if exit_code == 0:
                exit_str = f"{green}EXIT {exit_code}{reset}"
            else:
                exit_str = f"{red}EXIT {exit_code:<3}{reset}"

            try:
                dt = datetime.fromisoformat(created_at)
                timestamp_str = (
                    f"{magenta}{dt.strftime('%Y-%m-%d %H:%M:%S')}{reset}"
                )
            except Exception:
                timestamp_str = f"{magenta}{created_at[:19]}{reset}"

            output_parts: list[str] = []
            if stdout_total or stderr_total:
                total_output = (stdout_total or 0) + (stderr_total or 0)
                if total_output > 0:
                    if total_output >= 1024:
                        size_str = f"{total_output / 1024:.1f}KB"
                    else:
                        size_str = f"{total_output}B"
                    output_parts.append(size_str)

            if stdout_trunc or stderr_trunc:
                output_parts.append(f"{yellow}⚠TRUNC{reset}")

            line = f"{index_str} {cmd_display} {exit_str}  {timestamp_str}"
            if output_parts:
                line += "  " + " ".join(output_parts)

            lines.append(line)

        return "\n".join(lines)

    def _handle_history_detail(self, index: int) -> str:
        detail = self.store.get_history_detail(self.panel, index)

        if detail is None:
            history = self.store.get_history(self.panel)
            if not history:
                return "No history for this panel yet."
            return (
                f"Invalid index {index}. "
                f"History has {len(history)} entries."
            )

        raw_cmd = detail.get("raw_command", "")
        resolved_cmd = detail.get("resolved_command", "")
        exit_code = detail.get("exit_code", 0)
        created_at = detail.get("created_at")
        started_at = detail.get("started_at")
        duration_ms = detail.get("duration_ms")
        stdout = detail.get("stdout", "")
        stderr = detail.get("stderr", "")
        stdout_total = detail.get("stdout_bytes_total", 0)
        stderr_total = detail.get("stderr_bytes_total", 0)
        stdout_trunc = detail.get("stdout_truncated", 0)
        stderr_trunc = detail.get("stderr_truncated", 0)

        hist_color = ANSI_COLORS[TAG_COLORS["HISTORY"]]
        yellow = ANSI_COLORS["yellow"]
        green = ANSI_COLORS["green"]
        red = ANSI_COLORS["red"]
        reset = ANSI_COLORS["reset"]

        lines: list[str] = [
            f"{hist_color}[HISTORY]{reset} #{index} panel {self.panel}"
        ]

        try:
            dt = datetime.fromisoformat(started_at or created_at)
            time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            time_str = (started_at or created_at)[:19]

        lines.append(f"Time: {time_str} UTC")
        lines.append(f"Raw:  {raw_cmd}")
        lines.append(f"Exec: {resolved_cmd}")

        if exit_code == 0:
            lines.append(f"Exit: {green}{exit_code}{reset}")
        else:
            lines.append(f"Exit: {red}{exit_code}{reset}")

        if duration_ms is not None:
            lines.append(f"Duration: {duration_ms}ms")

        lines.append("")

        if stdout_trunc:
            lines.append(f"{yellow}⚠ stdout truncated{reset}")
            lines.append(
                f"Stored: {MAX_STDOUT_BYTES:,} of {stdout_total:,} bytes"
            )
            lines.append("")

        if stderr_trunc:
            lines.append(f"{yellow}⚠ stderr truncated{reset}")
            lines.append(
                f"Stored: {MAX_STDERR_BYTES:,} of {stderr_total:,} bytes"
            )
            lines.append("")

        if stdout:
            lines.append(
                "--- stdout (truncated) ---"
                if stdout_trunc
                else "--- stdout ---"
            )
            lines.append(stdout.rstrip())
            lines.append("")

        if stderr:
            lines.append(
                "--- stderr (truncated) ---"
                if stderr_trunc
                else "--- stderr ---"
            )
            lines.append(stderr.rstrip())
            lines.append("")

        return "\n".join(lines)

    # -----------------------
    # Settings
    # -----------------------

    def _handle_set_command(self, parts: list[str]) -> str:
        if len(parts) == 1:
            lines = ["Current settings:"]
            lines.append(f"  show_run: {self.show_run}")
            lines.append(f"  show_exit: {self.show_exit}")
            lines.append(f"  show_stdout: {self.show_stdout}")
            lines.append(f"  show_stderr: {self.show_stderr}")
            lines.append(f"  force_color: {self.force_color}")
            lines.append(f"  welcome: {self.welcome}")
            return "\n".join(lines)

        setting = parts[1]

        if setting == "reset":
            self.show_run = True
            self.show_exit = True
            self.show_stdout = True
            self.show_stderr = True
            self.force_color = True
            self.welcome = True
            self.store.set_setting("welcome", "true")
            return "Settings reset to defaults."

        if len(parts) < 3:
            return "Usage: SET <setting> <value>"

        value = parts[2].lower() in ["true", "1", "yes"]

        if setting == "show_run":
            self.show_run = value
            self.store.set_setting(
                "show_run", "true" if value else "false"
            )
            return f"show_run set to {value}"
        if setting == "show_exit":
            self.show_exit = value
            return f"show_exit set to {value}"
        if setting == "show_stdout":
            self.show_stdout = value
            return f"show_stdout set to {value}"
        if setting == "show_stderr":
            self.show_stderr = value
            return f"show_stderr set to {value}"
        if setting == "force_color":
            self.force_color = value
            return f"force_color set to {value}"
        if setting == "welcome":
            self.welcome = value
            self.store.set_setting(
                "welcome", "true" if value else "false"
            )
            return f"welcome set to {value}"

        return f"Unknown setting: {setting}"

    # -----------------------
    # Help + logging
    # -----------------------

    def _generate_help(self) -> str:
        lines: list[str] = []

        # Help triggers from YAML: commands.help.triggers
        help_cfg = (
            self.config.commands.get("help", {})
            if isinstance(self.config.commands, dict)
            else {}
        )
        help_triggers = (
            help_cfg.get("triggers", [])
            if isinstance(help_cfg, dict)
            else []
        )
        help_display = (
            ", ".join(help_triggers) if help_triggers else "?"
        )

        lines.append("Base commands:")
        for cmd_key, cmd_cfg in self.base_commands.items():
            desc = cmd_cfg.get("description", "")
            triggers = cmd_cfg.get("triggers", [])
            if triggers:
                lines.append(f"  {cmd_key} ({', '.join(triggers)}): {desc}")
            else:
                lines.append(f"  {cmd_key}: {desc}")

        lines.append("")
        lines.append("Panels:")
        for panel_name, panel_cfg in self.config.panels.items():
            entry = panel_cfg.get("entry")
            lines.append(f"  {panel_name}  (entry: {entry})")

        lines.append("")
        lines.append("Help:")
        lines.append(f"  {help_display}")

        return "\n".join(lines)

    # -----------------------
    # REP panel DB commands
    # -----------------------

    def _handle_rep_command(
        self, cmd: str, parts: list[str], stripped: str
    ) -> str:
        """Handle commands in REP panel (no alias support).

        REP panel only supports: DB, USE, WHERE, help commands.
        """
        # DB command
        if cmd == "DB":
            return self._handle_db()

        # USE command
        if cmd == "USE":
            if len(parts) < 2:
                return "Usage: USE <id|name>"
            arg = parts[1]
            return self._handle_use(arg)

        # WHERE command
        if cmd == "WHERE":
            return self._handle_where()

        # INFO command
        if cmd == "INFO":
            return self._handle_info()

        # Help commands
        if cmd in ["?", "h", "help"]:
            return self._rep_help()

        # Bare panel switching (entry tokens)
        if stripped in self._panel_entries:
            self.panel_stack.append(stripped)
            self.panel = stripped
            return ""

        # Unknown command in REP
        return "Unknown command (Tab Tab for REP menu)"

    def _rep_help(self) -> str:
        """Generate help text for REP panel."""
        lines = []
        lines.append("REP commands:")
        lines.append("  DB              list available databases")
        lines.append("  USE <id|name>   switch active database")
        lines.append(
            "  WHERE           show active database path and source"
        )
        lines.append("  INFO            show active database metadata")
        return "\n".join(lines)

    def _init_active_db_from_path(self, db_path: Path) -> None:
        """Initialize active DB state from a database path."""
        self.active_db_path = db_path

        # Determine source and name from path
        data_root = cfg_module.get_data_root()
        core_path = cfg_module.core_db_path(data_root)

        if db_path == core_path:
            self.active_db_name = "core"
            self.active_db_source = "core"
        else:
            # Project DB - look up metadata in core registry by exact db_path
            from . import db as db_module

            metadata = db_module.lookup_project_metadata(core_path, db_path)

            if metadata:
                self.active_db_name = metadata["project_name"]
                self.active_db_source = metadata["root_path"]
            else:
                # Fallback if not found in registry
                self.active_db_name = "project"
                self.active_db_source = "unknown"

    def _discover_db_targets(self) -> list[dict[str, Any]]:
        targets = []

        data_root = cfg_module.get_data_root()
        core_path = cfg_module.core_db_path(data_root)

        # Always include core target with path
        targets.append(
            {
                "id": 1,
                "name": "core",
                "source": "core",
                "key": "core",
                "path": core_path,
                "active": (
                    (self.active_db_path == core_path)
                    if self.active_db_path
                    else False
                ),
            }
        )

        # List all known project DBs from the core registry
        # Include only if db_path exists on disk
        from . import db as db_module

        project_dbs = db_module.discover_project_dbs(core_path)

        next_id = 2
        for proj in project_dbs:
            targets.append(
                {
                    "id": next_id,
                    "name": proj["project_name"],
                    "source": proj["root_path"],
                    "key": proj["project_id"],
                    "path": proj["db_path"],
                    "active": (
                        (str(self.active_db_path) == str(proj["db_path"]))
                        if self.active_db_path
                        else False
                    ),
                }
            )
            next_id += 1

        return targets

    def _handle_db(self) -> str:
        """Handle DB command - list available DB targets."""
        targets = self._discover_db_targets()

        if not targets:
            return "No databases available."

        # Build table
        headers = ["ID", "ACTIVE", "NAME", "KEY", "SOURCE"]
        rows = []
        for t in targets:
            rows.append(
                [
                    str(t["id"]),
                    "*" if t["active"] else "",
                    t["name"],
                    t["key"],
                    t["source"],
                ]
            )

        title = "[DB] targets"
        return format_table(headers, rows, title)

    def _handle_use(self, arg: str) -> str:
        """Handle USE command - switch active DB."""
        if not arg:
            return "Usage: USE <id|name>"

        targets = self._discover_db_targets()

        # Try to match by ID first (exact match)
        selected = None
        for t in targets:
            if str(t["id"]) == arg:
                selected = t
                break

        # If not matched by ID, try matching by name
        if not selected:
            matches = [t for t in targets if t["name"] == arg]
            if len(matches) == 0:
                return f"Unknown DB target: {arg}"
            elif len(matches) > 1:
                # Ambiguous - multiple targets with same name
                match_ids = [str(t["id"]) for t in matches]
                return (
                    f"Ambiguous DB target name: {arg}. "
                    f"Matches: {', '.join(match_ids)}"
                )
            else:
                selected = matches[0]

        # Switch to the selected DB
        new_db_path = selected["path"]

        # Ensure schema exists
        try:
            from . import db as db_module

            db_module.ensure_schema(new_db_path)
        except Exception as e:
            return f"Failed to ensure schema for {selected['name']}: {e}"

        # Create new store and replace current one
        try:
            new_store = SQLiteStore(new_db_path)
            self.store = new_store

            # Update active DB tracking with selected metadata
            self.active_db_path = new_db_path
            self.active_db_name = selected["name"]
            self.active_db_source = selected["source"]

            return f"Switched to {selected['name']} database."
        except Exception as e:
            return f"Failed to switch to {selected['name']}: {e}"

    def _handle_where(self) -> str:
        """Handle WHERE command - show active DB info."""
        if not self.active_db_path:
            return "No active database."

        lines = []
        lines.append(f"Active database: {self.active_db_name}")
        lines.append(f"Source:          {self.active_db_source}")
        lines.append(f"Path:            {self.active_db_path}")

        return "\n".join(lines)

    def _handle_info(self) -> str:
        """Handle INFO command - show active DB metadata."""
        if not self.active_db_path:
            return "No active database."

        db_path = self.active_db_path

        # Check if file exists
        if not db_path.exists():
            return f"Database file does not exist: {db_path}"

        # Get file stats
        try:
            stat = db_path.stat()
            size_bytes = stat.st_size
            modified_timestamp = stat.st_mtime

            # Format size (human readable)
            if size_bytes < 1024:
                size_str = f"{size_bytes}B"
            elif size_bytes < 1024 * 1024:
                size_str = f"{size_bytes / 1024:.1f}KB"
            elif size_bytes < 1024 * 1024 * 1024:
                size_str = f"{size_bytes / (1024 * 1024):.1f}MB"
            else:
                size_str = f"{size_bytes / (1024 * 1024 * 1024):.2f}GB"

            # Format modified time
            from datetime import datetime

            modified_dt = datetime.fromtimestamp(modified_timestamp)
            modified_str = modified_dt.strftime("%Y-%m-%d %H:%M:%S")

            # Check if writable
            writable = os.access(db_path, os.W_OK)
            writable_str = "yes" if writable else "no"

        except Exception as e:
            return f"Failed to get file info: {e}"

        # Format output
        lines = []
        lines.append("[DB] info")
        lines.append(f"Name:     {self.active_db_name}")
        lines.append(f"Source:   {self.active_db_source}")
        lines.append(f"Path:     {db_path}")
        lines.append(f"Size:     {size_str}")
        lines.append(f"Modified: {modified_str}")
        lines.append(f"Writable: {writable_str}")

        return "\n".join(lines)
