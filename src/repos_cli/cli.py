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

            try:
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
                # Unhandled exception in command processing - write crash log
                write_crash_log(
                    e,
                    panel=kernel.panel,
                    raw_command=line,
                    resolved_command="",
                    db_name=kernel.active_db_name,
                    db_path=kernel.active_db_path,
                )
                # Show error to user
                error_msg = f"[ERROR] Unhandled exception: {type(e).__name__}: {e}"
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

    # Start kernel session (do NOT include prompt; prompt comes from ui.read())
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
