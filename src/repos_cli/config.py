# RepOS™ — Multi-Panel REPL-Based Developer Command Environment
# Copyright (c) 2025
# TriFactoria (Andrew Blankfield)
#
# Licensed under the Business Source License 1.1 (BSL 1.1).
# You may use, modify, and redistribute this file under the terms of the BSL.
# On the Change Date (2029-01-01), this file will be licensed under
# the Apache License, Version 2.0.

"""
Filesystem discovery and data root resolution for RepOS.

Handles:
- Data root resolution (REPOS_DATA_HOME, ~/.local/share)
- DB path helpers (core, project)
- Project anchoring via .repos file discovery
- Per-project data root override resolution
- Packaged YAML defaults loading (repos.defaults/*.yaml)
- Branding + ANSI coloring constants + UI_CLEAR semantic sentinel
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

try:
    # Py3.9+
    from importlib import resources as importlib_resources
except Exception:  # pragma: no cover
    import importlib_resources  # type: ignore


# -----------------------
# UI + branding constants
# -----------------------

# ANSI color codes for branding (moved from kernel.py)
ANSI_COLORS: dict[str, str] = {
    "cyan": "\033[38;5;69;1m",
    "pink": "\033[38;5;169;1m",
    "magenta": "\033[38;5;126;1m",
    "yellow": "\033[38;5;226;1m",
    "orange": "\033[38;2;255;165;1;1m",
    "purple": "\033[38;5;96;1m",
    "docker_blue": "\033[38;5;67;1m",
    "node_green": "\033[38;5;40;1m",
    "conda_green": "\033[38;5;121;1m",
    "ruby_red": "\033[38;5;1;1m",
    "reset": "\033[0m",
    "dim": "\033[2m",
    "green": "\033[32m",
    "red": "\033[31m",
}

TAG_COLORS: dict[str, str] = {
    "RUN": "green",
    "EXIT": "magenta",
    "ERR": "red",
    "HISTORY": "cyan",
    "HIST": "cyan",
}

# Semantic UI intent for clear screen operations
UI_CLEAR = "__UI_CLEAR__"


# -----------------------
# Config model wrapper
# -----------------------


class YAMLConfig:
    """Simple config wrapper that implements ConfigModel protocol."""

    def __init__(self, config_dict: dict[str, Any]):
        self._config = config_dict
        # Derive branding from panels
        self._branding: dict[str, dict[str, str]] = {}
        for panel_name, panel_cfg in config_dict.get("panels", {}).items():
            entry = panel_cfg.get("entry")
            branding_info = {
                "panel_color": panel_cfg.get("panel_color"),
                "caret_color": panel_cfg.get("caret_color"),
            }
            self._branding[panel_name] = branding_info
            if entry and entry != panel_name:
                self._branding[entry] = branding_info

    @property
    def panels(self) -> dict[str, dict[str, Any]]:
        return self._config.get("panels", {})

    @property
    def commands(self) -> dict[str, Any]:
        return self._config.get("commands", {})

    @property
    def branding(self) -> dict[str, dict[str, str]]:
        return self._branding

    @property
    def system(self) -> dict[str, Any]:
        return self._config.get("system", {})

    @property
    def execution(self) -> dict[str, Any]:
        return self._config.get("execution", {})

    @property
    def ui(self) -> dict[str, Any]:
        ui_cfg = self._config.get("ui", {})
        return ui_cfg if isinstance(ui_cfg, dict) else {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def get_path(self, path: str, default: Any = None) -> Any:
        """
        Nested lookup using dot-separated path.
        Example: get_path("ui.theme.style", {}) -> dict style mapping
        """
        if not path:
            return default

        cur: Any = self._config
        for part in path.split("."):
            if not isinstance(cur, dict):
                return default
            if part not in cur:
                return default
            cur = cur[part]
        return cur


# -----------------------
# Data root + DB helpers
# -----------------------


def get_data_root() -> Path:
    """Get the data root directory for RepOS.

    Resolution order:
    1. REPOS_DATA_HOME environment variable (if set)
    2. ~/.local/share (default, XDG_DATA_HOME is ignored)
    """
    repos_data_home = os.getenv("REPOS_DATA_HOME")
    if repos_data_home:
        root = Path(repos_data_home)
    else:
        root = Path.home() / ".local" / "share"

    root.mkdir(parents=True, exist_ok=True)
    return root


def core_db_path(data_root: Path) -> Path:
    """<data_root>/repos/core.db"""
    return data_root / "repos" / "core.db"


def slugify(text: str) -> str:
    """Convert text to a URL-safe slug.

    Rules:
    - lowercase
    - replace any non [a-z0-9] with '-'
    - collapse multiple '-'
    - trim leading/trailing '-'
    - if empty after slugify, use "project"

    Args:
        text: Input text to slugify

    Returns:
        Slugified string
    """
    import re

    # Convert to lowercase
    slug = text.lower()

    # Replace any non-alphanumeric with hyphen
    slug = re.sub(r"[^a-z0-9]+", "-", slug)

    # Collapse multiple hyphens
    slug = re.sub(r"-+", "-", slug)

    # Trim leading/trailing hyphens
    slug = slug.strip("-")

    # Fallback to "project" if empty
    if not slug:
        slug = "project"

    return slug


def make_project_db_filename(project_name: str, project_id: str) -> str:
    """Create a human-readable project database filename.

    Format: {slug(project_name)}-{project_id}.db

    Args:
        project_name: Human-readable project name
        project_id: Unique project identifier (typically 8-char hex)

    Returns:
        Filename string (e.g., "rep-os-b7881729.db")
    """
    slug = slugify(project_name)
    return f"{slug}-{project_id}.db"


def project_db_path(
    data_root: Path, project_id: str, project_name: str | None = None
) -> Path:
    """Get the path to a project database.

    If project_name is provided, uses new naming convention: {slug}-{id}.db
    If project_name is None, uses legacy naming: {id}.db

    Args:
        data_root: Root data directory
        project_id: Unique project identifier
        project_name: Optional project name for human-readable filenames

    Returns:
        Path to project database file
    """
    db_dir = data_root / "repos" / "db"

    if project_name is not None:
        filename = make_project_db_filename(project_name, project_id)
    else:
        # Legacy naming for backward compatibility
        filename = f"{project_id}.db"

    return db_dir / filename


def find_project_root(cwd: Path) -> Path | None:
    """Find the project root by walking up from cwd looking for .repos file."""
    current = cwd.resolve()

    while True:
        repos_file = current / ".repos"
        if repos_file.exists():
            return current

        parent = current.parent
        if parent == current:
            return None

        current = parent


def load_project_config(project_root: Path) -> dict:
    """Load and parse the .repos JSON configuration file."""
    repos_file = project_root / ".repos"
    content = repos_file.read_text(encoding="utf-8")
    return json.loads(content)


def resolve_repos_data_home(cfg: dict, project_root: Path) -> Path | None:
    """Resolve the repos_data_home override from project config."""
    repos_data_home = cfg.get("repos_data_home")

    if repos_data_home is None:
        return None

    path = Path(repos_data_home)
    if not path.is_absolute():
        path = project_root / path

    return path.resolve()


# -----------------------
# Packaged defaults loading
# -----------------------


def _defaults_dir() -> Path:
    """Return the installed path to packaged defaults directory.

    Looks in repos/defaults.
    """
    return Path(
        importlib_resources.files("repos_cli.defaults")
    )  # type: ignore[arg-type]


def load_defaults_yaml(filename: str) -> dict[str, Any]:
    """
    Load a YAML file from repos/defaults/.
    """
    defaults_dir = _defaults_dir()
    path = defaults_dir / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Missing defaults YAML: {filename} "
            f"(looked in {defaults_dir})"
        )

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        raise ValueError(
            f"Defaults YAML {filename} must load to a mapping/dict."
        )
    return data


def load_system_config() -> YAMLConfig:
    """
    Load system.yaml from packaged defaults and return a YAMLConfig wrapper.
    """
    return YAMLConfig(load_defaults_yaml("system.yaml"))


def discover_profiles() -> list[str]:
    """
    Discover profile yamls in repos/defaults (excluding system.yaml).
    Returns stems, e.g. ["git", "docker"].
    """
    defaults_dir = _defaults_dir()
    names: list[str] = []
    for p in defaults_dir.glob("*.yaml"):
        if p.name == "system.yaml":
            continue
        names.append(p.stem)
    names.sort()
    return names


def load_profile(name: str) -> dict[str, Any]:
    """Load a profile yaml by stem name from repos/defaults.

    E.g. "git" -> git.yaml
    """
    return load_defaults_yaml(f"{name}.yaml")
