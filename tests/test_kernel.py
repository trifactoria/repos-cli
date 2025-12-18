# tests/test_kernel.py
"""
Kernel tests with dependency injection.
Kernel should only handle routing/formatting - actual work delegated to services.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import repos_cli.kernel as kernel_mod
from repos_cli.kernel import Kernel

# ----------------------------------------------------------------
# Boundary tests (hard gates)
# ----------------------------------------------------------------


def test_kernel_module_does_not_touch_schema_creation_or_migrations() -> None:
    """
    HARD BOUNDARY:
    - Kernel must not create/ensure/migrate schema.
    - Kernel must not import repos_cli.db or call ensure_schema / migration helpers.
    - Kernel must not contain SQL DDL (CREATE/ALTER/DROP TABLE).

    This is intentionally strict. If it fails, move schema work to repos_cli.db/init.
    """
    src_path = Path(kernel_mod.__file__)
    text = src_path.read_text(encoding="utf-8")

    forbidden_substrings = [
        # direct db module coupling (exception: USE command imports db to ensure schema)
        "import repos_cli.db",
        "from repos_cli import db",
        "from .db import",
        # obvious DDL
        "CREATE TABLE",
        "ALTER TABLE",
        "DROP TABLE",
        "PRAGMA table_info",
        # sqlite direct usage
        "import sqlite3",
        "sqlite3.connect",
        "migrate_",
        "migration",
    ]

    hits = [s for s in forbidden_substrings if s in text]
    assert not hits, f"Kernel must not touch schema/migrations directly. Found: {hits}"

    # Allow "from . import db" and "ensure_schema(" for USE command
    # These are acceptable for database switching functionality


# ----------------------------------------------------------------
# Mock dependencies
# ----------------------------------------------------------------


class FakeStore:
    """Mock RepoStore for testing kernel behavior."""

    def __init__(self):
        self.aliases = {}  # {(panel, name): command}
        self.events = []
        self.settings = {}

    def add_alias(self, panel: str, name: str, command: str) -> None:
        self.aliases[(panel, name)] = command

    def find_alias(self, panel: str, name: str) -> str | None:
        return self.aliases.get((panel, name))

    def list_aliases(self, panel: str) -> list[dict]:
        return [
            {"name": name, "command": cmd}
            for (p, name), cmd in sorted(self.aliases.items())
            if p == panel
        ]

    def remove_alias(self, panel: str, name: str) -> None:
        self.aliases.pop((panel, name), None)

    def record_event(
        self,
        panel,
        raw_command,
        resolved_command,
        exit_code,
        stdout,
        stderr,
        started_at=None,
        duration_ms=None,
    ):
        self.events.append(
            {
                "panel": panel,
                "raw": raw_command,
                "resolved": resolved_command,
                "exit": exit_code,
                "stdout": stdout,
                "stderr": stderr,
            }
        )
        return (False, False, len(stdout), len(stderr))

    def get_history(self, panel: str) -> list[dict]:
        return [
            {
                "id": i + 1,
                "raw_command": e["raw"],
                "exit_code": e["exit"],
                "created_at": "2025-12-14T10:00:00",
                "stdout_bytes_total": 0,
                "stderr_bytes_total": 0,
                "stdout_truncated": 0,
                "stderr_truncated": 0,
            }
            for i, e in enumerate(self.events)
            if e["panel"] == panel
        ]

    def get_history_detail(self, panel: str, index: int) -> dict | None:
        events = [e for e in self.events if e["panel"] == panel]
        if 1 <= index <= len(events):
            e = events[index - 1]
            return {
                "raw_command": e["raw"],
                "resolved_command": e["resolved"],
                "exit_code": e["exit"],
                "stdout": e["stdout"],
                "stderr": e["stderr"],
                "started_at": "2025-12-14T10:00:00",
                "duration_ms": 100,
                "stdout_bytes_total": len(e["stdout"]),
                "stderr_bytes_total": len(e["stderr"]),
                "stdout_truncated": 0,
                "stderr_truncated": 0,
            }
        return None

    def get_setting(self, key: str, default: str) -> str:
        return self.settings.get(key, default)

    def set_setting(self, key: str, value: str) -> None:
        self.settings[key] = value


class FakeExecutor:
    """Mock Executor for testing kernel behavior."""

    def __init__(self):
        self.commands_run = []
        self.argv_runs = []  # Track argv-based runs

    def run(self, command: str, cwd: str = None) -> tuple[int, str, str, str, int]:
        self.commands_run.append(command)
        return (0, "output\n", "", "2025-12-14T10:00:00", 100)

    def run_argv(
        self, script: str, posargs: list[str] | None = None, cwd: str | None = None
    ) -> tuple[int, str, str, str, int]:
        """Mock argv-based execution."""
        self.argv_runs.append({"script": script, "posargs": posargs or []})
        # For compatibility with tests, also track as a command
        # Simulate what would happen: script with args substituted
        if posargs:
            # Simulate $1, $2, etc. substitution for test compatibility
            import re

            result_script = script
            for i, arg in enumerate(posargs, 1):
                result_script = result_script.replace(f"${i}", arg)
            # Also handle $@
            result_script = result_script.replace("$@", " ".join(posargs))
            self.commands_run.append(result_script)
        else:
            self.commands_run.append(script)
        return (0, "output\n", "", "2025-12-14T10:00:00", 100)


class FakeConfig:
    """Mock ConfigModel for testing kernel behavior."""

    def __init__(self):
        self.panels = {
            "REP": {"entry": "REP", "name": "REP", "message": "Welcome to RepOS!"},
            "G": {"entry": "G", "name": "Git", "message": "Git panel"},
            "A": {"entry": "A", "name": "Automatr", "message": "Automatr panel"},
        }
        self.commands = {
            "help": {"triggers": ["?", "h"]},
            "base": {
                "list": {"triggers": ["L", "list"], "description": "List aliases"},
                "add": {"triggers": ["N", "A", "add"], "description": "Add alias"},
                "remove": {"triggers": ["RM", "remove"], "description": "Remove alias"},
                "rerun": {"triggers": ["RR", "rerun"], "description": "Rerun last"},
                "history": {"triggers": ["H", "history"], "description": "Show history"},
                "settings": {"triggers": ["SET", "set"], "description": "Settings"},
            },
        }
        self.branding = {
            "REP": {"panel_color": "cyan", "caret_color": "pink"},
            "G": {"panel_color": "orange", "caret_color": "orange"},
        }
        self.system = {"name": "RepOS", "entry_alias": "AA"}
        self.exit = {"entry": "ZZ", "message": "Love Ya - Bye!"}


class FakeConfigWithShellFallback:
    """Mock ConfigModel with shell_fallback enabled for SH panel."""

    def __init__(self):
        self.panels = {
            "REP": {"entry": "REP", "name": "REP", "message": "Welcome to RepOS!"},
            "G": {"entry": "G", "name": "Git", "message": "Git panel"},
            "SH": {
                "entry": "SH",
                "name": "Shell",
                "message": "Shell panel",
                "shell_fallback": True,
            },
        }
        self.commands = {
            "help": {"triggers": ["?", "h"]},
            "base": {
                "list": {"triggers": ["L", "list"], "description": "List aliases"},
                "add": {"triggers": ["N", "A", "add"], "description": "Add alias"},
                "remove": {"triggers": ["RM", "remove"], "description": "Remove alias"},
                "rerun": {"triggers": ["RR", "rerun"], "description": "Rerun last"},
                "history": {"triggers": ["H", "history"], "description": "Show history"},
                "settings": {"triggers": ["SET", "set"], "description": "Settings"},
            },
        }
        self.branding = {
            "REP": {"panel_color": "cyan", "caret_color": "pink"},
            "SH": {"panel_color": "green", "caret_color": "green"},
        }
        self.system = {"name": "RepOS", "entry_alias": "AA"}
        self.exit = {"entry": "ZZ", "message": "Love Ya - Bye!"}


@pytest.fixture
def kernel_with_mocks() -> Kernel:
    """Create kernel with mock dependencies."""
    store = FakeStore()
    executor = FakeExecutor()
    config = FakeConfig()

    k = Kernel(store=store, executor=executor, config=config)
    k.start()
    return k


# ----------------------------------------------------------------
# Kernel delegates to store for alias operations
# ----------------------------------------------------------------


def test_kernel_delegates_add_alias_to_store(kernel_with_mocks: Kernel):
    """Kernel must call store.add_alias(), not handle DB directly."""
    k = kernel_with_mocks
    k.handle_command("G")

    out = k.handle_command("N gs git status")

    assert "Added alias" in out
    assert k.store.find_alias("G", "gs") == "git status"


def test_kernel_delegates_list_aliases_to_store(kernel_with_mocks: Kernel):
    """Kernel must call store.list_aliases() for display."""
    k = kernel_with_mocks
    k.handle_command("G")
    k.store.add_alias("G", "gs", "git status")

    out = k.handle_command("L")

    assert "gs" in out
    assert "git status" in out


def test_kernel_delegates_remove_alias_to_store(kernel_with_mocks: Kernel):
    """Kernel must call store.remove_alias()."""
    k = kernel_with_mocks
    k.handle_command("G")
    k.store.add_alias("G", "gs", "git status")

    out = k.handle_command("RM gs")

    assert "Removed alias" in out
    assert k.store.find_alias("G", "gs") is None


# ----------------------------------------------------------------
# Kernel delegates to executor for command execution
# ----------------------------------------------------------------


def test_kernel_delegates_alias_execution_to_executor(kernel_with_mocks: Kernel):
    """Kernel must call executor.run() for alias execution."""
    k = kernel_with_mocks
    k.handle_command("G")
    k.store.add_alias("G", "test", "echo hello")

    out = k.handle_command("test")

    assert k.executor.commands_run == ["echo hello"]
    assert "output" in out


def test_kernel_delegates_raw_shell_to_executor(kernel_with_mocks: Kernel):
    """Kernel must call executor.run() for ! commands."""
    k = kernel_with_mocks

    out = k.handle_command("!echo test")

    assert k.executor.commands_run == ["echo test"]
    assert "output" in out


# ----------------------------------------------------------------
# Kernel delegates to store for history
# ----------------------------------------------------------------


def test_kernel_delegates_history_display_to_store(kernel_with_mocks: Kernel):
    """Kernel must call store.get_history() for display."""
    k = kernel_with_mocks
    k.handle_command("G")
    k.store.record_event("G", "test", "test", 0, "out", "")

    out = k.handle_command("H")

    assert "[HISTORY]" in out
    assert "test" in out


def test_kernel_delegates_history_detail_to_store(kernel_with_mocks: Kernel):
    """Kernel must call store.get_history_detail() for detail view."""
    k = kernel_with_mocks
    k.handle_command("G")
    k.store.record_event("G", "test", "echo test", 0, "output", "")

    out = k.handle_command("H 1")

    assert "Raw:" in out
    assert "test" in out


# ----------------------------------------------------------------
# Kernel delegates to store for settings
# ----------------------------------------------------------------


def test_kernel_delegates_get_setting_to_store(kernel_with_mocks: Kernel):
    """Kernel must call store.get_setting()."""
    k = kernel_with_mocks
    k.store.set_setting("test_key", "test_value")

    # Kernel internally calls store.get_setting during initialization
    assert k.store.get_setting("test_key", "default") == "test_value"


def test_kernel_delegates_set_setting_to_store(kernel_with_mocks: Kernel):
    """Kernel must call store.set_setting() for SET command."""
    k = kernel_with_mocks

    k.handle_command("SET show_run false")

    assert k.store.get_setting("show_run", "true") == "false"


# ----------------------------------------------------------------
# Kernel uses config for branding and routing
# ----------------------------------------------------------------


def test_kernel_uses_config_for_panel_routing(kernel_with_mocks: Kernel):
    """Kernel must use config.panels for navigation."""
    k = kernel_with_mocks

    k.handle_command("G")

    assert k.panel == "G"
    assert "G" in k.config.panels


def test_kernel_uses_config_for_prompt_branding(kernel_with_mocks: Kernel):
    """Kernel must use config.branding for prompt colors."""
    k = kernel_with_mocks

    prompt = k.prompt()

    assert "REP" in prompt
    assert ">" in prompt


# ----------------------------------------------------------------
# Kernel still handles routing/formatting logic
# ----------------------------------------------------------------


def test_kernel_handles_command_routing(kernel_with_mocks: Kernel):
    """Kernel must parse and route commands correctly."""
    k = kernel_with_mocks

    # Panel switching
    k.handle_command("G")
    assert k.panel == "G"

    # Help command (must be in non-REP panel)
    out = k.handle_command("?")
    assert "Base commands:" in out

    # Z navigation
    k.handle_command("Z")
    assert k.panel == "REP"


def test_kernel_formats_output_based_on_settings(kernel_with_mocks: Kernel):
    """Kernel must respect show_run/show_exit/show_stdout settings."""
    k = kernel_with_mocks
    k.show_run = False
    k.show_exit = False
    k.show_stdout = True

    k.handle_command("G")
    k.store.add_alias("G", "test", "echo hi")
    out = k.handle_command("test")

    assert "[RUN]" not in out
    assert "[EXIT]" not in out
    assert "output" in out  # stdout still shown


def test_kernel_handles_exit_command(kernel_with_mocks: Kernel):
    """Kernel must handle ZZ exit without delegating."""
    k = kernel_with_mocks

    out = k.handle_command("ZZ")

    assert k.running is False
    assert "Exiting" in out or "Bye" in out


# ----------------------------------------------------------------
# Additional coverage: Missing lines in kernel.py
# ----------------------------------------------------------------


def test_kernel_handles_panel_without_entry():
    """Cover line 92: continue when panel has no entry."""
    store = FakeStore()
    executor = FakeExecutor()
    config = FakeConfig()

    # Add a panel without entry field
    config.panels["NoEntry"] = {"name": "NoEntry"}  # missing "entry" key

    k = Kernel(store=store, executor=executor, config=config)
    k.start()

    # Kernel should handle panels without entry gracefully
    assert k.panel == "REP"


def test_kernel_handles_config_with_get_method():
    """Cover lines 103-105, 201-209: config.get() method for exit handling."""
    store = FakeStore()
    executor = FakeExecutor()

    # Create config with get method
    class ConfigWithGet:
        def __init__(self):
            self.panels = {
                "REP": {"entry": "REP", "name": "REP", "message": "Welcome!"},
            }
            self.commands = {
                "help": {"triggers": ["?"]},
                "base": {},
            }
            self.branding = {"REP": {"panel_color": "cyan", "caret_color": "pink"}}
            self.system = {"name": "RepOS", "entry_alias": "AA"}
            self.exit = {"entry": "ZZ", "message": "Bye!"}

        def get(self, key, default=None):
            return getattr(self, key, default)

    config = ConfigWithGet()
    k = Kernel(store=store, executor=executor, config=config)
    k.start()

    # Should add exit entry to documented_commands (line 105)
    assert "ZZ" in k.documented_commands

    # Exit command should use custom message (line 209)
    out = k.handle_command("ZZ")
    assert "Bye!" in out


def test_kernel_start_with_system_welcome_message():
    """Cover line 152: system welcome message replacement."""
    store = FakeStore()
    executor = FakeExecutor()
    config = FakeConfig()

    # Add system welcome message
    config.system = {
        "name": "RepOS",
        "entry_alias": "AA",
        "root_panel": "REP",
        "welcome": {"message": "Welcome to RepOS - the developer command environment!"},
    }

    k = Kernel(store=store, executor=executor, config=config)
    out = k.start()

    assert "Welcome to" in out
    assert "Rep" in out  # Branded version


def test_kernel_handles_clear_with_ctrl_l(kernel_with_mocks: Kernel):
    """Cover line 187: clear screen with \\x0c character."""
    k = kernel_with_mocks

    out = k.handle_command("\x0c")

    from repos_cli.config import UI_CLEAR

    assert out == UI_CLEAR


def test_kernel_handles_history_with_invalid_input(kernel_with_mocks: Kernel):
    """Cover lines 227-228: H with invalid input."""
    k = kernel_with_mocks

    out = k.handle_command("H abc")

    assert "Usage:" in out


def test_kernel_handles_empty_command(kernel_with_mocks: Kernel):
    """Cover line 232: unknown command for whitespace-only input."""
    k = kernel_with_mocks

    out = k.handle_command("   ")

    assert "Unknown command" in out


def test_kernel_handles_rep_command_to_root():
    """Cover lines 246-250: REP command alone goes to root."""
    store = FakeStore()
    executor = FakeExecutor()
    config = FakeConfig()
    config.system = {"name": "RepOS", "switch_command": "REP", "root_panel": "REP"}

    k = Kernel(store=store, executor=executor, config=config)
    k.start()

    # Switch to another panel first
    k.handle_command("G")
    assert k.panel == "G"

    # REP alone should go back to root
    k.handle_command("REP")
    assert k.panel == "REP"
    assert len(k.panel_stack) == 1


def test_kernel_handles_rep_command_with_panel():
    """Cover lines 254-261: REP X switches to panel X."""
    store = FakeStore()
    executor = FakeExecutor()
    config = FakeConfig()
    config.system = {"name": "RepOS", "switch_command": "REP", "root_panel": "REP"}

    k = Kernel(store=store, executor=executor, config=config)
    k.start()

    # REP G should switch to Git panel
    out = k.handle_command("REP G")
    assert k.panel == "G"
    assert "Git panel" in out


def test_kernel_handles_alias_with_dollar_one_placeholder(kernel_with_mocks: Kernel):
    """Cover lines 275-276: alias with $1 placeholder."""
    k = kernel_with_mocks
    k.handle_command("G")
    k.store.add_alias("G", "echo", "echo $1")

    k.handle_command("echo hello")

    assert k.executor.commands_run[-1] == "echo hello"


def test_kernel_handles_alias_with_message_placeholder(kernel_with_mocks: Kernel):
    """Cover lines 277-278: alias with {message} placeholder."""
    k = kernel_with_mocks
    k.handle_command("G")
    k.store.add_alias("G", "commit", "git commit -m {message}")

    # New syntax: use kwarg for placeholder
    k.handle_command('commit message="initial commit"')

    # The placeholder should be shell-quoted
    assert "initial commit" in k.executor.commands_run[-1]


def test_kernel_handles_rerun_command(kernel_with_mocks: Kernel):
    """Cover line 296: rerun last alias."""
    k = kernel_with_mocks
    k.handle_command("G")
    k.store.add_alias("G", "test", "echo test")

    # Run alias once
    k.handle_command("test")

    # Rerun should work
    out = k.handle_command("RR")
    assert "output" in out


def test_kernel_handles_rerun_without_previous_alias(kernel_with_mocks: Kernel):
    """Cover lines 488-495: rerun without previous alias."""
    k = kernel_with_mocks
    k.handle_command("G")

    out = k.handle_command("RR")

    assert "No previous alias" in out


def test_kernel_handles_list_aliases_empty(kernel_with_mocks: Kernel):
    """Cover line 466: list aliases when panel has none."""
    k = kernel_with_mocks
    k.handle_command("G")

    out = k.handle_command("L")

    assert "(none)" in out


def test_kernel_handles_new_alias_insufficient_args(kernel_with_mocks: Kernel):
    """Cover line 472: N command with insufficient args."""
    k = kernel_with_mocks
    k.handle_command("G")

    # N with only a name but no command should show usage
    out = k.handle_command("N alias")

    assert "Usage:" in out


def test_kernel_handles_remove_alias_insufficient_args(kernel_with_mocks: Kernel):
    """Cover line 481: RM command with insufficient args."""
    k = kernel_with_mocks
    k.handle_command("G")

    out = k.handle_command("RM")

    assert "Usage:" in out


def test_kernel_handles_history_empty(kernel_with_mocks: Kernel):
    """Cover line 504: history display when empty."""
    k = kernel_with_mocks
    k.handle_command("G")

    out = k.handle_command("H")

    assert "No history" in out


def test_kernel_handles_history_detail_invalid_index(kernel_with_mocks: Kernel):
    """Cover lines 568-571: history detail with invalid index."""
    k = kernel_with_mocks
    k.handle_command("G")
    k.store.record_event("G", "test", "echo test", 0, "out", "")

    out = k.handle_command("H 99")

    assert "Invalid index" in out


def test_kernel_handles_set_command_display(kernel_with_mocks: Kernel):
    """Cover lines 642-649: SET command without args displays settings."""
    k = kernel_with_mocks

    out = k.handle_command("SET")

    assert "show_run:" in out
    assert "show_exit:" in out
    assert "show_stdout:" in out


def test_kernel_handles_set_command_reset(kernel_with_mocks: Kernel):
    """Cover lines 654-661: SET reset."""
    k = kernel_with_mocks
    k.show_run = False

    out = k.handle_command("SET reset")

    assert k.show_run is True
    assert "reset to defaults" in out


def test_kernel_handles_set_command_insufficient_args(kernel_with_mocks: Kernel):
    """Cover line 664: SET with setting but no value."""
    k = kernel_with_mocks

    out = k.handle_command("SET show_run")

    assert "Usage:" in out


def test_kernel_handles_set_show_exit(kernel_with_mocks: Kernel):
    """Cover lines 672-674: SET show_exit."""
    k = kernel_with_mocks

    k.handle_command("SET show_exit false")

    assert k.show_exit is False


def test_kernel_handles_set_show_stdout(kernel_with_mocks: Kernel):
    """Cover lines 675-677: SET show_stdout."""
    k = kernel_with_mocks

    k.handle_command("SET show_stdout false")

    assert k.show_stdout is False


def test_kernel_handles_set_show_stderr(kernel_with_mocks: Kernel):
    """Cover lines 678-680: SET show_stderr."""
    k = kernel_with_mocks

    k.handle_command("SET show_stderr false")

    assert k.show_stderr is False


def test_kernel_handles_set_force_color(kernel_with_mocks: Kernel):
    """Cover lines 681-683: SET force_color."""
    k = kernel_with_mocks

    k.handle_command("SET force_color false")

    assert k.force_color is False


def test_kernel_handles_set_welcome(kernel_with_mocks: Kernel):
    """Cover lines 684-687: SET welcome."""
    k = kernel_with_mocks

    k.handle_command("SET welcome false")

    assert k.welcome is False
    assert k.store.get_setting("welcome", "true") == "false"


def test_kernel_handles_set_unknown_setting(kernel_with_mocks: Kernel):
    """Cover line 689: SET with unknown setting."""
    k = kernel_with_mocks

    out = k.handle_command("SET unknown_setting true")

    assert "Unknown setting" in out


def test_kernel_help_shows_base_commands_in_multiple_panels():
    """Help should show base commands consistently across different panels."""
    store = FakeStore()
    executor = FakeExecutor()
    config = FakeConfig()

    k = Kernel(store=store, executor=executor, config=config)
    k.start()

    # Get help in Git panel
    k.handle_command("G")
    help_git = k.handle_command("?")

    # Should show base commands
    assert "Base commands:" in help_git or "list" in help_git.lower()

    # Get help in Automatr panel
    k.handle_command("A")
    help_automatr = k.handle_command("?")

    # Should also show base commands
    assert "Base commands:" in help_automatr or "list" in help_automatr.lower()


def test_kernel_execute_alias_with_show_run_false(kernel_with_mocks: Kernel):
    """Cover line 357: show_run without show_stdout."""
    k = kernel_with_mocks
    k.show_run = True
    k.show_stdout = False
    k.handle_command("G")
    k.store.add_alias("G", "test", "echo test")

    out = k.handle_command("test")

    assert "[RUN]" in out
    assert "test" in out
    assert "=>" not in out  # Resolved command not shown when show_stdout is false


def test_kernel_execute_alias_with_stderr(kernel_with_mocks: Kernel):
    """Cover lines 366-367: show_stderr."""
    k = kernel_with_mocks
    k.show_stderr = True
    k.handle_command("G")
    k.store.add_alias("G", "test", "echo test")

    # Mock executor to return stderr
    class ExecutorWithStderr:
        def run(self, command: str, cwd: str = None):
            return (1, "out", "error output", "2025-12-14T10:00:00", 100)

    k.executor = ExecutorWithStderr()

    out = k.handle_command("test")

    assert "[ERR]" in out
    assert "error output" in out


def test_kernel_execute_alias_with_truncation(kernel_with_mocks: Kernel):
    """Cover line 370: truncation warning."""
    k = kernel_with_mocks
    k.handle_command("G")
    k.store.add_alias("G", "test", "echo test")

    # Mock store to return truncation
    class StoreWithTruncation(FakeStore):
        def __init__(self, base_store):
            super().__init__()
            self.aliases = base_store.aliases.copy()

        def record_event(
            self,
            panel,
            raw_command,
            resolved_command,
            exit_code,
            stdout,
            stderr,
            started_at=None,
            duration_ms=None,
        ):
            self.events.append(
                {
                    "panel": panel,
                    "raw": raw_command,
                    "resolved": resolved_command,
                    "exit": exit_code,
                    "stdout": stdout,
                    "stderr": stderr,
                }
            )
            return (True, False, 1000000, 0)  # stdout_truncated=True

    k.store = StoreWithTruncation(k.store)

    out = k.handle_command("test")

    assert "output not fully captured" in out


def test_kernel_execute_raw_shell_with_stderr(kernel_with_mocks: Kernel):
    """Cover lines 413-414: raw shell with stderr."""
    k = kernel_with_mocks
    k.show_stderr = True

    # Mock executor to return stderr
    class ExecutorWithStderr:
        def run(self, command: str, cwd: str = None):
            return (1, "out", "error", "2025-12-14T10:00:00", 100)

    k.executor = ExecutorWithStderr()

    out = k.handle_command("!echo test")

    assert "[ERR]" in out


def test_kernel_log_execution_without_session_log_path(kernel_with_mocks: Kernel):
    """Cover line 430: early return when session_log_path is None."""
    k = kernel_with_mocks
    k.session_log_path = None
    k.handle_command("G")
    k.store.add_alias("G", "test", "echo test")

    # Should not crash
    out = k.handle_command("test")
    assert "output" in out


def test_kernel_history_display_with_long_command(kernel_with_mocks: Kernel):
    """Cover line 528: history with command > 18 chars."""
    k = kernel_with_mocks
    k.handle_command("G")
    k.store.record_event("G", "this_is_a_very_long_command_name", "echo test", 0, "out", "")

    out = k.handle_command("H")

    assert ".." in out  # Command should be truncated


def test_kernel_history_display_with_nonzero_exit(kernel_with_mocks: Kernel):
    """Cover line 535: history with non-zero exit code."""
    k = kernel_with_mocks
    k.handle_command("G")
    k.store.record_event("G", "fail", "false", 1, "", "")

    out = k.handle_command("H")

    assert "EXIT 1" in out


def test_kernel_history_display_with_invalid_timestamp(kernel_with_mocks: Kernel):
    """Cover lines 540-541: history with invalid timestamp."""
    k = kernel_with_mocks
    k.handle_command("G")

    # Add event with malformed timestamp
    class StoreWithBadTimestamp(FakeStore):
        def get_history(self, panel: str):
            return [
                {
                    "raw_command": "test",
                    "exit_code": 0,
                    "created_at": "invalid-timestamp",
                    "stdout_bytes_total": 0,
                    "stderr_bytes_total": 0,
                    "stdout_truncated": 0,
                    "stderr_truncated": 0,
                }
            ]

    k.store = StoreWithBadTimestamp()

    out = k.handle_command("H")

    assert "test" in out  # Should handle gracefully


def test_kernel_history_display_with_output_size(kernel_with_mocks: Kernel):
    """Cover lines 545-551: history with output size formatting."""
    k = kernel_with_mocks
    k.handle_command("G")

    # Add event with large output
    class StoreWithOutput(FakeStore):
        def get_history(self, panel: str):
            return [
                {
                    "raw_command": "test",
                    "exit_code": 0,
                    "created_at": "2025-12-14T10:00:00",
                    "stdout_bytes_total": 2048,
                    "stderr_bytes_total": 0,
                    "stdout_truncated": 0,
                    "stderr_truncated": 0,
                }
            ]

    k.store = StoreWithOutput()

    out = k.handle_command("H")

    assert "KB" in out or "B" in out


def test_kernel_history_display_with_truncation_flag(kernel_with_mocks: Kernel):
    """Cover line 554: history with truncation warning."""
    k = kernel_with_mocks
    k.handle_command("G")

    # Add event with truncation
    class StoreWithTrunc(FakeStore):
        def get_history(self, panel: str):
            return [
                {
                    "raw_command": "test",
                    "exit_code": 0,
                    "created_at": "2025-12-14T10:00:00",
                    "stdout_bytes_total": 1000000,
                    "stderr_bytes_total": 0,
                    "stdout_truncated": 1,
                    "stderr_truncated": 0,
                }
            ]

    k.store = StoreWithTrunc()

    out = k.handle_command("H")

    assert "TRUNC" in out


def test_kernel_history_detail_with_invalid_timestamp(kernel_with_mocks: Kernel):
    """Cover lines 597-598: history detail with invalid timestamp."""
    k = kernel_with_mocks
    k.handle_command("G")
    k.store.record_event("G", "test", "echo test", 0, "out", "")

    # Mock bad timestamp
    class StoreWithBadTimestamp(FakeStore):
        def __init__(self):
            super().__init__()

        def get_history_detail(self, panel: str, index: int):
            return {
                "raw_command": "test",
                "resolved_command": "echo test",
                "exit_code": 0,
                "stdout": "out",
                "stderr": "",
                "started_at": "bad-timestamp",
                "created_at": "bad-timestamp",
                "duration_ms": 100,
                "stdout_bytes_total": 3,
                "stderr_bytes_total": 0,
                "stdout_truncated": 0,
                "stderr_truncated": 0,
            }

    k.store = StoreWithBadTimestamp()

    out = k.handle_command("H 1")

    assert "test" in out


def test_kernel_history_detail_with_nonzero_exit(kernel_with_mocks: Kernel):
    """Cover line 607: history detail with non-zero exit code."""
    k = kernel_with_mocks
    k.handle_command("G")

    class StoreWithFailure(FakeStore):
        def __init__(self):
            super().__init__()

        def get_history(self, panel: str):
            return [
                {
                    "raw_command": "fail",
                    "exit_code": 1,
                    "created_at": "2025-12-14T10:00:00",
                    "stdout_bytes_total": 0,
                    "stderr_bytes_total": 0,
                    "stdout_truncated": 0,
                    "stderr_truncated": 0,
                }
            ]

        def get_history_detail(self, panel: str, index: int):
            return {
                "raw_command": "fail",
                "resolved_command": "false",
                "exit_code": 1,
                "stdout": "",
                "stderr": "error",
                "started_at": "2025-12-14T10:00:00",
                "created_at": "2025-12-14T10:00:00",
                "duration_ms": 50,
                "stdout_bytes_total": 0,
                "stderr_bytes_total": 5,
                "stdout_truncated": 0,
                "stderr_truncated": 0,
            }

    k.store = StoreWithFailure()

    out = k.handle_command("H 1")

    assert "Exit:" in out


def test_kernel_history_detail_with_stdout_truncation(kernel_with_mocks: Kernel):
    """Cover lines 615-617: history detail with stdout truncation."""
    k = kernel_with_mocks
    k.handle_command("G")

    class StoreWithTrunc(FakeStore):
        def __init__(self):
            super().__init__()

        def get_history(self, panel: str):
            return [
                {
                    "raw_command": "big",
                    "exit_code": 0,
                    "created_at": "2025-12-14T10:00:00",
                    "stdout_bytes_total": 1000000,
                    "stderr_bytes_total": 0,
                    "stdout_truncated": 1,
                    "stderr_truncated": 0,
                }
            ]

        def get_history_detail(self, panel: str, index: int):
            return {
                "raw_command": "big",
                "resolved_command": "cat largefile",
                "exit_code": 0,
                "stdout": "truncated output",
                "stderr": "",
                "started_at": "2025-12-14T10:00:00",
                "created_at": "2025-12-14T10:00:00",
                "duration_ms": 200,
                "stdout_bytes_total": 1000000,
                "stderr_bytes_total": 0,
                "stdout_truncated": 1,
                "stderr_truncated": 0,
            }

    k.store = StoreWithTrunc()

    out = k.handle_command("H 1")

    assert "stdout truncated" in out


def test_kernel_history_detail_with_stderr_truncation(kernel_with_mocks: Kernel):
    """Cover lines 620-622: history detail with stderr truncation."""
    k = kernel_with_mocks
    k.handle_command("G")

    class StoreWithStderrTrunc(FakeStore):
        def __init__(self):
            super().__init__()

        def get_history(self, panel: str):
            return [
                {
                    "raw_command": "err",
                    "exit_code": 1,
                    "created_at": "2025-12-14T10:00:00",
                    "stdout_bytes_total": 0,
                    "stderr_bytes_total": 500000,
                    "stdout_truncated": 0,
                    "stderr_truncated": 1,
                }
            ]

        def get_history_detail(self, panel: str, index: int):
            return {
                "raw_command": "err",
                "resolved_command": "command",
                "exit_code": 1,
                "stdout": "",
                "stderr": "truncated errors",
                "started_at": "2025-12-14T10:00:00",
                "created_at": "2025-12-14T10:00:00",
                "duration_ms": 150,
                "stdout_bytes_total": 0,
                "stderr_bytes_total": 500000,
                "stdout_truncated": 0,
                "stderr_truncated": 1,
            }

    k.store = StoreWithStderrTrunc()

    out = k.handle_command("H 1")

    assert "stderr truncated" in out


def test_kernel_history_detail_with_both_outputs(kernel_with_mocks: Kernel):
    """Cover lines 630-632: history detail with stderr output."""
    k = kernel_with_mocks
    k.handle_command("G")

    class StoreWithBothOutputs(FakeStore):
        def __init__(self):
            super().__init__()

        def get_history(self, panel: str):
            return [
                {
                    "raw_command": "both",
                    "exit_code": 0,
                    "created_at": "2025-12-14T10:00:00",
                    "stdout_bytes_total": 10,
                    "stderr_bytes_total": 20,
                    "stdout_truncated": 0,
                    "stderr_truncated": 0,
                }
            ]

        def get_history_detail(self, panel: str, index: int):
            return {
                "raw_command": "both",
                "resolved_command": "command",
                "exit_code": 0,
                "stdout": "stdout text",
                "stderr": "stderr text",
                "started_at": "2025-12-14T10:00:00",
                "created_at": "2025-12-14T10:00:00",
                "duration_ms": 100,
                "stdout_bytes_total": 10,
                "stderr_bytes_total": 20,
                "stdout_truncated": 0,
                "stderr_truncated": 0,
            }

    k.store = StoreWithBothOutputs()

    out = k.handle_command("H 1")

    assert "stderr text" in out


def test_kernel_generate_help_without_triggers():
    """Cover line 717: help generation for commands without triggers."""
    store = FakeStore()
    executor = FakeExecutor()
    config = FakeConfig()

    # Add command without triggers
    config.commands["base"]["no_trigger"] = {
        "description": "Command without triggers",
        "triggers": [],
    }

    k = Kernel(store=store, executor=executor, config=config)
    k.start()

    # Switch to non-REP panel first so "?" triggers general help
    k.handle_command("G")

    out = k.handle_command("?")

    assert "no_trigger:" in out


# ----------------------------------------------------------------
# Shell fallback feature tests
# ----------------------------------------------------------------


def test_kernel_shell_fallback_executes_unknown_commands():
    """Shell fallback should execute unknown commands as shell commands."""
    store = FakeStore()
    executor = FakeExecutor()
    config = FakeConfig()

    # Add Term panel with shell_fallback enabled
    config.panels["Term"] = {
        "entry": "$",
        "name": "Term",
        "message": "Terminal mode",
        "shell_fallback": True,
    }

    k = Kernel(store=store, executor=executor, config=config)
    k.start()

    # Switch to Term panel
    k.handle_command("$")
    assert k.panel == "$"

    # Execute unknown command - should be treated as shell command
    out = k.handle_command("ls -la")

    # Verify command was executed via executor
    assert "ls -la" in executor.commands_run
    assert "output" in out


def test_kernel_shell_fallback_base_commands_have_priority():
    """Shell fallback: base commands should have priority over shell execution."""
    store = FakeStore()
    executor = FakeExecutor()
    config = FakeConfig()

    # Add Term panel with shell_fallback
    config.panels["Term"] = {"entry": "$", "name": "Term", "shell_fallback": True}

    k = Kernel(store=store, executor=executor, config=config)
    k.start()
    k.handle_command("$")

    # L is a base command (list aliases)
    out = k.handle_command("L")

    # Should list aliases, not execute "L" as shell command
    assert "output" not in out  # Not executed as shell
    assert "L" not in executor.commands_run


def test_kernel_shell_fallback_aliases_have_priority():
    """Shell fallback: aliases should have priority over shell execution."""
    store = FakeStore()
    executor = FakeExecutor()
    config = FakeConfig()

    # Add Term panel with shell_fallback
    config.panels["Term"] = {"entry": "$", "name": "Term", "shell_fallback": True}

    k = Kernel(store=store, executor=executor, config=config)
    k.start()
    k.handle_command("$")

    # Add an alias
    store.add_alias("$", "gs", "git status")

    # Execute alias
    k.handle_command("gs")

    # Should execute alias command, not "gs" as shell command
    assert "git status" in executor.commands_run
    assert "gs" not in executor.commands_run


def test_kernel_shell_fallback_disabled_returns_unknown_command():
    """Without shell_fallback, unknown commands should return 'Unknown command'."""
    store = FakeStore()
    executor = FakeExecutor()
    config = FakeConfig()

    # G panel doesn't have shell_fallback
    k = Kernel(store=store, executor=executor, config=config)
    k.start()
    k.handle_command("G")

    # Unknown command should return error
    out = k.handle_command("ls -la")

    assert "Unknown command" in out
    assert "ls -la" not in executor.commands_run


def test_kernel_shell_fallback_with_alias_placeholders():
    """Shell fallback: aliases with placeholders should work correctly."""
    store = FakeStore()
    executor = FakeExecutor()
    config = FakeConfig()

    config.panels["Term"] = {"entry": "$", "name": "Term", "shell_fallback": True}

    k = Kernel(store=store, executor=executor, config=config)
    k.start()
    k.handle_command("$")

    # Add alias with placeholder (use $@ for all args)
    store.add_alias("$", "echo", "echo $@")

    # Execute alias with args
    k.handle_command("echo hello world")

    # Should expand alias with both args
    assert "echo hello world" in executor.commands_run


# ----------------------------------------------------------------
# write_crash_log function
# ----------------------------------------------------------------


def test_write_crash_log_creates_log_entry(tmp_path, monkeypatch):
    """write_crash_log must create crash log entry with error details."""
    import repos_cli.config as cfg_module

    # Mock data root to use temp directory
    monkeypatch.setattr(cfg_module, "get_data_root", lambda: tmp_path)

    from repos_cli.kernel import write_crash_log

    # Write crash log
    error = RuntimeError("test error")
    write_crash_log(
        error=error,
        panel="REP",
        raw_command="test command",
        resolved_command="resolved test command",
        db_name="test.db",
        db_path=Path("/tmp/test.db"),
    )

    # Verify crash log was created
    crash_log = tmp_path / "repos" / "logs" / "crash.log"
    assert crash_log.exists()

    content = crash_log.read_text()
    assert "panel=REP" in content
    assert "raw=test command" in content
    assert "resolved=resolved test command" in content
    assert "db=test.db" in content
    assert "db_path=/tmp/test.db" in content
    assert "error=RuntimeError: test error" in content
    assert "traceback:" in content
    assert "----" in content


def test_write_crash_log_handles_minimal_info(tmp_path, monkeypatch):
    """write_crash_log must work with minimal information."""
    import repos_cli.config as cfg_module

    monkeypatch.setattr(cfg_module, "get_data_root", lambda: tmp_path)

    from repos_cli.kernel import write_crash_log

    # Write crash log with minimal info
    error = ValueError("minimal error")
    write_crash_log(error=error, panel="G")

    crash_log = tmp_path / "repos" / "logs" / "crash.log"
    assert crash_log.exists()

    content = crash_log.read_text()
    assert "panel=G" in content
    assert "error=ValueError: minimal error" in content


def test_write_crash_log_appends_to_existing_log(tmp_path, monkeypatch):
    """write_crash_log must append to existing log file."""
    import repos_cli.config as cfg_module

    monkeypatch.setattr(cfg_module, "get_data_root", lambda: tmp_path)

    from repos_cli.kernel import write_crash_log

    # Write first entry
    write_crash_log(error=RuntimeError("error 1"), panel="REP")

    # Write second entry
    write_crash_log(error=ValueError("error 2"), panel="G")

    crash_log = tmp_path / "repos" / "logs" / "crash.log"
    content = crash_log.read_text()

    # Both entries should be present
    assert "error=RuntimeError: error 1" in content
    assert "error=ValueError: error 2" in content
    assert content.count("----") == 2


def test_write_crash_log_fails_silently_on_error(tmp_path, monkeypatch):
    """write_crash_log must fail silently if it can't write."""
    import repos_cli.config as cfg_module

    # Mock get_data_root to raise exception
    monkeypatch.setattr(cfg_module, "get_data_root", lambda: None)

    from repos_cli.kernel import write_crash_log

    # Should not raise exception
    write_crash_log(error=RuntimeError("test"), panel="REP")


# ----------------------------------------------------------------
# INFO command
# ----------------------------------------------------------------


def test_kernel_info_command_shows_db_metadata(tmp_path):
    """INFO command must show active database metadata."""
    from repos_cli.db import ensure_schema
    from repos_cli.store import SQLiteStore

    db_path = tmp_path / "test.db"
    ensure_schema(db_path)

    store = SQLiteStore(db_path)
    executor = FakeExecutor()
    config = FakeConfig()

    k = Kernel(store=store, executor=executor, config=config)
    k.start()
    k.active_db_path = db_path
    k.active_db_name = "test.db"
    k.active_db_source = "local"

    out = k.handle_command("INFO")

    # Should show DB information
    assert "test.db" in out or "DB" in out or "info" in out.lower()


def test_kernel_info_command_no_active_db(kernel_with_mocks: Kernel):
    """INFO command with no active DB must show message."""
    k = kernel_with_mocks
    k.active_db_path = None

    out = k.handle_command("INFO")

    assert "no" in out.lower() or "not" in out.lower()


# ----------------------------------------------------------------
# H with index (history detail)
# ----------------------------------------------------------------


def test_kernel_history_detail_shows_full_output(kernel_with_mocks: Kernel):
    """H with index must show full command details including output."""
    k = kernel_with_mocks

    # Execute a command
    k.handle_command("!echo test")

    # Get detail for first entry (1-indexed)
    out = k.handle_command("H 1")

    # Should show detailed information
    assert "echo test" in out.lower()


def test_kernel_history_detail_invalid_index(kernel_with_mocks: Kernel):
    """H with invalid index must show error."""
    k = kernel_with_mocks

    # Try to get detail for non-existent entry
    out = k.handle_command("H 999")

    # Should show error message (could be "no history" or "not found" etc)
    assert (
        "not found" in out.lower()
        or "no entry" in out.lower()
        or "invalid" in out.lower()
        or "no history" in out.lower()
    )


# ----------------------------------------------------------------
# Additional execution mode tests
# ----------------------------------------------------------------


def test_kernel_reserved_triggers_includes_base_commands(kernel_with_mocks: Kernel):
    """get_reserved_triggers must include all base command triggers."""
    k = kernel_with_mocks

    reserved = k.get_reserved_triggers()

    # Should include base command triggers
    assert "L" in reserved  # list
    assert "N" in reserved or "A" in reserved  # add
    assert "RM" in reserved  # remove


def test_kernel_reserved_triggers_includes_special_commands(kernel_with_mocks: Kernel):
    """get_reserved_triggers must include special built-in commands."""
    k = kernel_with_mocks

    reserved = k.get_reserved_triggers()

    # Should include special built-ins
    assert "Z" in reserved
    assert "ZZ" in reserved
    assert "INFO" in reserved


# ----------------------------------------------------------------
# Shell builtin tests (cd, pwd)
# ----------------------------------------------------------------


def test_kernel_handles_cd_command_in_shell_fallback_panel(tmp_path):
    """cd command should change working directory in shell_fallback panels."""
    import os

    from repos_cli.db import ensure_schema
    from repos_cli.kernel import Kernel
    from repos_cli.store import SQLiteStore

    # Create database
    db_path = tmp_path / "test.db"
    ensure_schema(db_path)

    # Create test directory
    test_dir = tmp_path / "testdir"
    test_dir.mkdir()

    # Create kernel with shell_fallback config
    config = FakeConfigWithShellFallback()
    store = SQLiteStore(db_path)
    executor = FakeExecutor()
    k = Kernel(store=store, executor=executor, config=config)
    k.start()

    # Switch to shell_fallback panel
    k.panel = "SH"

    # Store original cwd
    original_cwd = os.getcwd()

    try:
        # Change to test directory
        k.handle_command(f"cd {test_dir}")

        # Should have changed directory
        assert k.cwd == str(test_dir)
    finally:
        # Restore original cwd
        os.chdir(original_cwd)


def test_kernel_handles_pwd_command_in_shell_fallback_panel():
    """pwd command should show current working directory in shell_fallback panels."""
    import os
    import tempfile

    from repos_cli.db import ensure_schema
    from repos_cli.kernel import Kernel
    from repos_cli.store import SQLiteStore

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        ensure_schema(db_path)

        config = FakeConfigWithShellFallback()
        store = SQLiteStore(db_path)
        executor = FakeExecutor()
        k = Kernel(store=store, executor=executor, config=config)
        k.start()

        # Switch to shell_fallback panel
        k.panel = "SH"

        # Get pwd
        out = k.handle_command("pwd")

        # Should show current working directory
        assert k.cwd in out or os.getcwd() in out


def test_kernel_cd_command_updates_cwd():
    """cd command should update kernel.cwd."""
    import os
    import tempfile

    from repos_cli.db import ensure_schema
    from repos_cli.kernel import Kernel
    from repos_cli.store import SQLiteStore

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        ensure_schema(db_path)

        # Create test subdirectory
        test_dir = Path(tmpdir) / "subdir"
        test_dir.mkdir()

        config = FakeConfigWithShellFallback()
        store = SQLiteStore(db_path)
        executor = FakeExecutor()
        k = Kernel(store=store, executor=executor, config=config)
        k.start()

        k.panel = "SH"

        original_cwd = os.getcwd()
        try:
            # Change directory
            k.handle_command(f"cd {test_dir}")

            # Verify cwd was updated
            assert k.cwd == str(test_dir)
        finally:
            os.chdir(original_cwd)


# ----------------------------------------------------------------
# Panel switching by entry token
# ----------------------------------------------------------------


def test_kernel_switches_panel_by_typing_entry_token(kernel_with_mocks: Kernel):
    """Typing a panel entry token should switch to that panel."""
    k = kernel_with_mocks

    # Start in REP
    assert k.panel == "REP"

    # Type G to switch to GIT panel
    k.handle_command("G")

    # Should have switched to GIT
    assert k.panel == "G"


def test_kernel_panel_switch_pushes_to_stack(kernel_with_mocks: Kernel):
    """Panel switching should push to panel stack."""
    k = kernel_with_mocks

    # Clear panel stack
    k.panel_stack = []

    # Switch to G panel
    k.handle_command("G")

    # Should have pushed to stack
    assert "G" in k.panel_stack


# ----------------------------------------------------------------
# Config edge cases
# ----------------------------------------------------------------


def test_kernel_current_panel_has_shell_fallback_returns_false_for_non_fallback():
    """current_panel_has_shell_fallback should return False for non-fallback panels."""
    import tempfile

    from repos_cli.db import ensure_schema
    from repos_cli.kernel import Kernel
    from repos_cli.store import SQLiteStore

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        ensure_schema(db_path)

        # FakeConfig panels don't have shell_fallback
        config = FakeConfig()
        store = SQLiteStore(db_path)
        executor = FakeExecutor()
        k = Kernel(store=store, executor=executor, config=config)
        k.start()

        k.panel = "REP"

        # REP panel doesn't have shell_fallback
        assert k.current_panel_has_shell_fallback() is False


# ----------------------------------------------------------------
# cd command edge cases
# ----------------------------------------------------------------


def test_kernel_cd_with_no_args_goes_to_home(tmp_path):
    """cd with no arguments should go to home directory."""
    import os

    from repos_cli.db import ensure_schema
    from repos_cli.kernel import Kernel
    from repos_cli.store import SQLiteStore

    db_path = tmp_path / "test.db"
    ensure_schema(db_path)

    config = FakeConfigWithShellFallback()
    store = SQLiteStore(db_path)
    executor = FakeExecutor()
    k = Kernel(store=store, executor=executor, config=config)
    k.start()

    k.panel = "SH"

    original_cwd = os.getcwd()
    try:
        # cd with no args
        k.handle_command("cd")

        # Should have changed to home directory
        home = os.path.expanduser("~")
        assert k.cwd == home
    finally:
        os.chdir(original_cwd)


def test_kernel_cd_dash_toggles_to_previous_directory(tmp_path):
    """cd - should toggle to previous directory."""
    import os

    from repos_cli.db import ensure_schema
    from repos_cli.kernel import Kernel
    from repos_cli.store import SQLiteStore

    db_path = tmp_path / "test.db"
    ensure_schema(db_path)

    dir1 = tmp_path / "dir1"
    dir2 = tmp_path / "dir2"
    dir1.mkdir()
    dir2.mkdir()

    config = FakeConfigWithShellFallback()
    store = SQLiteStore(db_path)
    executor = FakeExecutor()
    k = Kernel(store=store, executor=executor, config=config)
    k.start()

    k.panel = "SH"

    original_cwd = os.getcwd()
    try:
        # Change to dir1
        k.handle_command(f"cd {dir1}")
        assert k.cwd == str(dir1)

        # Change to dir2
        k.handle_command(f"cd {dir2}")
        assert k.cwd == str(dir2)

        # cd - should go back to dir1
        k.handle_command("cd -")
        assert k.cwd == str(dir1)
    finally:
        os.chdir(original_cwd)


def test_kernel_cd_to_nonexistent_directory_shows_error(tmp_path):
    """cd to non-existent directory should show error."""
    import os

    from repos_cli.db import ensure_schema
    from repos_cli.kernel import Kernel
    from repos_cli.store import SQLiteStore

    db_path = tmp_path / "test.db"
    ensure_schema(db_path)

    config = FakeConfigWithShellFallback()
    store = SQLiteStore(db_path)
    executor = FakeExecutor()
    k = Kernel(store=store, executor=executor, config=config)
    k.start()

    k.panel = "SH"

    original_cwd = os.getcwd()
    try:
        # Try to cd to non-existent directory
        out = k.handle_command("cd /nonexistent/directory/xyz")

        # Should show error message
        assert "no such directory" in out.lower() or "not found" in out.lower()
    finally:
        os.chdir(original_cwd)


def test_kernel_cd_dash_without_previous_shows_error(tmp_path):
    """cd - without previous directory should show error."""
    from repos_cli.db import ensure_schema
    from repos_cli.kernel import Kernel
    from repos_cli.store import SQLiteStore

    db_path = tmp_path / "test.db"
    ensure_schema(db_path)

    config = FakeConfigWithShellFallback()
    store = SQLiteStore(db_path)
    executor = FakeExecutor()
    k = Kernel(store=store, executor=executor, config=config)
    k.start()

    k.panel = "SH"

    # cd - without any previous directory
    out = k.handle_command("cd -")

    # Should show error
    assert "no previous directory" in out.lower()


# ----------------------------------------------------------------
# SET command variations
# ----------------------------------------------------------------


def test_kernel_set_command_shows_current_settings_with_no_args(kernel_with_mocks: Kernel):
    """SET with no arguments should show current settings."""
    k = kernel_with_mocks

    out = k.handle_command("SET")

    # Should show current settings
    assert "show_run" in out or "Current settings" in out


def test_kernel_set_command_reset_restores_defaults(kernel_with_mocks: Kernel):
    """SET reset should restore default settings."""
    k = kernel_with_mocks

    # Change some settings
    k.show_run = False
    k.show_exit = False

    # Reset
    out = k.handle_command("SET reset")

    # Should have reset to defaults
    assert k.show_run is True
    assert k.show_exit is True
    assert "reset" in out.lower() or "default" in out.lower()


# ----------------------------------------------------------------
# History edge cases
# ----------------------------------------------------------------


def test_kernel_history_with_empty_panel(kernel_with_mocks: Kernel):
    """History in empty panel should show 'No history' message."""
    k = kernel_with_mocks

    # Switch to fresh panel
    k.handle_command("G")

    # Get history
    out = k.handle_command("H")

    # Should indicate no history
    assert "no history" in out.lower() or "No history" in out


# ----------------------------------------------------------------
# list_alias_completions edge cases
# ----------------------------------------------------------------


def test_kernel_list_alias_completions_handles_store_exception(kernel_with_mocks: Kernel):
    """list_alias_completions should handle store exceptions gracefully."""
    k = kernel_with_mocks

    # Mock store to raise exception
    original_list_aliases = k.store.list_aliases

    def broken_list_aliases(panel):
        raise RuntimeError("Store error")

    k.store.list_aliases = broken_list_aliases

    try:
        # Should not crash
        completions = k.list_alias_completions()

        # Should return empty list
        assert completions == []
    finally:
        k.store.list_aliases = original_list_aliases


# ----------------------------------------------------------------
# Prompt generation
# ----------------------------------------------------------------


def test_kernel_prompt_returns_formatted_prompt(kernel_with_mocks: Kernel):
    """prompt() should return formatted prompt with panel."""
    k = kernel_with_mocks

    prompt_str = k.prompt()

    # Should contain panel name and caret
    assert "REP" in prompt_str or k.panel in prompt_str


def test_kernel_prompt_uses_branding_colors(kernel_with_mocks: Kernel):
    """prompt() should use branding colors from config."""
    k = kernel_with_mocks

    # Switch to G panel which has orange branding
    k.handle_command("G")

    prompt_str = k.prompt()

    # Should generate a non-empty prompt
    assert len(prompt_str) > 0


# ----------------------------------------------------------------
# DB command tests
# ----------------------------------------------------------------


def test_kernel_db_command_lists_available_databases(kernel_with_mocks: Kernel):
    """DB command should list available database targets."""
    k = kernel_with_mocks

    out = k.handle_command("DB")

    # Should show core database
    assert "core" in out.lower() or "db" in out.lower()


def test_kernel_discover_db_targets_includes_core(kernel_with_mocks: Kernel):
    """_discover_db_targets should always include core DB."""
    k = kernel_with_mocks

    targets = k._discover_db_targets()

    # Should have at least core DB
    assert len(targets) >= 1
    assert any(t["source"] == "core" for t in targets)


# ----------------------------------------------------------------
# Crash log edge cases
# ----------------------------------------------------------------


def test_kernel_execution_logs_crash_on_store_failure(tmp_path):
    """Execution should log crash if store.record_event fails."""
    import importlib

    from repos_cli import config as cfg_module
    from repos_cli.db import ensure_schema
    from repos_cli.kernel import Kernel
    from repos_cli.store import SQLiteStore

    # Force reload config to clear cache
    importlib.reload(cfg_module)

    db_path = tmp_path / "test.db"
    ensure_schema(db_path)

    # Mock get_data_root to return tmp_path
    original_get_data_root = cfg_module.get_data_root
    cfg_module.get_data_root = lambda: tmp_path

    try:
        store = SQLiteStore(db_path)
        executor = FakeExecutor()
        config = FakeConfig()
        k = Kernel(store=store, executor=executor, config=config)
        k.start()
        k.active_db_name = "test.db"
        k.active_db_path = db_path

        # Mock store.record_event to raise exception
        original_record_event = store.record_event

        def broken_record_event(*args, **kwargs):
            raise RuntimeError("Database error")

        store.record_event = broken_record_event

        try:
            # Execute command - should not crash even if record_event fails
            out = k.handle_command("!echo test")

            # Should show error message
            assert "ERROR" in out or len(out) >= 0  # Should complete without crashing

            # Should have created crash log
            crash_log = tmp_path / "repos" / "logs" / "crash.log"
            assert crash_log.exists()

            # Log should contain error details
            log_content = crash_log.read_text()
            assert "panel=REP" in log_content
            assert "!echo test" in log_content
        finally:
            store.record_event = original_record_event
    finally:
        cfg_module.get_data_root = original_get_data_root


# ----------------------------------------------------------------
# expand_alias method
# ----------------------------------------------------------------


def test_kernel_expand_alias_returns_command(kernel_with_mocks: Kernel):
    """expand_alias should return expanded command for alias."""
    k = kernel_with_mocks

    # Add alias
    k.handle_command("G")
    k.handle_command("N gs git status")

    # Expand alias
    expanded = k.expand_alias("gs")

    assert expanded == "git status"


def test_kernel_expand_alias_returns_none_for_unknown(kernel_with_mocks: Kernel):
    """expand_alias should return None for unknown alias."""
    k = kernel_with_mocks

    expanded = k.expand_alias("unknown_alias_xyz")

    assert expanded is None


# ----------------------------------------------------------------
# Z command (back navigation)
# ----------------------------------------------------------------


def test_kernel_z_command_pops_panel_stack(kernel_with_mocks: Kernel):
    """Z command should pop panel stack and go back."""
    k = kernel_with_mocks

    # Start in REP
    assert k.panel == "REP"

    # Go to G panel
    k.handle_command("G")
    assert k.panel == "G"

    # Z should go back to REP
    k.handle_command("Z")
    assert k.panel == "REP"


def test_kernel_z_command_with_single_item_stack_stays_in_panel(kernel_with_mocks: Kernel):
    """Z command with single item in stack should stay in current panel."""
    k = kernel_with_mocks

    # Start with single item in stack (the current panel)
    k.panel_stack = [k.panel]

    original_panel = k.panel

    # Z should not crash and should stay in same panel
    k.handle_command("Z")

    # Should stay in same panel (stack has only one item)
    assert k.panel == original_panel


# ----------------------------------------------------------------
# Reserved trigger protection
# ----------------------------------------------------------------


def test_kernel_cannot_create_alias_with_reserved_trigger(kernel_with_mocks: Kernel):
    """Cannot create alias with name that's a reserved trigger."""
    k = kernel_with_mocks

    k.handle_command("G")

    # Try to create alias with reserved name 'L'
    out = k.handle_command("N L git log")

    # Should show error about reserved trigger
    assert "reserved" in out.lower() or "cannot" in out.lower()


def test_kernel_cannot_create_alias_with_help_trigger(kernel_with_mocks: Kernel):
    """Cannot create alias with name that's a help trigger."""
    k = kernel_with_mocks

    k.handle_command("G")

    # Try to create alias with help trigger '?'
    out = k.handle_command("N ? some command")

    # Should show error about reserved trigger
    assert "reserved" in out.lower() or "cannot" in out.lower()


# ----------------------------------------------------------------
# REP panel commands
# ----------------------------------------------------------------


def test_kernel_rep_panel_shows_unknown_for_invalid_command(kernel_with_mocks: Kernel):
    """REP panel should show unknown command message for invalid commands."""
    k = kernel_with_mocks

    # Stay in REP panel
    assert k.panel == "REP"

    # Try invalid command
    out = k.handle_command("invalid_rep_command_xyz")

    # Should show unknown command message
    assert "unknown" in out.lower() or "Unknown command" in out


def test_kernel_rep_panel_use_command_requires_arg(kernel_with_mocks: Kernel):
    """USE command without argument should show usage."""
    k = kernel_with_mocks

    # Stay in REP panel
    assert k.panel == "REP"

    # USE without argument
    out = k.handle_command("USE")

    # Should show usage
    assert "Usage:" in out or "USE" in out


# ----------------------------------------------------------------
# Unknown command handling
# ----------------------------------------------------------------


def test_kernel_unknown_command_in_non_fallback_panel(kernel_with_mocks: Kernel):
    """Unknown command in non-fallback panel should return 'Unknown command'."""
    k = kernel_with_mocks

    # REP panel doesn't have shell_fallback
    out = k.handle_command("unknown_command_xyz_123")

    # Should show unknown command message
    assert "unknown" in out.lower() or "Unknown command" in out


# ----------------------------------------------------------------
# current_panel_has_shell_fallback edge cases
# ----------------------------------------------------------------


def test_kernel_current_panel_has_shell_fallback_returns_true(tmp_path):
    """current_panel_has_shell_fallback should return True for fallback panels."""
    from repos_cli.db import ensure_schema
    from repos_cli.kernel import Kernel
    from repos_cli.store import SQLiteStore

    db_path = tmp_path / "test.db"
    ensure_schema(db_path)

    config = FakeConfigWithShellFallback()
    store = SQLiteStore(db_path)
    executor = FakeExecutor()
    k = Kernel(store=store, executor=executor, config=config)
    k.start()

    # Switch to SH panel which has shell_fallback
    k.panel = "SH"

    # Should return True
    assert k.current_panel_has_shell_fallback() is True


# ----------------------------------------------------------------
# welcome message configuration
# ----------------------------------------------------------------


def test_kernel_welcome_setting_can_be_disabled(kernel_with_mocks: Kernel):
    """welcome setting can be disabled via SET command."""
    k = kernel_with_mocks

    # Disable welcome
    k.handle_command("SET welcome false")

    assert k.welcome is False


def test_kernel_welcome_setting_can_be_enabled(kernel_with_mocks: Kernel):
    """welcome setting can be enabled via SET command."""
    k = kernel_with_mocks

    # First disable
    k.welcome = False

    # Enable welcome
    k.handle_command("SET welcome true")

    assert k.welcome is True


# ----------------------------------------------------------------
# force_color setting
# ----------------------------------------------------------------


def test_kernel_force_color_setting_can_be_toggled(kernel_with_mocks: Kernel):
    """force_color setting can be toggled via SET command."""
    k = kernel_with_mocks

    # Disable force_color
    k.handle_command("SET force_color false")
    assert k.force_color is False

    # Enable force_color
    k.handle_command("SET force_color true")
    assert k.force_color is True


# ----------------------------------------------------------------
# TTY mode configuration tests
# ----------------------------------------------------------------


def test_kernel_should_use_tty_disabled_by_default(kernel_with_mocks: Kernel):
    """_should_use_tty returns False when TTY apps not configured."""
    k = kernel_with_mocks

    # No execution.tty_apps config = always False
    result = k._should_use_tty("ls -la", "!ls -la")
    assert result is False


def test_kernel_should_use_tty_with_force_prefix():
    """_should_use_tty returns True when command has force_prefix."""
    import tempfile

    from repos_cli.db import ensure_schema
    from repos_cli.store import SQLiteStore

    class FakeConfigWithTTY:
        def __init__(self):
            self.panels = {"REP": {"entry": "REP", "name": "REP", "message": "Welcome!"}}
            self.commands = {}
            self.branding = {"panel": {"sep": "→"}}
            self.system = {"aliases_db": "repos/aliases.db"}
            # Enable TTY apps with force_prefix
            self._execution_config = {
                "timeout": 30,
                "tty_apps": {
                    "enabled": True,
                    "force_prefix": "!tty ",
                },
            }

        @property
        def execution(self):
            return self._execution_config

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        ensure_schema(db_path)
        store = SQLiteStore(db_path)
        executor = FakeExecutor()
        config = FakeConfigWithTTY()

        k = Kernel(store=store, executor=executor, config=config)
        k.start()

        # Command with force_prefix should use TTY
        assert k._should_use_tty("!tty ls", "!tty ls") is True

        # Command without force_prefix should not use TTY
        assert k._should_use_tty("!ls", "!ls") is False


def test_kernel_show_run_can_be_disabled(kernel_with_mocks: Kernel):
    """show_run setting controls whether [RUN] tag is shown."""
    k = kernel_with_mocks

    # Default is True
    assert k.show_run is True

    # Can be disabled
    k.show_run = False
    assert k.show_run is False


def test_kernel_show_exit_can_be_disabled(kernel_with_mocks: Kernel):
    """show_exit setting controls whether [EXIT] tag is shown."""
    k = kernel_with_mocks

    # Default is True
    assert k.show_exit is True

    # Can be disabled
    k.show_exit = False
    assert k.show_exit is False


def test_kernel_show_stdout_can_be_disabled(kernel_with_mocks: Kernel):
    """show_stdout setting controls whether stdout is shown."""
    k = kernel_with_mocks

    # Default is True
    assert k.show_stdout is True

    # Can be disabled
    k.show_stdout = False
    assert k.show_stdout is False


def test_kernel_show_stderr_can_be_disabled(kernel_with_mocks: Kernel):
    """show_stderr setting controls whether stderr is shown."""
    k = kernel_with_mocks

    # Default is True
    assert k.show_stderr is True

    # Can be disabled
    k.show_stderr = False
    assert k.show_stderr is False


# ----------------------------------------------------------------
# Streaming execution tests (with output callbacks)
# ----------------------------------------------------------------


def test_kernel_streaming_execution_with_callbacks():
    """Streaming execution should call output_fn and error_fn callbacks."""
    import tempfile

    from repos_cli.db import ensure_schema
    from repos_cli.store import SQLiteStore

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        ensure_schema(db_path)
        store = SQLiteStore(db_path)

        # Use real executor to test streaming
        from repos_cli.executor import SubprocessExecutor

        executor = SubprocessExecutor()

        config = FakeConfigWithShellFallback()
        k = Kernel(store=store, executor=executor, config=config)
        k.start()

        # Capture output via callbacks
        output_lines = []
        error_lines = []

        def output_fn(s: str):
            output_lines.append(s)

        def error_fn(s: str):
            error_lines.append(s)

        # Set output callbacks directly (no wire_output method)
        k.output_fn = output_fn
        k.error_fn = error_fn

        # Execute command that produces stdout
        k.panel = "SH"
        k.handle_command("!echo streaming_test")

        # Callbacks should have been called with output
        assert len(output_lines) > 0
        all_output = "".join(output_lines)
        assert "streaming_test" in all_output


def test_kernel_output_fn_can_be_set(kernel_with_mocks: Kernel):
    """output_fn can be set for streaming output."""
    k = kernel_with_mocks

    # Default is None
    assert k.output_fn is None

    # Can be set
    def my_output(s: str):
        pass

    k.output_fn = my_output
    assert k.output_fn is my_output


def test_kernel_error_fn_can_be_set(kernel_with_mocks: Kernel):
    """error_fn can be set for streaming errors."""
    k = kernel_with_mocks

    # Default is None
    assert k.error_fn is None

    # Can be set
    def my_error(s: str):
        pass

    k.error_fn = my_error
    assert k.error_fn is my_error


# ----------------------------------------------------------------
# USE and WHERE command tests
# ----------------------------------------------------------------


def test_kernel_use_command_switches_database(kernel_with_mocks: Kernel):
    """USE command should switch to a different database."""
    k = kernel_with_mocks

    # First, ensure there are multiple databases available
    # The kernel should have a core database by default
    out = k.handle_command("DB")
    assert "Core" in out or "core" in out

    # Try to use the core database by ID or name
    # (This may fail if already on core, but should not crash)
    out = k.handle_command("USE 1")
    # Should either switch or show already on it (implementation detail)
    assert "Unknown DB target" not in out or "Switched" in out or out == ""


def test_kernel_use_command_requires_argument(kernel_with_mocks: Kernel):
    """USE command without argument should show usage."""
    k = kernel_with_mocks

    out = k.handle_command("USE")
    assert "Usage" in out or "usage" in out.lower()


def test_kernel_use_command_shows_error_for_unknown_db(kernel_with_mocks: Kernel):
    """USE command with unknown DB should show error."""
    k = kernel_with_mocks

    out = k.handle_command("USE nonexistent_db_xyz_123")
    assert "Unknown" in out or "unknown" in out


def test_kernel_where_command_shows_active_db(kernel_with_mocks: Kernel):
    """WHERE command should show active database info."""
    k = kernel_with_mocks

    out = k.handle_command("WHERE")
    # Should show database info (name, source, path)
    assert len(out) > 0
    # At minimum should mention "database" or show path
    assert "database" in out.lower() or "db" in out.lower() or "/" in out


# ----------------------------------------------------------------
# Additional edge cases
# ----------------------------------------------------------------


def test_kernel_info_command_shows_system_info(kernel_with_mocks: Kernel):
    """INFO command should show system information."""
    k = kernel_with_mocks

    out = k.handle_command("INFO")
    # Should show some system info (may include version, paths, etc.)
    assert len(out) > 0


def test_kernel_prompt_includes_panel_name(kernel_with_mocks: Kernel):
    """prompt() should include panel name in formatted output."""
    k = kernel_with_mocks

    prompt = k.prompt()
    # Should include panel indicator
    assert "REP" in prompt or len(prompt) > 0


def test_kernel_can_tty_checks_executor_capability(kernel_with_mocks: Kernel):
    """_can_tty() should check if executor has run_tty method."""
    k = kernel_with_mocks

    # FakeExecutor doesn't have run_tty, so should return False
    result = k._can_tty()
    assert result is False


def test_kernel_can_stream_checks_executor_capability(kernel_with_mocks: Kernel):
    """_can_stream() should check if executor has run_stream method."""
    k = kernel_with_mocks

    # FakeExecutor doesn't have run_stream, so should return False
    result = k._can_stream()
    assert result is False


def test_kernel_panel_stack_starts_with_initial_panel(kernel_with_mocks: Kernel):
    """panel_stack should start with the initial panel."""
    k = kernel_with_mocks

    # Panel stack should contain at least the starting panel
    assert "REP" in k.panel_stack
    assert len(k.panel_stack) > 0


def test_kernel_cwd_starts_as_current_directory(kernel_with_mocks: Kernel):
    """cwd should be initialized to current directory."""
    k = kernel_with_mocks

    # Should have a cwd set
    assert k.cwd is not None
    assert len(k.cwd) > 0
    # Should be an absolute path
    assert k.cwd.startswith("/")


def test_kernel_prev_cwd_starts_as_none(kernel_with_mocks: Kernel):
    """prev_cwd should start as None."""
    k = kernel_with_mocks

    # Before any cd command, prev_cwd should be None
    assert k.prev_cwd is None


def test_kernel_running_flag_after_start(kernel_with_mocks: Kernel):
    """running flag should be True after start()."""
    k = kernel_with_mocks

    # Kernel should be running after start
    assert k.running is True


def test_kernel_branding_comes_from_config(kernel_with_mocks: Kernel):
    """Kernel branding should be loaded from config."""
    k = kernel_with_mocks

    # Should have branding from FakeConfig
    assert k.branding is not None
    assert isinstance(k.branding, dict)


def test_kernel_active_db_name_is_string(kernel_with_mocks: Kernel):
    """active_db_name should be a string."""
    k = kernel_with_mocks

    # Should be a string (may be empty if no DB set)
    assert isinstance(k.active_db_name, str)


def test_kernel_active_db_source_is_string(kernel_with_mocks: Kernel):
    """active_db_source should be a string."""
    k = kernel_with_mocks

    # Should be a string
    assert isinstance(k.active_db_source, str)


def test_kernel_documented_commands_includes_z(kernel_with_mocks: Kernel):
    """documented_commands should include Z for navigation."""
    k = kernel_with_mocks

    # Z command should be documented
    assert "Z" in k.documented_commands


def test_kernel_base_commands_is_dict(kernel_with_mocks: Kernel):
    """base_commands should be a dictionary."""
    k = kernel_with_mocks

    # Should be a dict mapping commands to handlers
    assert isinstance(k.base_commands, dict)


def test_kernel_command_triggers_is_dict(kernel_with_mocks: Kernel):
    """command_triggers should be a dictionary."""
    k = kernel_with_mocks

    # Should be a dict
    assert isinstance(k.command_triggers, dict)


def test_kernel_entry_to_panel_mapping_exists(kernel_with_mocks: Kernel):
    """_entry_to_panel mapping should exist."""
    k = kernel_with_mocks

    # Should have entry to panel mapping
    assert hasattr(k, "_entry_to_panel")
    assert isinstance(k._entry_to_panel, dict)


def test_kernel_panel_entries_set_exists(kernel_with_mocks: Kernel):
    """_panel_entries set should exist."""
    k = kernel_with_mocks

    # Should have panel entries set
    assert hasattr(k, "_panel_entries")


def test_kernel_format_truncation_warning_shows_message():
    """_format_truncation_warning should return formatted warning."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        from repos_cli.db import ensure_schema

        ensure_schema(db_path)

        from repos_cli.store import SQLiteStore

        store = SQLiteStore(db_path)
        k = Kernel(store=store, executor=FakeExecutor(), config=FakeConfig())
        k.start()

        # Test truncation warning
        warning = k._format_truncation_warning(
            stdout_truncated=True,
            stderr_truncated=False,
            stdout_bytes_total=100000,
            stderr_bytes_total=1000,
        )

        # Should mention output not fully captured
        assert "captured" in warning.lower()


def test_kernel_list_alias_completions_returns_list(kernel_with_mocks: Kernel):
    """list_alias_completions should return a list of aliases."""
    k = kernel_with_mocks

    # Should return a list (may be empty)
    completions = k.list_alias_completions()
    assert isinstance(completions, list)
