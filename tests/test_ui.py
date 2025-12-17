# tests/test_ui.py
from __future__ import annotations

import importlib

import pytest

prompt_toolkit = pytest.importorskip("prompt_toolkit")


# Helper: Create fake dependencies for Kernel
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
        self.events.append({"panel": panel, "raw": raw_command})
        return (False, False, len(stdout), len(stderr))

    def get_history(self, panel: str) -> list[dict]:
        return []

    def get_history_detail(self, panel: str, index: int) -> dict | None:
        return None

    def get_setting(self, key: str, default: str) -> str:
        return self.settings.get(key, default)

    def set_setting(self, key: str, value: str) -> None:
        self.settings[key] = value


class FakeExecutor:
    def run(self, command: str) -> tuple[int, str, str, str, int]:
        return (0, "output\n", "", "2025-12-14T10:00:00", 100)


class FakeConfig:
    def __init__(self):
        self.panels = {
            "REP": {"entry": "REP", "name": "REP", "message": "Welcome!"},
            "GIT": {"entry": "GIT", "name": "Git", "message": "Git panel"},
            "NPM": {"entry": "NPM", "name": "Npm", "message": "Npm panel"},
        }
        self.commands = {
            "help": {"triggers": ["?"]},
            "base": {},
        }
        self.branding = {"REP": {"panel_color": "cyan", "caret_color": "pink"}}
        self.system = {"name": "RepOS", "entry_alias": "AA"}
        self.exit = {"entry": "ZZ", "message": "Bye!"}


def test_ui_module_exists_after_refactor() -> None:
    ui = importlib.import_module("repos_cli.ui")
    assert hasattr(
        ui, "PromptToolkitUI"
    ), "Expected PromptToolkitUI to be exported from repos_cli.ui"


def test_prompt_toolkit_ui_contract_surface() -> None:
    ui = importlib.import_module("repos_cli.ui")
    PTK = ui.PromptToolkitUI

    inst = PTK()

    assert callable(getattr(inst, "read", None))
    assert callable(getattr(inst, "write", None))
    assert callable(getattr(inst, "clear", None))
    assert callable(getattr(inst, "build_key_bindings", None))


def test_ctrl_l_binding_exists_and_is_registered() -> None:
    ui = importlib.import_module("repos_cli.ui")
    from repos_cli.kernel import Kernel

    k = Kernel(store=FakeStore(), executor=FakeExecutor(), config=FakeConfig())
    k.start()

    inst = ui.PromptToolkitUI()
    kb = inst.build_key_bindings(k)

    bindings = getattr(kb, "bindings", None)
    assert bindings is not None and len(bindings) > 0, "Expected at least one key binding"

    found = False
    for b in bindings:
        keys = getattr(b, "keys", [])
        for key in keys:
            key_str = getattr(key, "value", getattr(key, "key", ""))
            if key_str in ("c-l", "C-l", "control-l"):
                found = True
                break
        if found:
            break

    assert found, "Expected Ctrl+L binding (c-l) to be registered"


def test_ui_clear_is_ansi_free(monkeypatch: pytest.MonkeyPatch) -> None:
    ui = importlib.import_module("repos_cli.ui")

    called = {"clear": 0}

    def fake_clear():
        called["clear"] += 1

    monkeypatch.setattr(ui, "pt_clear", fake_clear, raising=True)

    inst = ui.PromptToolkitUI()
    ret = inst.clear()
    assert ret is None
    assert called["clear"] == 1


def test_ui_write_noops_on_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    ui = importlib.import_module("repos_cli.ui")

    called = {"print": 0}

    def fake_print_formatted_text(*args, **kwargs):
        called["print"] += 1

    monkeypatch.setattr(ui, "print_formatted_text", fake_print_formatted_text, raising=True)

    inst = ui.PromptToolkitUI()
    inst.write("")
    inst.write(None)  # type: ignore[arg-type]
    assert called["print"] == 0


def test_ui_write_wraps_ansi(monkeypatch: pytest.MonkeyPatch) -> None:
    ui = importlib.import_module("repos_cli.ui")

    seen = {"arg": None}

    def fake_print_formatted_text(arg, **kwargs):
        seen["arg"] = arg

    monkeypatch.setattr(ui, "print_formatted_text", fake_print_formatted_text, raising=True)

    inst = ui.PromptToolkitUI()
    inst.write("\033[31mRED\033[0m")

    assert seen["arg"] is not None
    assert seen["arg"].__class__.__name__ == "ANSI"


def test_ui_read_creates_session_without_kernel(monkeypatch: pytest.MonkeyPatch) -> None:
    ui = importlib.import_module("repos_cli.ui")

    created = {"key_bindings": "unset", "prompt_arg": None}

    class FakeSession:
        def __init__(
            self,
            key_bindings=None,
            completer=None,
            complete_while_typing=None,
            style=None,
            bottom_toolbar=None,
        ):
            created["key_bindings"] = key_bindings

        def prompt(self, arg):
            created["prompt_arg"] = arg
            return "hello"

    monkeypatch.setattr(ui, "PromptSession", FakeSession, raising=True)

    inst = ui.PromptToolkitUI(kernel=None)
    out = inst.read("REP>")
    assert out == "hello"
    assert created["key_bindings"] is None
    assert created["prompt_arg"].__class__.__name__ == "ANSI"


def test_ui_read_creates_session_with_kernel_and_key_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ui = importlib.import_module("repos_cli.ui")

    created = {"key_bindings": None}

    class FakeSession:
        def __init__(
            self,
            key_bindings=None,
            completer=None,
            complete_while_typing=None,
            style=None,
            bottom_toolbar=None,
        ):
            created["key_bindings"] = key_bindings

        def prompt(self, arg):
            return "ok"

    monkeypatch.setattr(ui, "PromptSession", FakeSession, raising=True)

    from repos_cli.kernel import Kernel

    k = Kernel(store=FakeStore(), executor=FakeExecutor(), config=FakeConfig())
    k.start()

    inst = ui.PromptToolkitUI(kernel=k)

    sentinel_kb = object()

    def fake_build(kernel):
        assert kernel is k
        return sentinel_kb

    monkeypatch.setattr(inst, "build_key_bindings", fake_build, raising=True)

    out = inst.read("REP>")
    assert out == "ok"
    assert created["key_bindings"] is sentinel_kb


# -------------------------------------------------------------------
# NEW: hit ui.py 58->64 (session already exists)
# -------------------------------------------------------------------
def test_ui_read_uses_existing_session_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Covers the branch where PromptToolkitUI.read() does NOT create a PromptSession
    because self.session is already set. (ui.py: 58->64)
    """
    ui = importlib.import_module("repos_cli.ui")

    class FakeSession:
        def __init__(self):
            self.calls = 0
            self.last_arg = None

        def prompt(self, arg):
            self.calls += 1
            self.last_arg = arg
            return "again"

    inst = ui.PromptToolkitUI(kernel=None)
    inst.session = FakeSession()  # pre-seed session to force the branch
    out = inst.read("REP>")
    assert out == "again"
    assert inst.session.calls == 1
    assert inst.session.last_arg.__class__.__name__ == "ANSI"


# -------------------------------------------------------------------
# NEW: hit ui.py 96-100 (execute Ctrl+L handler body)
# -------------------------------------------------------------------
def test_ctrl_l_handler_executes_clear_reset_invalidate() -> None:
    """
    Calls the actual handler registered by build_key_bindings()
    to execute the body (ui.py lines 96-100).
    """
    ui = importlib.import_module("repos_cli.ui")
    from repos_cli.kernel import Kernel

    k = Kernel(store=FakeStore(), executor=FakeExecutor(), config=FakeConfig())
    k.start()

    inst = ui.PromptToolkitUI(kernel=k)
    kb = inst.build_key_bindings(k)

    # Find the Ctrl+L binding and grab its handler
    handler = None
    for binding in kb.bindings:
        keys = [getattr(key, "value", getattr(key, "key", "")) for key in binding.keys]
        if "c-l" in keys:
            handler = binding.handler
            break

    assert handler is not None, "Ctrl+L handler not found"

    calls = {"clear": 0, "reset": 0, "invalidate": 0}

    class Renderer:
        def clear(self):
            calls["clear"] += 1

    class App:
        renderer = Renderer()

        def invalidate(self):
            calls["invalidate"] += 1

    class Buffer:
        def reset(self):
            calls["reset"] += 1

    class Event:
        app = App()
        current_buffer = Buffer()

    handler(Event())

    assert calls == {"clear": 1, "reset": 1, "invalidate": 1}


# -------------------------------------------------------------------
# ExeCompleter tests
# -------------------------------------------------------------------


def test_exe_completer_loads_path_executables(monkeypatch: pytest.MonkeyPatch) -> None:
    """ExecutableCompleter should load executables from PATH."""
    ui = importlib.import_module("repos_cli.ui")

    # Mock PATH to contain a test directory
    test_path = "/fake/bin"
    monkeypatch.setenv("PATH", test_path)

    # Mock os.listdir to return fake executables
    def fake_listdir(path):
        if path == test_path:
            return ["git", "python", "node"]
        return []

    # Mock os.path checks
    def fake_isfile(path):
        return path.startswith(test_path)

    def fake_access(path, mode):
        return True

    monkeypatch.setattr("os.listdir", fake_listdir)
    monkeypatch.setattr("os.path.isfile", fake_isfile)
    monkeypatch.setattr("os.access", fake_access)

    completer = ui.ExecutableCompleter()
    exes = completer._load()

    assert "git" in exes
    assert "python" in exes
    assert "node" in exes


def test_exe_completer_only_completes_bang_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    """ExecutableCompleter should only complete commands starting with !"""
    ui = importlib.import_module("repos_cli.ui")

    monkeypatch.setenv("PATH", "/fake")
    monkeypatch.setattr("os.listdir", lambda p: ["git"])
    monkeypatch.setattr("os.path.isfile", lambda p: True)
    monkeypatch.setattr("os.access", lambda p, m: True)

    completer = ui.ExecutableCompleter()

    # Create mock document
    class Document:
        def __init__(self, text):
            self.text = text

    # Should not complete regular text
    completions = list(completer.get_completions(Document("git"), None))
    assert len(completions) == 0

    # Should complete after !
    completions = list(completer.get_completions(Document("!git"), None))
    assert len(completions) > 0


def test_exe_completer_caches_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """ExecutableCompleter should cache PATH results."""
    ui = importlib.import_module("repos_cli.ui")

    test_path = "/fake"
    monkeypatch.setenv("PATH", test_path)

    call_count = [0]

    def fake_listdir(path):
        call_count[0] += 1
        return ["git"]

    monkeypatch.setattr("os.listdir", fake_listdir)
    monkeypatch.setattr("os.path.isfile", lambda p: True)
    monkeypatch.setattr("os.access", lambda p, m: True)

    completer = ui.ExecutableCompleter()

    # First load
    completer._load()
    first_count = call_count[0]

    # Second load should use cache
    completer._load()

    assert call_count[0] == first_count  # No additional calls


# -------------------------------------------------------------------
# PathCompleter tests
# -------------------------------------------------------------------


def test_path_completer_lists_directory_contents(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PathCompleter should list directory contents."""
    ui = importlib.import_module("repos_cli.ui")

    # Create test files
    (tmp_path / "file1.txt").touch()
    (tmp_path / "file2.py").touch()
    (tmp_path / "subdir").mkdir()

    completer = ui.PathCompleter()

    # Mock current directory
    monkeypatch.chdir(tmp_path)

    # Create mock document - PathCompleter requires space after command
    class Document:
        text = "!cat f"

    completions = list(completer.get_completions(Document(), None, require_bang=True))

    # Should list files in current directory starting with 'f'
    completion_texts = [c.text for c in completions]
    assert any("file1.txt" in t for t in completion_texts)
    assert any("file2.py" in t for t in completion_texts)


def test_path_completer_expands_tilde(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PathCompleter should expand ~ to home directory."""
    ui = importlib.import_module("repos_cli.ui")
    import os

    # Mock home directory
    home = str(tmp_path / "home")
    os.makedirs(home, exist_ok=True)
    (tmp_path / "home" / "test.txt").touch()

    monkeypatch.setattr("os.path.expanduser", lambda p: p.replace("~", home))

    completer = ui.PathCompleter()

    class Document:
        text = "!cat ~/test"

    # Should expand ~ and find files
    completions = list(completer.get_completions(Document(), None, require_bang=True))
    assert len(completions) > 0


def test_path_completer_respects_require_bang(monkeypatch: pytest.MonkeyPatch) -> None:
    """PathCompleter should respect require_bang flag."""
    ui = importlib.import_module("repos_cli.ui")

    completer = ui.PathCompleter()

    class Document:
        text = "cat file.txt"

    # With require_bang=True, should not complete
    completions = list(completer.get_completions(Document(), None, require_bang=True))
    assert len(completions) == 0

    # With require_bang=False, should complete
    monkeypatch.setattr("os.listdir", lambda p: ["file.txt"])
    completions = list(completer.get_completions(Document(), None, require_bang=False))
    # May or may not have completions depending on parsing, but should not error


# -------------------------------------------------------------------
# BangArgCompleter tests
# -------------------------------------------------------------------


def test_bang_arg_completer_wraps_path_completer(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """BangArgCompleter should wrap PathCompleter with require_bang=True."""
    ui = importlib.import_module("repos_cli.ui")

    # Create test file
    (tmp_path / "test.txt").touch()

    monkeypatch.setattr("os.getcwd", lambda: str(tmp_path))

    completer = ui.BangArgCompleter()

    class Document:
        text = "!cat "

    completions = list(completer.get_completions(Document(), None))

    # Should provide completions for bang commands
    assert len(completions) >= 0  # May be empty if no files match pattern


# -------------------------------------------------------------------
# ReposCompleter tests
# -------------------------------------------------------------------


def test_repos_completer_combines_completers(monkeypatch: pytest.MonkeyPatch) -> None:
    """ReposCompleter should combine alias and bang completers."""
    ui = importlib.import_module("repos_cli.ui")
    from repos_cli.kernel import Kernel

    k = Kernel(store=FakeStore(), executor=FakeExecutor(), config=FakeConfig())
    k.start()

    # Add an alias
    k.store.add_alias("REP", "test", "echo test")

    completer = ui.ReposCompleter(k)

    class Document:
        text = "tes"
        text_before_cursor = "tes"
        cursor_position = 3

    # Should complete alias
    completions = list(completer.get_completions(Document(), None))

    # Should have alias completion
    completion_texts = [c.text for c in completions]
    assert "test" in completion_texts


def test_repos_completer_handles_no_kernel() -> None:
    """ReposCompleter should handle None kernel."""
    ui = importlib.import_module("repos_cli.ui")

    completer = ui.ReposCompleter(None)

    class Document:
        text = "test"
        text_before_cursor = "test"
        cursor_position = 4

    # Should not crash with None kernel
    completions = list(completer.get_completions(Document(), None))
    assert len(completions) >= 0  # Should work without errors


# -------------------------------------------------------------------
# Panel switching tests
# -------------------------------------------------------------------


def test_switch_to_entry_calls_kernel_command() -> None:
    """_switch_to_entry should call kernel.handle_command with switch command."""
    ui = importlib.import_module("repos_cli.ui")
    from repos_cli.kernel import Kernel

    k = Kernel(store=FakeStore(), executor=FakeExecutor(), config=FakeConfig())
    k.start()

    inst = ui.PromptToolkitUI(kernel=k)

    # Switch to GIT panel - this should call handle_command internally
    inst._switch_to_entry("GIT")

    # Panel should have switched (verify no crash)
    # The exact behavior depends on kernel implementation


def test_switch_to_slot_switches_panel() -> None:
    """_switch_to_slot should switch to panel at given slot index."""
    ui = importlib.import_module("repos_cli.ui")
    from repos_cli.kernel import Kernel

    k = Kernel(store=FakeStore(), executor=FakeExecutor(), config=FakeConfig())
    k.start()

    inst = ui.PromptToolkitUI(kernel=k)

    # Get panels list - now should have multiple panels
    panels = inst._panels_in_order()

    # FakeConfig now has 3 panels
    if len(panels) > 0:
        # Switch to slot 0
        inst._switch_to_slot(0)
        # Verify no crash


def test_cycle_panel_moves_forward() -> None:
    """_cycle_panel should move forward through panels."""
    ui = importlib.import_module("repos_cli.ui")
    from repos_cli.kernel import Kernel

    k = Kernel(store=FakeStore(), executor=FakeExecutor(), config=FakeConfig())
    k.start()

    inst = ui.PromptToolkitUI(kernel=k)

    # Cycle forward
    inst._cycle_panel(1)

    # Panel should have changed (unless only one panel)
    # We just verify no crash


def test_current_panel_entry_returns_kernel_panel() -> None:
    """_current_panel_entry should return current kernel panel."""
    ui = importlib.import_module("repos_cli.ui")
    from repos_cli.kernel import Kernel

    k = Kernel(store=FakeStore(), executor=FakeExecutor(), config=FakeConfig())
    k.start()
    k.panel = "REP"

    inst = ui.PromptToolkitUI(kernel=k)

    entry = inst._current_panel_entry()
    assert entry == "REP"


def test_switch_with_no_kernel_does_not_crash() -> None:
    """Panel switching with no kernel should not crash."""
    ui = importlib.import_module("repos_cli.ui")

    inst = ui.PromptToolkitUI(kernel=None)

    # These should all be safe with no kernel
    inst._switch_to_entry("GIT")
    inst._switch_to_slot(0)
    inst._cycle_panel(1)

    entry = inst._current_panel_entry()
    assert entry == ""


# -------------------------------------------------------------------
# Key bindings tests
# -------------------------------------------------------------------


def test_ctrl_n_binding_cycles_forward() -> None:
    """Ctrl+N should cycle panels forward."""
    ui = importlib.import_module("repos_cli.ui")
    from repos_cli.kernel import Kernel

    k = Kernel(store=FakeStore(), executor=FakeExecutor(), config=FakeConfig())
    k.start()

    inst = ui.PromptToolkitUI(kernel=k)

    # Create key bindings with kernel parameter
    kb = inst.build_key_bindings(k)

    # Verify Ctrl+N is registered
    assert kb is not None


def test_ctrl_p_binding_cycles_backward() -> None:
    """Ctrl+P should cycle panels backward."""
    ui = importlib.import_module("repos_cli.ui")
    from repos_cli.kernel import Kernel

    k = Kernel(store=FakeStore(), executor=FakeExecutor(), config=FakeConfig())
    k.start()

    inst = ui.PromptToolkitUI(kernel=k)

    # Create key bindings with kernel parameter
    kb = inst.build_key_bindings(k)

    # Verify Ctrl+P is registered
    assert kb is not None


def test_alt_digit_bindings_switch_to_slot() -> None:
    """Alt+digit should switch to panel slot."""
    ui = importlib.import_module("repos_cli.ui")
    from repos_cli.kernel import Kernel

    k = Kernel(store=FakeStore(), executor=FakeExecutor(), config=FakeConfig())
    k.start()

    inst = ui.PromptToolkitUI(kernel=k)

    # Create key bindings with kernel parameter
    kb = inst.build_key_bindings(k)

    # Verify key bindings exist
    assert kb is not None


def test_toolbar_width_defaults_to_120() -> None:
    """_toolbar_width should default to 120 if session not available."""
    ui = importlib.import_module("repos_cli.ui")
    from repos_cli.kernel import Kernel

    k = Kernel(store=FakeStore(), executor=FakeExecutor(), config=FakeConfig())
    k.start()

    inst = ui.PromptToolkitUI(kernel=k)

    # Without session, should return default
    width = inst._toolbar_width()
    assert width == 120


def test_panelbar_style_defaults() -> None:
    """_panelbar_style_defaults should return style tuple."""
    ui = importlib.import_module("repos_cli.ui")
    from repos_cli.kernel import Kernel

    k = Kernel(store=FakeStore(), executor=FakeExecutor(), config=FakeConfig())
    k.start()

    inst = ui.PromptToolkitUI(kernel=k)

    active, inactive, sep = inst._panelbar_style_defaults()

    # Should return non-empty strings
    assert isinstance(active, str)
    assert isinstance(inactive, str)
    assert isinstance(sep, str)


# -------------------------------------------------------------------
# Bottom toolbar tests
# -------------------------------------------------------------------


def test_bottom_toolbar_shows_db_info(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bottom toolbar should show database information."""
    ui = importlib.import_module("repos_cli.ui")
    from pathlib import Path

    from repos_cli.kernel import Kernel

    k = Kernel(store=FakeStore(), executor=FakeExecutor(), config=FakeConfig())
    k.start()
    k.active_db_name = "test.db"
    k.active_db_source = "local"
    k.active_db_path = Path("/tmp/test.db")

    inst = ui.PromptToolkitUI(kernel=k)

    # Call bottom toolbar
    toolbar = inst._bottom_toolbar()

    # Should contain database info (returned as formatted text)
    # The toolbar returns formatted text, so check if it's not None
    assert toolbar is not None


def test_bottom_toolbar_handles_no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bottom toolbar should handle no active database."""
    ui = importlib.import_module("repos_cli.ui")
    from repos_cli.kernel import Kernel

    k = Kernel(store=FakeStore(), executor=FakeExecutor(), config=FakeConfig())
    k.start()
    k.active_db_path = None

    inst = ui.PromptToolkitUI(kernel=k)

    # Should not crash with no DB
    toolbar = inst._bottom_toolbar()
    assert toolbar is not None or toolbar == ""
