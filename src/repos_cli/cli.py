# RepOS™ — Multi-Panel REPL-Based Developer Command Environment
# Copyright (c) 2025
# TriFactoria (Andrew Blankfield)
#
# Licensed under the Business Source License 1.1 (BSL 1.1).
# You may use, modify, and redistribute this file under the terms of the BSL.
# On the Change Date (2029-01-01), this file will be licensed under
# the Apache License, Version 2.0.

"""
RepOS CLI entry point and REPL loop.

Design:
- CLI owns process startup and DB resolution.
- Kernel is the session engine (config+store+executor injected).
- UI is terminal-friendly PromptSession (keeps scrollback + copy/select).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

from . import config
from .executor import SubprocessExecutor
from .init import ensure_active_db, init_project
from .kernel import Kernel, write_crash_log
from .store import SQLiteStore
from .ui import PromptToolkitUI
from .utils import is_shell_input_incomplete


def _extract_alias_body(line: str, kernel: Kernel) -> str | None:
    """Extract alias body from 'A' command, or None if not alias add.

    This function must work even when the input has incomplete quotes
    or trailing backslashes, as we need to detect continuation cases.

    Args:
        line: The command line to check
        kernel: Kernel instance for checking command triggers

    Returns:
        The alias body if this is an alias add command, None otherwise
    """
    stripped = line.strip()
    if not stripped:
        return None

    # Extract the first token (trigger) without using shlex
    space_idx = stripped.find(' ')
    if space_idx <= 0:
        return None

    trigger = stripped[:space_idx]
    after_trigger = stripped[space_idx:].lstrip()

    # Check if trigger is an alias add command
    action = kernel.command_triggers.get(trigger)
    if action != "add":
        return None

    # Extract the alias name (alphanumeric/underscore only)
    if not after_trigger:
        return None

    name_end = 0
    for i, ch in enumerate(after_trigger):
        if ch.isalnum() or ch == '_':
            name_end = i + 1
        else:
            break

    if name_end == 0:
        return None

    # Everything after the name is the body
    raw_body = after_trigger[name_end:].lstrip()
    return raw_body


def run_repl(
    kernel: Kernel,
    ui: PromptToolkitUI | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> None:
    """Run the standard RepOS REPL loop."""
    while kernel.running:
        try:
            prompt = kernel.prompt()

            if ui is not None:
                line = ui.read(prompt)
            else:
                line = input_fn(prompt + " ")

            line = (line or "").strip()
            if not line:
                continue

            # Check if alias add command needs continuation
            alias_body = _extract_alias_body(line, kernel)
            if alias_body is not None:
                # Save original line prefix for reconstruction
                original_line = line

                # Check if the alias body is incomplete
                while is_shell_input_incomplete(alias_body):
                    try:
                        # Read continuation line
                        cont_prompt = (
                            config.ANSI_COLORS["cyan"] + "..." +
                            config.ANSI_COLORS["pink"] + ">" +
                            config.ANSI_COLORS["reset"]
                        )
                        if ui is not None:
                            continuation = ui.read(cont_prompt)
                        else:
                            continuation = input_fn(cont_prompt)

                        # Append with literal newline
                        alias_body = alias_body + "\n" + continuation

                    except (KeyboardInterrupt, EOFError):
                        # User aborted - don't save
                        msg = "\n[Cancelled]\n"
                        if ui is not None:
                            ui.write(msg)
                        else:
                            output_fn(msg)
                        # Set line to empty so we don't process it
                        line = ""
                        break

                # Reconstruct the full command with accumulated body
                if line:  # Only if not cancelled
                    # Extract trigger and name from original line
                    stripped = original_line.strip()

                    # Find the trigger (first token)
                    space_idx = stripped.find(' ')
                    if space_idx > 0:
                        trigger = stripped[:space_idx]
                        after_trigger = stripped[space_idx:].lstrip()

                        # Find name - alphanumeric/underscore
                        name_end = 0
                        for i, ch in enumerate(after_trigger):
                            if ch.isalnum() or ch == '_':
                                name_end = i + 1
                            else:
                                break

                        if name_end > 0:
                            name = after_trigger[:name_end]
                            # Rebuild command with accumulated body
                            line = f"{trigger} {name} {alias_body}"

            try:
                if line:  # Only process if not cancelled
                    response = kernel.handle_command(line)

                    if response == config.UI_CLEAR:
                        if ui is not None:
                            ui.clear()
                        else:
                            output_fn("\033[2J\033[H")
                        continue

                    if response:
                        if ui is not None:
                            ui.write(response)
                        else:
                            output_fn(response)

            except Exception as e:
                # Unhandled exception - write crash log
                write_crash_log(
                    e,
                    panel=kernel.panel,
                    raw_command=line,
                    resolved_command="",
                    db_name=kernel.active_db_name,
                    db_path=kernel.active_db_path,
                )
                # Show error to user
                error_msg = (
                    f"[ERROR] Unhandled exception: "
                    f"{type(e).__name__}: {e}"
                )
                if ui is not None:
                    ui.write(error_msg)
                else:
                    output_fn(error_msg)
                # Continue session

        except (KeyboardInterrupt, EOFError):
            msg = "\nBye!\n"
            if ui is not None:
                ui.write(msg)
            else:
                output_fn(msg)
            break


def main() -> None:
    """Main entry point for RepOS CLI."""
    # Determine DB path to boot against.
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        _project_id, db_path = init_project(cwd=Path.cwd())
    else:
        db_path = ensure_active_db(cwd=Path.cwd())

    # Create store with resolved DB
    store = SQLiteStore(db_path)

    # Explicit wiring: config + executor + store injected into kernel
    cfg = config.load_system_config()
    executor = SubprocessExecutor(force_color=True)
    kernel = Kernel(store=store, executor=executor, config=cfg)

    # Start kernel (do NOT include prompt; comes from ui.read())
    start_output = kernel.start(include_prompt=False)

    # If user explicitly disables prompt_toolkit UI:
    if os.environ.get("REPOS_LEGACY_UI") == "1":
        if start_output:
            print(start_output)
        run_repl(kernel)
        return

    # Default: PromptToolkitUI (keeps terminal scrollback/copy/select)
    ui = PromptToolkitUI(kernel)

    # Route streaming output through UI (executor/kernel may call these)
    kernel.output_fn = ui.write
    kernel.error_fn = ui.write

    # Print startup message into the UI (ensure it ends cleanly)
    if start_output:
        ui.write(start_output)
        if not start_output.endswith("\n"):
            ui.write("\n")

    run_repl(kernel, ui=ui)
