from __future__ import annotations

import os
from pathlib import Path

import pytest

from repos_cli import config


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def repos_data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "repos_data_home"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REPOS_DATA_HOME", str(data))
    return data


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    p = tmp_path / "project"
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def nested_dir(project_dir: Path) -> Path:
    n = project_dir / "a" / "b" / "c"
    n.mkdir(parents=True, exist_ok=True)
    return n


def test_get_data_root_prefers_repos_data_home(
    repos_data_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    REPOS_DATA_HOME wins when present.
    """
    monkeypatch.setenv("REPOS_DATA_HOME", str(repos_data_home))
    assert config.get_data_root() == repos_data_home


def test_get_data_root_defaults_to_local_share_and_ignores_xdg(
    tmp_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    If REPOS_DATA_HOME is not set:
    - ignore XDG_DATA_HOME
    - default to ~/.local/share
    """
    monkeypatch.delenv("REPOS_DATA_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_home / "xdg_should_be_ignored"))

    expected = Path(os.path.expanduser("~")) / ".local" / "share"
    assert config.get_data_root() == expected


def test_core_db_path_is_under_data_root(
    repos_data_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Core DB path must be:
      <data_root>/repos/core.db
    """
    monkeypatch.setenv("REPOS_DATA_HOME", str(repos_data_home))
    data_root = config.get_data_root()
    assert config.core_db_path(data_root) == repos_data_home / "repos" / "core.db"


def test_project_db_path_is_under_data_root_db_folder(
    repos_data_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Project DB path must be:
      <data_root>/repos/db/<project_id>.db
    """
    monkeypatch.setenv("REPOS_DATA_HOME", str(repos_data_home))
    data_root = config.get_data_root()
    assert (
        config.project_db_path(data_root, "6f2a9c1e")
        == repos_data_home / "repos" / "db" / "6f2a9c1e.db"
    )


def test_find_project_root_walks_up_for_repos_file(
    project_dir: Path,
    nested_dir: Path,
) -> None:
    """
    find_project_root must walk upward from cwd and return the directory
    containing the .repos file, or None.
    """
    (project_dir / ".repos").write_text("{}", encoding="utf-8")
    assert config.find_project_root(nested_dir) == project_dir


def test_find_project_root_returns_none_when_missing(tmp_path: Path) -> None:
    """
    If there is no .repos file in any parent directories, return None.
    """
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    assert config.find_project_root(outside) is None


def test_load_project_config_reads_json(project_dir: Path) -> None:
    """
    load_project_config must parse .repos JSON file.
    """
    payload = {
        "project_id": "6f2a9c1e",
        "project_name": "rep-os",
        "repos_data_home": None,
        "metadata": {"created_at": "x", "created_by": "local", "project_root": str(project_dir)},
    }
    (project_dir / ".repos").write_text(__import__("json").dumps(payload), encoding="utf-8")

    cfg = config.load_project_config(project_dir)
    assert cfg["project_id"] == "6f2a9c1e"
    assert cfg["project_name"] == "rep-os"
    assert "metadata" in cfg


def test_resolve_repos_data_home_override_relative_path(project_dir: Path) -> None:
    """
    If repos_data_home is set to a relative path (e.g. '.'),
    resolve it relative to project_root.
    """
    cfg = {"repos_data_home": "."}
    assert config.resolve_repos_data_home(cfg, project_dir) == project_dir


def test_resolve_repos_data_home_override_null_returns_none(project_dir: Path) -> None:
    """
    If repos_data_home is null/missing, returns None meaning "use global default".
    """
    assert config.resolve_repos_data_home({"repos_data_home": None}, project_dir) is None
    assert config.resolve_repos_data_home({}, project_dir) is None


# ----------------------------------------------------------------
# YAMLConfig property tests
# ----------------------------------------------------------------


def test_yaml_config_execution_property():
    """YAMLConfig.execution should return execution config dict."""
    cfg = config.YAMLConfig({"execution": {"timeout": 30}})

    execution_cfg = cfg.execution
    assert execution_cfg == {"timeout": 30}


def test_yaml_config_ui_property():
    """YAMLConfig.ui should return ui config dict."""
    cfg = config.YAMLConfig({"ui": {"theme": "dark"}})

    ui_cfg = cfg.ui
    assert ui_cfg == {"theme": "dark"}


def test_yaml_config_ui_property_returns_empty_dict_for_invalid():
    """YAMLConfig.ui should return empty dict if ui is not a dict."""
    cfg = config.YAMLConfig({"ui": "invalid"})

    ui_cfg = cfg.ui
    assert ui_cfg == {}


def test_yaml_config_get_method():
    """YAMLConfig.get should retrieve top-level config values."""
    cfg = config.YAMLConfig({"custom_key": "custom_value"})

    assert cfg.get("custom_key") == "custom_value"
    assert cfg.get("missing_key", "default") == "default"


def test_yaml_config_get_path_method():
    """YAMLConfig.get_path should retrieve nested config values using dot notation."""
    cfg = config.YAMLConfig({"ui": {"theme": {"style": "monokai"}}})

    assert cfg.get_path("ui.theme.style") == "monokai"
    assert cfg.get_path("ui.theme.missing", "default") == "default"


def test_yaml_config_get_path_with_empty_path():
    """YAMLConfig.get_path should return default for empty path."""
    cfg = config.YAMLConfig({"key": "value"})

    assert cfg.get_path("", "default") == "default"


def test_yaml_config_get_path_with_non_dict():
    """YAMLConfig.get_path should return default if intermediate value is not dict."""
    cfg = config.YAMLConfig({"ui": "not_a_dict"})

    assert cfg.get_path("ui.theme.style", "default") == "default"
