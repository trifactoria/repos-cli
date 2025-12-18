# tests/test_cli.py
from __future__ import annotations

import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import repos_cli.cli as cli
from repos_cli.config import UI_CLEAR
from repos_cli.kernel import Kernel


@dataclass
class FakeUI:
    """
    UI abstraction used by CLI:
      - read(prompt) -> str
      - write(text) -> None
      - clear() -> None
    """

    inputs: list[str]
    outputs: list[str] = field(default_factory=list)
    clears: int = 0
    prompts: list[str] = field(default_factory=list)

    def read(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.inputs:
            raise EOFError
        return self.inputs.pop(0)

    def write(self, text: str) -> None:
        self.outputs.append(text)

    def clear(self) -> None:
        self.clears += 1


# -------------------------------------------------------------------
# Helper: Create fake dependencies for Kernel
# -------------------------------------------------------------------


class FakeStore:
    def __init__(self):
        self.aliases = {}
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
                "created_at": "2025-12-14",
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
                "started_at": "2025-12-14",
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
    def __init__(self):
        self.commands_run = []

    def run(self, command: str) -> tuple[int, str, str, str, int]:
        self.commands_run.append(command)
        return (0, "output\n", "", "2025-12-14T10:00:00", 100)


class FakeConfig:
    def __init__(self):
        self.panels = {
            "REP": {"entry": "REP", "name": "REP", "message": "Welcome to RepOS!"},
            "G": {"entry": "G", "name": "Git", "message": "Git panel"},
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


def make_kernel() -> Kernel:
    """Create a kernel with fake dependencies."""
    return Kernel(store=FakeStore(), executor=FakeExecutor(), config=FakeConfig())


# -------------------------------------------------------------------
# run_repl behavior
# -------------------------------------------------------------------


def test_cli_current_loop_skips_blank_and_writes_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Covers current cli.run_repl behavior (input_fn/output_fn loop).
    """
    k = make_kernel()
    k.running = True

    calls = {"handled": 0}

    def fake_prompt() -> str:
        return "REP>"

    def fake_handle(line: str) -> str:
        calls["handled"] += 1
        if line == "ZZ":
            k.running = False
            return "bye"
        return "ok"

    monkeypatch.setattr(k, "prompt", fake_prompt)
    monkeypatch.setattr(k, "handle_command", fake_handle)

    inputs = ["   ", "cmd", "ZZ"]
    outputs: list[str] = []

    def input_fn(prompt: str) -> str:
        return inputs.pop(0)

    def output_fn(text: str) -> None:
        outputs.append(text)

    cli.run_repl(k, input_fn=input_fn, output_fn=output_fn)
    assert calls["handled"] == 2  # blank skipped, cmd + ZZ handled
    assert outputs == ["ok", "bye"]


def test_cli_keyboard_interrupt_prints_bye_no_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Covers KeyboardInterrupt path in cli.run_repl (no UI).
    """
    k = make_kernel()
    k.running = True

    monkeypatch.setattr(k, "prompt", lambda: "REP>")

    def input_fn(_: str) -> str:
        raise KeyboardInterrupt

    out: list[str] = []
    cli.run_repl(k, input_fn=input_fn, output_fn=out.append)
    assert any("Bye!" in s for s in out)


def test_cli_keyboard_interrupt_writes_bye_with_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Covers KeyboardInterrupt/EOFError path in cli.run_repl (UI mode).
    """
    k = make_kernel()
    k.running = True
    monkeypatch.setattr(k, "prompt", lambda: "REP>")

    class KIUI(FakeUI):
        def read(self, prompt: str) -> str:
            raise KeyboardInterrupt

    ui = KIUI(inputs=[])
    cli.run_repl(k, ui=ui)
    assert any("Bye!" in s for s in ui.outputs)


def test_cli_requires_ui_wiring_api() -> None:
    """
    CLI must be able to run with a UI object, not just input()/print().
    """
    sig = inspect.signature(cli.run_repl)
    assert "ui" in sig.parameters, "Expected cli.run_repl(kernel, ui=...) after UI split"


def test_cli_routes_clear_to_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    If Kernel returns UI_CLEAR, CLI must call ui.clear() exactly once.
    """
    k = make_kernel()
    k.running = True
    monkeypatch.setattr(k, "prompt", lambda: "REP>")

    def fake_handle(line: str) -> str:
        if line == "clear":
            return UI_CLEAR
        if line == "ZZ":
            k.running = False
            return ""
        return "ok"

    monkeypatch.setattr(k, "handle_command", fake_handle)

    ui = FakeUI(inputs=["clear", "ZZ"])
    cli.run_repl(k, ui=ui)

    assert ui.clears == 1


def test_cli_routes_clear_to_ansi_when_no_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Covers fallback clear behavior: if Kernel returns UI_CLEAR and no UI is provided,
    cli.run_repl must emit ANSI clear codes via output_fn.
    """
    k = make_kernel()
    k.running = True
    monkeypatch.setattr(k, "prompt", lambda: "REP>")

    def fake_handle(line: str) -> str:
        if line == "clear":
            return UI_CLEAR
        if line == "ZZ":
            k.running = False
            return ""
        return "ok"

    monkeypatch.setattr(k, "handle_command", fake_handle)

    inputs = ["clear", "ZZ"]
    outputs: list[str] = []

    def input_fn(prompt: str) -> str:
        return inputs.pop(0)

    cli.run_repl(k, input_fn=input_fn, output_fn=outputs.append)

    assert any("\033[2J\033[H" in s for s in outputs)


def test_cli_with_ui_reads_and_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Covers UI read/write routing in cli.run_repl.
    """
    k = make_kernel()
    k.running = True
    monkeypatch.setattr(k, "prompt", lambda: "REP>")

    def fake_handle(line: str) -> str:
        if line == "ZZ":
            k.running = False
            return "bye"
        return "ok"

    monkeypatch.setattr(k, "handle_command", fake_handle)

    ui = FakeUI(inputs=["cmd", "ZZ"])
    cli.run_repl(k, ui=ui)

    assert ui.outputs == ["ok", "bye"]
    assert ui.prompts and "REP>" in ui.prompts[0]


# Test EOFError in UI mode
def test_cli_eoferror_with_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that EOFError in UI mode triggers goodbye message."""
    k = make_kernel()
    k.running = True
    monkeypatch.setattr(k, "prompt", lambda: "REP>")

    ui = FakeUI(inputs=[])  # Empty inputs causes EOFError
    cli.run_repl(k, ui=ui)
    assert any("Bye!" in s for s in ui.outputs)


# -------------------------------------------------------------------
# cli.main: UI vs legacy behavior
# -------------------------------------------------------------------


def test_cli_main_writes_kernel_start_output_without_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    UI mode:
      - Kernel.start(include_prompt=False) output is written once via UI
      - output must not contain prompt
    """
    monkeypatch.setenv("REPOS_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("REPOS_LEGACY_UI", raising=False)

    created: dict[str, object] = {}

    class StubUI:
        def __init__(self, kernel: Kernel) -> None:
            created["ui"] = self
            self.outputs: list[str] = []

        def write(self, text: str) -> None:
            self.outputs.append(text)

        def read(self, prompt: str) -> str:
            raise EOFError

        def clear(self) -> None:
            pass

    class StubKernel:
        def __init__(self, store=None, executor=None, config=None) -> None:
            self.running = False

        def start(self, include_prompt: bool = False) -> str:
            assert include_prompt is False
            return "Welcome to RepOS!"

    monkeypatch.setattr(cli, "Kernel", StubKernel, raising=True)
    monkeypatch.setattr(cli, "PromptToolkitUI", StubUI, raising=True)
    monkeypatch.setattr(cli, "run_repl", lambda *args, **kwargs: None, raising=True)
    monkeypatch.setattr(sys, "argv", ["repos"])

    cli.main()

    ui = created["ui"]
    combined = "\n".join(ui.outputs)
    assert "Welcome to" in combined
    assert "REP>" not in combined


def test_cli_main_legacy_mode_prints_start_including_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    Legacy mode:
      - Kernel.start(include_prompt=False) is used (prompt comes from ui.read())
      - output is printed via print()
    """
    monkeypatch.setenv("REPOS_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("REPOS_LEGACY_UI", "1")

    printed = []

    def fake_print(text: str) -> None:
        printed.append(text)

    class StubKernel:
        def __init__(self, store=None, executor=None, config=None) -> None:
            self.running = False

        def start(self, include_prompt: bool = False) -> str:
            assert include_prompt is False
            return "Welcome to RepOS!"

    monkeypatch.setattr(cli, "Kernel", StubKernel, raising=True)
    monkeypatch.setattr(cli, "run_repl", lambda *args, **kwargs: None, raising=True)
    monkeypatch.setattr("builtins.print", fake_print, raising=False)
    monkeypatch.setattr(sys, "argv", ["repos"])

    cli.main()

    combined = "\n".join(printed)
    assert "Welcome to" in combined


def test_cli_main_legacy_mode_does_not_print_when_start_output_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    Legacy mode: if Kernel.start() returns empty string, print() should not be called.
    """
    monkeypatch.setenv("REPOS_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("REPOS_LEGACY_UI", "1")

    printed = []

    def fake_print(text: str) -> None:
        printed.append(text)

    class StubKernel:
        def __init__(self, store=None, executor=None, config=None) -> None:
            self.running = False

        def start(self, include_prompt: bool = False) -> str:
            return ""

    monkeypatch.setattr(cli, "Kernel", StubKernel, raising=True)
    monkeypatch.setattr(cli, "run_repl", lambda *args, **kwargs: None, raising=True)
    monkeypatch.setattr("builtins.print", fake_print, raising=False)
    monkeypatch.setattr(sys, "argv", ["repos"])

    cli.main()

    assert not printed


def test_cli_main_ui_mode_does_not_write_when_start_output_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    UI mode: if Kernel.start() returns empty string, UI.write() should not be called.
    """
    monkeypatch.setenv("REPOS_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("REPOS_LEGACY_UI", raising=False)

    created: dict[str, object] = {}

    class StubUI:
        def __init__(self, kernel: Kernel) -> None:
            created["ui"] = self
            self.outputs: list[str] = []

        def write(self, text: str) -> None:
            self.outputs.append(text)

        def read(self, prompt: str) -> str:
            raise EOFError

        def clear(self) -> None:
            pass

    class StubKernel:
        def __init__(self, store=None, executor=None, config=None) -> None:
            self.running = False

        def start(self, include_prompt: bool = False) -> str:
            return ""

    monkeypatch.setattr(cli, "Kernel", StubKernel, raising=True)
    monkeypatch.setattr(cli, "PromptToolkitUI", StubUI, raising=True)
    monkeypatch.setattr(cli, "run_repl", lambda *args, **kwargs: None, raising=True)
    monkeypatch.setattr(sys, "argv", ["repos"])

    cli.main()

    ui = created["ui"]
    assert not ui.outputs


def test_cli_main_calls_ensure_active_db_on_repos(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    `repos` (no args) must call ensure_active_db(cwd=Path.cwd()).
    """
    monkeypatch.setenv("REPOS_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("REPOS_LEGACY_UI", raising=False)
    monkeypatch.setattr(sys, "argv", ["repos"])

    called = {"cwd": None}

    def fake_ensure_active_db(cwd: Path) -> Path:
        called["cwd"] = cwd
        db = tmp_path / "data" / "repos" / "core.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        db.touch()
        return db

    class StubKernel:
        def __init__(self, store=None, executor=None, config=None) -> None:
            self.running = False

        def start(self, include_prompt: bool = False) -> str:
            return ""

    class StubUI:
        def __init__(self, kernel) -> None:
            pass

        def write(self, text: str) -> None:
            pass

        def read(self, prompt: str) -> str:
            raise EOFError

        def clear(self) -> None:
            pass

    monkeypatch.setattr(cli, "ensure_active_db", fake_ensure_active_db, raising=True)
    monkeypatch.setattr(cli, "Kernel", StubKernel, raising=True)
    monkeypatch.setattr(cli, "PromptToolkitUI", StubUI, raising=True)
    monkeypatch.setattr(cli, "run_repl", lambda *args, **kwargs: None, raising=True)

    cli.main()

    assert called["cwd"] == Path.cwd()


def test_cli_main_calls_init_project_on_repos_init(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    `repos init` must call init_project(cwd=Path.cwd()).
    """
    monkeypatch.setenv("REPOS_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("REPOS_LEGACY_UI", raising=False)
    monkeypatch.setattr(sys, "argv", ["repos", "init"])

    called = {"cwd": None}

    def fake_init_project(cwd: Path) -> tuple:
        called["cwd"] = cwd
        db = tmp_path / "data" / "repos" / "db" / "deadbeef.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        db.touch()
        return ("deadbeef", db)

    class StubKernel:
        def __init__(self, store=None, executor=None, config=None) -> None:
            self.running = False

        def start(self, include_prompt: bool = False) -> str:
            return ""

    class StubUI:
        def __init__(self, kernel) -> None:
            pass

        def write(self, text: str) -> None:
            pass

        def read(self, prompt: str) -> str:
            raise EOFError

        def clear(self) -> None:
            pass

    monkeypatch.setattr(cli, "init_project", fake_init_project, raising=True)
    monkeypatch.setattr(cli, "Kernel", StubKernel, raising=True)
    monkeypatch.setattr(cli, "PromptToolkitUI", StubUI, raising=True)
    monkeypatch.setattr(cli, "run_repl", lambda *args, **kwargs: None, raising=True)

    cli.main()

    assert called["cwd"] == Path.cwd()


def test_cli_repos_init_does_not_prompt_for_seeding_choices(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    HARD GATE:
    `repos init` must not ask the user seed-mode/profile questions at the CLI layer.

    The init interview (project name, mode, which profiles) is owned by repos.init
    and is tested in test_init.py.
    """
    monkeypatch.setenv("REPOS_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("REPOS_LEGACY_UI", raising=False)
    monkeypatch.setattr(sys, "argv", ["repos", "init"])

    ui = FakeUI(inputs=["anything"])

    # If CLI tries to read *any* seeding choices, it will hit this and fail.
    def boom_read(prompt: str) -> str:
        raise AssertionError(f"CLI must not prompt during repos init. Prompt was: {prompt}")

    ui.read = boom_read  # type: ignore[method-assign]

    class StubKernel:
        def __init__(self, store=None, executor=None, config=None) -> None:
            self.running = False

        def start(self, include_prompt: bool = False) -> str:
            return ""

    def fake_init_project(cwd: Path, **kwargs):
        db = tmp_path / "data" / "repos" / "db" / "deadbeef.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        db.touch()
        return ("deadbeef", db)

    monkeypatch.setattr(cli, "Kernel", StubKernel, raising=True)
    monkeypatch.setattr(cli, "PromptToolkitUI", lambda kernel: ui, raising=True)
    monkeypatch.setattr(cli, "init_project", fake_init_project, raising=True)
    monkeypatch.setattr(cli, "run_repl", lambda *args, **kwargs: None, raising=True)

    cli.main()


# -------------------------------------------------------------------
# A command continuation tests
# -------------------------------------------------------------------


def test_alias_continuation_with_trailing_backslash(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Test that alias creation continues when line ends with backslash.

    Input:
      A e echo "this is a \
      test"

    Expected: Alias 'e' is created with body: echo "this is a \ntest"
    """
    k = make_kernel()
    k.running = True
    monkeypatch.setattr(k, "prompt", lambda: "G>")

    # Simulate user input with continuation
    ui = FakeUI(inputs=[
        'G',                       # Switch to Git panel (supports aliases)
        'A e echo "this is a \\',  # Trailing backslash
        'test"',                   # Continuation line
        "ZZ"                       # Exit
    ])

    cli.run_repl(k, ui=ui)

    # Check that alias was created with newline in body
    alias_body = k.store.find_alias("G", "e")
    assert alias_body is not None
    assert 'echo "this is a \\\ntest"' == alias_body


def test_alias_continuation_with_unbalanced_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Test that alias creation continues when quotes are unbalanced.

    Input:
      A e printf "%s\n" "a
      b"

    Expected: Alias 'e' is created with body containing newline
    """
    k = make_kernel()
    k.running = True
    monkeypatch.setattr(k, "prompt", lambda: "G>")

    ui = FakeUI(inputs=[
        'G',                       # Switch to Git panel (supports aliases)
        'A e printf "%s\\n" "a',  # Unbalanced quote
        'b"',                      # Closing quote
        "ZZ"                       # Exit
    ])

    cli.run_repl(k, ui=ui)

    # Check that alias was created with newline in body
    alias_body = k.store.find_alias("G", "e")
    assert alias_body is not None
    assert 'printf "%s\\n" "a\nb"' == alias_body


def test_alias_no_continuation_when_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Test that alias creation does NOT continue when input is complete.

    Input:
      A e echo "ok"

    Expected: Alias 'e' is created immediately without continuation
    """
    k = make_kernel()
    k.running = True
    monkeypatch.setattr(k, "prompt", lambda: "G>")

    ui = FakeUI(inputs=[
        'G',              # Switch to Git panel (supports aliases)
        'A e echo "ok"',  # Complete command
        "ZZ"              # Exit
    ])

    cli.run_repl(k, ui=ui)

    # Check that alias was created
    alias_body = k.store.find_alias("G", "e")
    assert alias_body is not None
    assert alias_body == 'echo "ok"'

    # Verify continuation prompt was NOT shown (only 3 prompts: G switch, alias, ZZ)
    assert len([p for p in ui.prompts if "G>" in p or "REP>" in p]) == 3
    assert len([p for p in ui.prompts if p.startswith("...")]) == 0


def test_alias_continuation_abort_with_ctrl_d(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Test that pressing Ctrl-D during continuation aborts without saving.

    Input:
      A e echo "incomplete
      <Ctrl-D>

    Expected: No alias 'e' is created
    """
    k = make_kernel()
    k.running = True
    monkeypatch.setattr(k, "prompt", lambda: "G>")

    # Use a custom UI that raises EOFError on second read
    class ContinuationAbortUI(FakeUI):
        def __init__(self):
            super().__init__(inputs=[])
            self.read_count = 0

        def read(self, prompt: str) -> str:
            self.prompts.append(prompt)
            self.read_count += 1

            if self.read_count == 1:
                # First read: switch to G panel
                return 'G'
            elif self.read_count == 2:
                # Second read: incomplete alias command
                return 'A e echo "incomplete'
            elif self.read_count == 3:
                # Third read (continuation): simulate Ctrl-D
                raise EOFError
            else:
                # Fourth read would be normal prompt, exit
                k.running = False
                return "ZZ"

    ui = ContinuationAbortUI()
    cli.run_repl(k, ui=ui)

    # Check that alias was NOT created
    alias_body = k.store.find_alias("G", "e")
    assert alias_body is None

    # Verify [Cancelled] message was shown
    assert any("[Cancelled]" in output for output in ui.outputs)
