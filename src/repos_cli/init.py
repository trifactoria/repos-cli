# RepOS™ — Multi-Panel REPL-Based Developer Command Environment
# Copyright (c) 2025
# TriFactoria (Andrew Blankfield)
#
# Licensed under the Business Source License 1.1 (BSL 1.1).
# You may use, modify, and redistribute this file under the terms of the BSL.
# On the Change Date (2029-01-01), this file will be licensed under
# the Apache License, Version 2.0.

"""
Initialization and database resolution for RepOS.

Responsibilities:
- Core DB creation (schema only)
- Project DB creation and registration
- Active DB resolution (core vs project)
- Profile-based alias application
- Guided initialization wizard for `repos init`

Important boundary:
- This module must NOT parse YAML directly.
- YAML/defaults are owned by repos.config.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Protocol

from . import config, db

# ----------------------------------------------------------------
# Interviewer (init-owned wizard interface)
# ----------------------------------------------------------------


class Interviewer(Protocol):
    """Minimal interface for init wizard interaction."""

    def write(self, text: str) -> None: ...

    def ask(self, prompt: str) -> str: ...


class StdIOInterviewer:
    """Default interviewer for real CLI usage (input/print)."""

    def write(self, text: str) -> None:
        print(text)

    def ask(self, prompt: str) -> str:
        return input(prompt)


# ----------------------------------------------------------------
# Public API
# ----------------------------------------------------------------


def ensure_core_db(cwd: Path) -> Path:
    """Ensure core database exists and has schema."""
    data_root = config.get_data_root()
    core_db_path = config.core_db_path(data_root)
    db.ensure_schema(core_db_path)
    return core_db_path


def ensure_active_db(cwd: Path) -> Path:
    """Resolve and ensure the active database for the current directory."""
    project_root = config.find_project_root(cwd)

    if project_root is None:
        return ensure_core_db(cwd)

    project_cfg = config.load_project_config(project_root)
    project_id = project_cfg["project_id"]
    project_name = project_cfg.get("project_name")

    project_data_home = config.resolve_repos_data_home(
        project_cfg, project_root
    )
    data_root = (
        project_data_home if project_data_home
        else config.get_data_root()
    )

    project_db_path = config.project_db_path(
        data_root, project_id, project_name
    )
    db.ensure_schema(project_db_path)

    core_db_path = ensure_core_db(cwd)
    db.update_project_location(
        core_db=core_db_path,
        project_id=project_id,
        new_root=project_root,
    )

    return project_db_path


def init_project(
    cwd: Path,
    interviewer: Interviewer | None = None,
) -> tuple[str, Path]:
    """Initialize a new RepOS project in the given directory."""
    if interviewer is None:
        interviewer = StdIOInterviewer()

    interviewer.write("Initializing RepOS project…\n")

    repos_file = cwd / ".repos"
    if repos_file.exists():
        interviewer.write(
            "An existing .repos file was found in this directory."
        )
        interviewer.write(
            "Refusing to overwrite. If you intend to re-init, "
            "remove .repos first.\n"
        )
        raise Exception("Project already initialized (.repos exists)")

    interviewer.write("No existing .repos file found.")
    interviewer.write("This will create a new RepOS project configuration.\n")

    core_db_path = ensure_core_db(cwd)

    project_id = secrets.token_hex(4)

    default_name = cwd.name
    raw_name = interviewer.ask(f"Project name [{default_name}]: ").strip()
    project_name = raw_name if raw_name else default_name
    interviewer.write(f"\nProject name set to: {project_name}\n")

    mode = _choose_mode(interviewer)

    included_profiles: list[str] = []
    skipped_profiles: list[str] = []

    available = _discover_profiles()

    if mode == "minimal":
        interviewer.write(
            "\nYou can optionally include base panels and aliases."
        )
        interviewer.write("Each panel is defined by a YAML profile.\n")
        interviewer.write(
            "For each panel, the definition will be shown before "
            "you choose.\n"
        )

        for profile_name in available:
            profile_data = _load_profile(profile_name)
            if not profile_data:
                continue

            preview = _build_profile_preview(profile_name, profile_data)
            interviewer.write(preview)

            ans = (
                interviewer.ask(
                    f"Would you like to include {profile_name} "
                    f"aliases? [y/N]: "
                )
                .strip()
                .lower()
            )
            if ans in {"y", "yes"}:
                included_profiles.append(profile_name)
                interviewer.write(
                    f"\n✔ {profile_name.capitalize()} "
                    f"will be included\n"
                )
            else:
                skipped_profiles.append(profile_name)
                interviewer.write(f"\n✘ {profile_name.capitalize()} skipped\n")
    else:
        interviewer.write("\nNo panels or aliases will be created.")
        interviewer.write("You can add everything manually later.\n")
        skipped_profiles = list(available)

    data_root = config.get_data_root()
    project_db_path = config.project_db_path(
        data_root, project_id, project_name
    )

    summary = _build_initialization_summary(
        project_name=project_name,
        project_id=project_id,
        mode=mode,
        included=included_profiles,
        skipped=skipped_profiles,
        core_db_path=core_db_path,
        project_db_path=project_db_path,
        repos_file=repos_file,
    )
    interviewer.write("\n" + summary + "\n")

    confirm = (
        interviewer.ask("Proceed with initialization? [Y/n]: ")
        .strip()
        .lower()
    )
    if confirm in {"n", "no"}:
        interviewer.write("\nInitialization aborted.\n")
        raise Exception("Initialization aborted by user")

    repos_config = {
        "project_id": project_id,
        "project_name": project_name,
        "repos_data_home": None,
        "seeded_profiles": included_profiles,
        "metadata": {
            "created_at": datetime.now().isoformat(),
            "created_by": "local",
            "project_root": str(cwd),
        },
    }
    repos_file.write_text(json.dumps(repos_config, indent=2), encoding="utf-8")

    db.ensure_schema(project_db_path)

    if included_profiles:
        _apply_profiles(project_db_path, included_profiles)

    db.register_project(
        core_db=core_db_path,
        project_id=project_id,
        project_name=project_name,
        origin_root_path=cwd,
        last_known_root_path=cwd,
        project_db_path=project_db_path,
    )

    interviewer.write("\n✔ Created .repos configuration file")
    interviewer.write("✔ Created project database")
    interviewer.write("✔ Registered project in core database\n")
    interviewer.write("RepOS project initialized successfully.\n")

    return project_id, project_db_path


# ----------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------


def _choose_mode(interviewer: Interviewer) -> str:
    interviewer.write("Choose initialization mode:\n")
    interviewer.write("  1) minimal  – start with no aliases (recommended)")
    interviewer.write("  2) blank    – empty project, no panels or aliases\n")

    while True:
        choice = interviewer.ask("Select [1/2]: ").strip()
        if choice == "1":
            interviewer.write("\nSelected mode: minimal\n")
            return "minimal"
        if choice == "2":
            interviewer.write("\nSelected mode: blank\n")
            return "blank"
        interviewer.write("\nPlease choose 1 or 2.\n")


def _discover_profiles() -> list[str]:
    """Discover available profile YAMLs from packaged defaults."""
    return config.discover_profiles()


def _load_profile(profile_name: str) -> dict | None:
    """Load a profile dict via config boundary.

    Returns None if invalid/unusable.
    """
    try:
        data = config.load_profile(profile_name)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None
    if "aliases" not in data or not isinstance(data.get("aliases"), list):
        return None
    return data


def _build_profile_preview(profile_name: str, profile_data: dict) -> str:
    sep = "────────────────────────────────────────────"
    lines: list[str] = [sep]
    lines.append(f"Panel: {profile_name}")
    lines.append("")
    desc = profile_data.get("description")
    if isinstance(desc, str) and desc.strip():
        lines.append("Description:")
        lines.append(f"  {desc.strip()}")
        lines.append("")

    lines.append("Aliases:")
    aliases = profile_data.get("aliases", [])
    if not aliases:
        lines.append("  (none)")
    else:
        for alias_def in aliases:
            name = alias_def.get("name")
            cmd = alias_def.get("command")
            if isinstance(name, str) and name and isinstance(cmd, str) and cmd:
                lines.append(f"  {name:<4} → {cmd}")

    lines.append(sep)
    lines.append("")
    return "\n".join(lines)


def _build_initialization_summary(
    *,
    project_name: str,
    project_id: str,
    mode: str,
    included: list[str],
    skipped: list[str],
    core_db_path: Path,
    project_db_path: Path,
    repos_file: Path,
) -> str:
    lines: list[str] = []
    lines.append("Initialization summary:\n")
    lines.append(f"Project name: {project_name}")
    lines.append(f"Project ID:   {project_id}")
    lines.append(f"Mode:         {mode}\n")

    lines.append("Included panels:")
    if included:
        for p in included:
            lines.append(f"  • {p.capitalize()}")
    else:
        lines.append("  • (none)")

    lines.append("\nSkipped panels:")
    if skipped:
        for p in skipped:
            lines.append(f"  • {p.capitalize()}")
    else:
        lines.append("  • (none)")

    lines.append("")
    lines.append(f"Core database:    {core_db_path}")
    lines.append(f"Project database: {project_db_path}")
    lines.append(f"Project config:   {repos_file}")

    return "\n".join(lines)


def _get_reserved_triggers() -> set[str]:
    """Get all reserved command triggers that cannot be used as alias names.

    Returns:
        Set of reserved trigger strings (base commands + special builtins)
    """
    # Load system config to get base command triggers
    from . import config as cfg_module

    system_config = cfg_module.load_system_config()
    reserved = set()

    # Collect all base command triggers from config
    if (hasattr(system_config, "commands") and
            isinstance(system_config.commands, dict)):
        base_cmds = system_config.commands.get("base", {})
        if isinstance(base_cmds, dict):
            for _cmd_name, cmd_cfg in base_cmds.items():
                triggers = cmd_cfg.get("triggers", []) or []
                reserved.update(triggers)

        # Collect help triggers
        help_cfg = system_config.commands.get("help", {})
        if isinstance(help_cfg, dict):
            help_triggers = help_cfg.get("triggers", []) or []
            reserved.update(help_triggers)

    # Add special built-ins
    reserved.update({"Z", "ZZ", "cls", "DB", "USE", "WHERE", "INFO", "REP"})

    return reserved


def _apply_profiles(db_path: Path, profile_names: list[str]) -> None:
    """Apply profiles into the given DB by inserting aliases.

    Uses direct sqlite operations.
    """
    conn = sqlite3.connect(str(db_path))
    reserved = _get_reserved_triggers()

    try:
        for profile_name in profile_names:
            profile_data = _load_profile(profile_name)
            if not isinstance(profile_data, dict):
                continue

            panel = profile_data.get("panel")
            aliases = profile_data.get("aliases", [])
            if not panel or not isinstance(aliases, list):
                continue

            for alias_def in aliases:
                name = alias_def.get("name")
                cmd = alias_def.get("command")
                if not (isinstance(name, str) and name and
                        isinstance(cmd, str) and cmd):
                    continue

                # Skip reserved triggers and warn
                if name in reserved:
                    print(
                        f'Skipping alias "{name}" in panel {panel}: '
                        f'reserved base command trigger.'
                    )
                    continue

                now = datetime.now().isoformat()
                alias_key = panel.lower() + name
                conn.execute(
                    """
                    INSERT OR REPLACE INTO aliases
                    (panel, name, alias_key, command, created_at,
                     updated_at, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                    """,
                    (panel, name, alias_key, cmd, now, now),
                )
        conn.commit()
    finally:
        conn.close()
