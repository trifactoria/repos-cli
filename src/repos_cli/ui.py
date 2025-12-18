# RepOS™ — Multi-Panel REPL-Based Developer Command Environment
# Copyright (c) 2025
# TriFactoria (Andrew Blankfield)
#
# Licensed under the Business Source License 1.1 (BSL 1.1).
# You may use, modify, and redistribute this file under the terms of the BSL.
# On the Change Date (2029-01-01), this file will be licensed under
# the Apache License, Version 2.0.

from __future__ import annotations

import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, merge_completers
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import clear as pt_clear
from prompt_toolkit.shortcuts import print_formatted_text
from prompt_toolkit.styles import Style

if TYPE_CHECKING:
    from .kernel import Kernel  # pragma: no cover


@dataclass(frozen=True)
class AliasCompletionItem:
    key: str
    expanded: str


# ----------------------------
# Config helpers (MUST come from config.py facade via kernel.config.get_path)
# ----------------------------


def _cfg_get_path(kernel: Kernel | None, path: str, default):
    if kernel is None:
        return default
    cfg = getattr(kernel, "config", None)
    if cfg is None or not hasattr(cfg, "get_path"):
        return default
    try:
        return cfg.get_path(path, default)
    except Exception:
        return default


def _cfg_bool(kernel: Kernel | None, path: str, default: bool) -> bool:
    return bool(_cfg_get_path(kernel, path, default))


def _cfg_dict(kernel: Kernel | None, path: str, default: dict) -> dict:
    val = _cfg_get_path(kernel, path, default)
    return val if isinstance(val, dict) else default


def _cfg_str(kernel: Kernel | None, path: str, default: str) -> str:
    val = _cfg_get_path(kernel, path, default)
    return str(val) if val is not None else default


# ----------------------------
# Theme / Style
# ----------------------------


def _default_style_dict() -> dict[str, str]:
    # Conservative: works across prompt_toolkit versions.
    return {
        # completion menu
        "completion-menu": "bg:#111111 #d0d0d0",
        "completion-menu.completion": "bg:#111111 #d0d0d0",
        "completion-menu.completion.current": "bg:#303030 #ffffff bold",
        "completion-menu.meta.completion": "bg:#111111 #808080",
        "completion-menu.meta.completion.current": "bg:#303030 #a0a0a0",
        "scrollbar.background": "bg:#202020",
        "scrollbar.button": "bg:#505050",
        # toolbar base (prompt_toolkit uses this class name)
        "bottom-toolbar": "bg:#0b0b0b #d0d0d0",
        # panelbar styles (used in formatted_text tuples)
        "repos.panelbar": "bg:#0b0b0b #d0d0d0",
        "repos.panelbar.active": "bg:#d0d0d0 #0b0b0b bold",
        "repos.panelbar.inactive": "bg:#0b0b0b #d0d0d0",
        "repos.panelbar.sep": "bg:#0b0b0b #666666",
        # alias expansion line
        "repos.aliasbar": "bg:#0b0b0b #a0a0a0",
        "repos.aliasbar.label": "bg:#0b0b0b #a0a0a0",
        "repos.aliasbar.value": "bg:#0b0b0b #d0d0d0",
    }


def _build_style(kernel: Kernel | None) -> Style:
    base = _default_style_dict()
    overrides = _cfg_dict(kernel, "ui.theme.style", {})
    # only keep string->string
    for k, v in list(overrides.items()):
        if isinstance(k, str) and isinstance(v, str):
            base[k] = v
    return Style.from_dict(base)


# ----------------------------
# Bang-mode completions
# ----------------------------


class ExecutableCompleter(Completer):
    """Completes executable names available on PATH (first token after '!')."""

    def __init__(self) -> None:
        self._cache: set[str] | None = None
        self._cache_path: str | None = None

    def _load(self) -> set[str]:
        path_val = os.environ.get("PATH", "")
        if self._cache is not None and self._cache_path == path_val:
            return self._cache

        exes: set[str] = set()
        for p in path_val.split(os.pathsep):
            if not p:
                continue
            try:
                for name in os.listdir(p):
                    full = os.path.join(p, name)
                    if os.path.isfile(full) and os.access(full, os.X_OK):
                        exes.add(name)
            except Exception:
                continue

        self._cache = exes
        self._cache_path = path_val
        return exes

    def get_completions(
        self, document, complete_event
    ) -> Iterable[Completion]:
        text = document.text or ""
        stripped = text.lstrip()
        if not stripped.startswith("!"):
            return

        after = stripped[1:].lstrip()
        if not after:
            return

        # If there's a space, we're in args (not completing the command token)
        if " " in after:
            return

        token = after
        for exe in sorted(self._load()):
            if exe.startswith(token):
                yield Completion(
                    exe, start_position=-len(token), display_meta="exe"
                )


class PathCompleter(Completer):
    """Generic filesystem path completion."""

    def _current_arg_token(
        self, full_text: str, require_bang: bool = False
    ) -> tuple[str | None, int]:
        """Extract the current token to complete from the text.

        Args:
            full_text: The full input text
            require_bang: If True, only work on lines starting with !

        Returns:
            (token, replace_len) or (None, 0) if not applicable
        """
        stripped = full_text.lstrip()

        if require_bang:
            if not stripped.startswith("!"):
                return (None, 0)
            after = stripped[1:].lstrip()
        else:
            after = stripped

        if not after:
            return (None, 0)

        # Need at least one space after cmd token
        if " " not in after:
            return (None, 0)

        # Split into command and arguments
        parts = after.split(maxsplit=1)
        arg = parts[1] if len(parts) > 1 else ""

        # If the line ends with space, complete from "."
        if after.endswith(" "):
            return ("", 0)

        arg = arg.lstrip()
        if not arg:
            return ("", 0)

        token = arg.split()[-1]
        return (token, len(token))

    def _list_dir(self, directory: str) -> list[str]:
        try:
            return sorted(os.listdir(directory))
        except Exception:
            return []

    def get_completions(
        self, document, complete_event, require_bang: bool = False
    ) -> Iterable[Completion]:
        """Get path completions.

        Args:
            document: The document to complete
            complete_event: The completion event
            require_bang: If True, only complete on lines starting with !
        """
        token, replace_len = self._current_arg_token(
            document.text or "", require_bang=require_bang
        )
        if token is None:
            return

        display_token = token
        expanded = os.path.expanduser(token)

        if token == "":
            base_dir = "."
            prefix = ""
            insert_prefix = ""
        else:
            if expanded.endswith("/") or expanded.endswith(os.sep):
                base_dir = expanded
                prefix = ""
                insert_prefix = display_token
            else:
                base_dir = os.path.dirname(expanded) or "."
                prefix = os.path.basename(expanded)
                insert_prefix = os.path.dirname(display_token)
                if insert_prefix and not insert_prefix.endswith("/"):
                    insert_prefix += "/"
                elif insert_prefix == ".":
                    insert_prefix = "./"

        for name in self._list_dir(base_dir):
            if not name.startswith(prefix):
                continue
            full = os.path.join(base_dir, name)
            is_dir = os.path.isdir(full)
            ins = f"{insert_prefix}{name}" + ("/" if is_dir else "")
            meta = "dir" if is_dir else "file"
            yield Completion(
                ins, start_position=-replace_len, display_meta=meta
            )


class BangArgCompleter(Completer):
    """Filesystem path completion for bang-mode arguments.

    Completes after the command token.
    """

    def __init__(self) -> None:
        self._path_completer = PathCompleter()

    def get_completions(
        self, document, complete_event
    ) -> Iterable[Completion]:
        yield from self._path_completer.get_completions(
            document, complete_event, require_bang=True
        )


# ----------------------------
# RepOS Completer (aliases + bang)
# ----------------------------


class ReposCompleter(Completer):
    def __init__(self, kernel: Kernel | None) -> None:
        self.kernel = kernel
        self._exe = ExecutableCompleter()
        self._path = PathCompleter()
        self._bang_args = BangArgCompleter()
        self._bang = merge_completers(
            [self._exe, self._bang_args]
        )

    def _get_alias_items(self) -> list[AliasCompletionItem]:
        k = self.kernel
        if k is None:
            return []
        if hasattr(k, "list_alias_completions"):
            try:
                raw = k.list_alias_completions()
                items: list[AliasCompletionItem] = []
                for r in raw or []:
                    if isinstance(r, dict):
                        key = str(r.get("key", "")).strip()
                        expanded = str(
                            r.get("expanded", "")
                        ).strip()
                        if key:
                            items.append(
                                AliasCompletionItem(
                                    key=key, expanded=expanded
                                )
                            )
                return items
            except Exception:
                return []
        return []

    def _first_token_before_cursor(self, text_before_cursor: str) -> str:
        """Return the first token from the start of the line.

        Up to first whitespace. If cursor is past first token
        (i.e. there is whitespace), return "" so we don't suggest
        aliases for arguments.
        """
        s = text_before_cursor.lstrip()

        # If user already typed whitespace after the first token,
        # we're in args → no alias completion.
        # e.g. "ec m" or "b && ..." (v1.0 doesn't do chaining)
        if " " in s:
            return ""

        return s  # the whole thing is still the first token fragment

    def _is_shell_fallback_panel(self) -> bool:
        """Check if current panel has shell_fallback enabled."""
        k = self.kernel
        if k is None:
            return False
        if hasattr(k, "current_panel_has_shell_fallback"):
            try:
                return k.current_panel_has_shell_fallback()
            except Exception:
                return False
        return False

    def get_completions(
        self, document, complete_event
    ) -> Iterable[Completion]:
        text = document.text or ""
        before = document.text_before_cursor or ""

        # Bang mode always gets shell completion
        if text.lstrip().startswith("!"):
            yield from self._bang.get_completions(
                document, complete_event
            )
            return

        # Check if we're in a shell_fallback panel
        is_shell_fallback = self._is_shell_fallback_panel()

        token = self._first_token_before_cursor(before)

        # If we're on the first token
        if token:
            # Always complete aliases on first token
            for it in self._get_alias_items():
                if it.key.startswith(token):
                    yield Completion(
                        it.key,
                        start_position=-len(token),
                        display_meta=(it.expanded or ""),
                    )

            # In shell_fallback panels, also complete executables
            # on first token
            if is_shell_fallback:
                for exe in sorted(self._exe._load()):
                    if exe.startswith(token):
                        yield Completion(
                            exe, start_position=-len(token),
                            display_meta="exe"
                        )
            return

        # If we're past the first token (in arguments)
        if is_shell_fallback and " " in text.lstrip():
            # Complete paths/files in shell_fallback panels
            yield from self._path.get_completions(
                document, complete_event, require_bang=False
            )


# ----------------------------
# PromptSession UI + Bottom Panel Bar
# ----------------------------


class PromptToolkitUI:
    """
    This is the *terminal-friendly* UI:
      - Keeps normal terminal scrollback + drag-select copy.
      - Uses PromptSession so completion menus remain exactly as expected.
      - Adds a YAML-themed bottom toolbar that can show:
          * panel strip (wrap at panel boundaries)
          * alias expansion for current token
      - Adds hotkeys:
          * Ctrl+N / Ctrl+P: cycle panels
          * Alt+0..9: jump to panel slot
    """

    def __init__(self, kernel: Kernel | None = None) -> None:
        self.kernel = kernel
        self.session: PromptSession[str] | None = None
        self._completer: ReposCompleter | None = None
        self._style = _build_style(kernel)

        # Track whether we ended on a newline (to prevent prompt mangling)
        self._needs_newline_before_prompt = False

        # Cached panels in config order: [(Name, entry)]
        self._panels: list[tuple[str, str]] = self._panels_in_order()
        self._last_tab_time = 0.0
        self._last_tab_text = ""

    # ---------- panels ----------

    def _panels_in_order(self) -> list[tuple[str, str]]:
        panels = _cfg_get_path(self.kernel, "panels", {})
        if not isinstance(panels, dict):
            return []
        out: list[tuple[str, str]] = []
        for name, pcfg in panels.items():
            if not isinstance(pcfg, dict):
                continue
            entry = pcfg.get("entry") or name
            if isinstance(entry, str):
                out.append((str(name), entry))
        return out

    def _current_panel_entry(self) -> str:
        if self.kernel is None:
            return ""
        return str(getattr(self.kernel, "panel", "") or "")

    def _switch_command(self) -> str:
        return _cfg_str(self.kernel, "system.switch_command", "REP")

    def _switch_to_entry(self, entry: str) -> None:
        if self.kernel is None:
            return
        try:
            self.kernel.handle_command(f"{self._switch_command()} {entry}")
        except Exception:
            pass

    def _switch_to_slot(self, slot: int) -> None:
        if not self._panels:
            return
        if slot < 0 or slot >= len(self._panels):
            return
        _name, entry = self._panels[slot]
        self._switch_to_entry(entry)

    def _cycle_panel(self, delta: int) -> None:
        if not self._panels:
            return
        current = self._current_panel_entry()
        entries = [e for _, e in self._panels]
        idx = entries.index(current) if current in entries else 0
        nxt = (idx + delta) % len(entries)
        self._switch_to_entry(entries[nxt])

    # ---------- toolbar rendering ----------

    def _toolbar_width(self) -> int:
        try:
            if (self.session and self.session.app and
                    self.session.app.output):
                return int(
                    self.session.app.output.get_size().columns
                )
        except Exception:
            pass
        return 120

    def _panelbar_style_defaults(self) -> tuple[str, str, str]:
        active = _cfg_str(
            self.kernel, "ui.panelbar.active_style",
            "class:repos.panelbar.active"
        )
        inactive = _cfg_str(
            self.kernel, "ui.panelbar.inactive_style",
            "class:repos.panelbar.inactive"
        )
        sep = _cfg_str(
            self.kernel, "ui.panelbar.sep_style",
            "class:repos.panelbar.sep"
        )
        return (active, inactive, sep)

    def _panelbar_style_for(self, entry: str, is_active: bool) -> str:
        key = "active" if is_active else "inactive"
        v = _cfg_str(
            self.kernel, f"ui.panelbar.per_panel.{entry}.{key}", ""
        ).strip()
        if v:
            return v
        active, inactive, _sep = self._panelbar_style_defaults()
        return active if is_active else inactive

    def _wrap_tokens(
        self, tokens: list[tuple[str, str]], width: int, max_lines: int = 3
    ) -> list[list[tuple[str, str]]]:
        """
        Wrap at token boundaries only. Each token is (style, text).
        Returns list-of-lines; each line is list of tokens.
        """
        lines: list[list[tuple[str, str]]] = [[]]
        used = 0

        def newline() -> None:
            nonlocal used
            if len(lines) < max_lines:
                lines.append([])
                used = 0
            else:
                used = width  # stop adding

        for style, text in tokens:
            if used >= width:
                newline()
                if used >= width:
                    break

            # hard truncate tokens that cannot fit on a line
            if len(text) > width:
                text = text[:width]

            if used + len(text) <= width:
                lines[-1].append((style, text))
                used += len(text)
            else:
                newline()
                if used >= width:
                    break
                lines[-1].append((style, text))
                used += len(text)

        return lines

    def _build_panelbar_tokens(
        self, width: int
    ) -> list[list[tuple[str, str]]]:
        if not _cfg_bool(self.kernel, "ui.panelbar.enabled", True):
            return []

        # refresh list (in case config changed)
        self._panels = self._panels_in_order()
        current = self._current_panel_entry()

        _active, _inactive, sep_style = self._panelbar_style_defaults()

        tokens: list[tuple[str, str]] = []
        max_show = min(len(self._panels), 10)
        for i, (name, entry) in enumerate(self._panels[:max_show]):
            style = self._panelbar_style_for(
                entry, is_active=(entry == current)
            )
            tokens.append((style, f"{i}•{name} "))
            if i != max_show - 1:
                tokens.append((sep_style, "|"))

        return self._wrap_tokens(
            tokens, width=width, max_lines=3
        )

    def _build_aliasbar_tokens(self) -> list[tuple[str, str]]:
        if not _cfg_bool(self.kernel, "ui.toolbar.enabled", True):
            return []

        if self.session is None:
            return []

        buf = self.session.default_buffer
        raw = buf.text or ""
        s = raw.lstrip()

        # nothing / bang-mode
        if not s or s.startswith("!"):
            return []

        # v1.0: always preview the FIRST token, even after spaces
        # (persist while typing args)
        first = s.split(maxsplit=1)[0].strip()
        if not first:
            return []

        if self.kernel is None or not hasattr(self.kernel, "expand_alias"):
            return []

        try:
            expanded = self.kernel.expand_alias(first)
        except Exception:
            expanded = ""

        if not expanded:
            return []

        return [
            ("class:repos.aliasbar.label", "  "),
            ("class:repos.aliasbar.value", f"{first} → {expanded}"),
            ("class:repos.aliasbar.label", "  "),
        ]

    def _bottom_toolbar(self):
        """
        Return formatted text (list-of-tuples) with optional newlines.
        """
        width = self._toolbar_width()

        panel_lines = self._build_panelbar_tokens(width=width)
        alias_line = self._build_aliasbar_tokens()

        if not panel_lines and not alias_line:
            return ""

        out: list[tuple[str, str]] = []

        # 1) aliasbar first (single line), if present
        if alias_line:
            out.extend(alias_line)

        # 2) panel bar below (can be multi-line), if present
        if panel_lines:
            if out:
                out.append(("class:repos.panelbar", "\n"))
            for li, line in enumerate(panel_lines):
                out.extend(
                    line if line else [("class:repos.panelbar", "")]
                )
                if li != len(panel_lines) - 1:
                    out.append(("class:repos.panelbar", "\n"))

        return out

    # ---------- session ----------

    def _ensure_session(self) -> None:
        if self.session is not None:
            return

        key_bindings = (
            self.build_key_bindings(self.kernel)
            if self.kernel else None
        )
        self._completer = ReposCompleter(self.kernel)

        self.session = PromptSession(
            key_bindings=key_bindings,
            completer=self._completer,
            complete_while_typing=True,
            style=self._style,
            bottom_toolbar=self._bottom_toolbar,
        )

    # ---------- public API ----------

    def read(self, prompt: str) -> str:
        self._ensure_session()
        assert self.session is not None

        # If last output didn't end with newline, insert one
        # before prompt redraw
        if self._needs_newline_before_prompt:
            print_formatted_text(
                ANSI("\n"), style=self._style, end=""
            )
            self._needs_newline_before_prompt = False

        with patch_stdout():
            # prompt contains ANSI from kernel.prompt(),
            # so preserve it
            return self.session.prompt(ANSI(prompt + " "))

    def write(self, text: str) -> None:
        """Write EXACTLY what we receive (no extra newline).

        Track prompt safety.
        """
        if not text:
            return
        print_formatted_text(ANSI(text), style=self._style, end="")
        self._needs_newline_before_prompt = not text.endswith("\n")

    def clear(self) -> None:
        pt_clear()

    # ---------- TTY handoff support ----------

    def prepare_tty_handoff(self) -> None:
        """Prepare for handing control to a TTY program.

        Works with pager or interactive tool. Ensures clean visual
        transition by printing a newline if needed.
        """
        # If previous output didn't end with newline, add one now
        # This prevents the TTY program from starting on the same line
        if self._needs_newline_before_prompt:
            print_formatted_text(ANSI("\n"), style=self._style, end="")
            self._needs_newline_before_prompt = False

    def restore_after_tty(self) -> None:
        """Restore UI state after TTY program returns control.

        TTY programs (like less) handle their own screen management,
        so when they exit, we need to ensure the next prompt will be clean.
        """
        # TTY programs usually leave cursor at start of line after exiting
        # Mark that we need a newline before next prompt to be safe
        self._needs_newline_before_prompt = False

    # ---------- keybindings ----------

    def build_key_bindings(self, kernel: Kernel) -> KeyBindings:
        kb = KeyBindings()

        def _switch_and_refresh(event, switch_fn) -> None:
            # 1) drop whatever user was typing
            try:
                event.current_buffer.reset()
            except Exception:
                pass

            # 2) switch panel
            switch_fn()

            # 4) redraw prompt immediately (shows new REP>/G>/etc)
            event.app.exit(result="")

        @kb.add("c-l")
        def _(event):
            try:
                event.app.renderer.clear()
            except Exception:
                pass
            try:
                event.current_buffer.reset()
            except Exception:
                pass
            event.app.invalidate()

        @kb.add("c-n")
        def _(event):
            _switch_and_refresh(event, lambda: self._cycle_panel(+1))

        @kb.add("c-p")
        def _(event):
            _switch_and_refresh(event, lambda: self._cycle_panel(-1))

        # Alt+0..9 is usually ESC then digit (GNOME Terminal + tmux)
        for n in range(10):

            @kb.add("escape", str(n))
            def _(event, n=n):
                _switch_and_refresh(event, lambda n=n: self._switch_to_slot(n))

        # double tab shows alias list for panel
        @kb.add("tab")
        def _(event):
            buf = event.current_buffer
            now = time.time()

            text = buf.text
            cursor_at_end = buf.cursor_position == len(text)

            # Detect double-tab
            if (now - self._last_tab_time < 0.5 and
                    text == self._last_tab_text and cursor_at_end):
                # In REP panel: show REP menu via "?"
                # In other panels: show alias list via "L"
                if kernel.panel == "REP":
                    buf.text = "?"
                else:
                    buf.text = "L"
                buf.cursor_position = len(buf.text)
                event.app.exit(result=buf.text)
                return

            # Otherwise: normal completion
            self._last_tab_time = now
            self._last_tab_text = text
            event.app.current_buffer.complete_next()

        return kb
