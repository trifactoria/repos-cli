# tests/test_init.py
"""
RepOS — Initialization & DB Resolution Tests
(Init Wizard UX + DB resolution)

This suite enforces the bootstrap contract for repos.init:

- init.py is bootstrap-only (no CLI/UI/Kernel/Store imports)
- ensure_active_db resolves core vs project DB via filesystem discovery
- init_project runs the interactive init wizard (blank/minimal)
- minimal MUST show preview BEFORE asking to include each profile
- init writes .repos and registers project in core registry
- .repos records seeded_profiles so behavior is auditable/reproducible

Important:
- Tests MUST NOT "cheat" by passing selected_profiles directly.
  Selection must occur via ask() prompts only.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import repos_cli.init as init_mod
from repos_cli.init import ensure_active_db, ensure_core_db, init_project

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repos_data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "repos_data"
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


# ---------------------------------------------------------------------------
# Helpers (pure filesystem / sqlite only)
# ---------------------------------------------------------------------------


def core_db_path(repos_data_home: Path) -> Path:
    return repos_data_home / "repos" / "core.db"


def project_db_path(repos_data_home: Path, project_id: str, project_name: str | None = None) -> Path:
    """Generate project DB path matching the main config module."""
    from repos_cli.config import make_project_db_filename

    db_dir = repos_data_home / "repos" / "db"

    if project_name is not None:
        filename = make_project_db_filename(project_name, project_id)
    else:
        # Legacy naming
        filename = f"{project_id}.db"

    return db_dir / filename


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def table_columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cur.fetchall()}
    finally:
        conn.close()


def count_rows(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        row = cur.fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def list_alias_keys(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("SELECT alias_key FROM aliases")
        return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# IO-style Interviewer (matches src/repos/init.py: write() + ask())
# ---------------------------------------------------------------------------


@dataclass
class FakeIOInterviewer:
    """
    Drives init wizard via IO prompts:

      - write(text): collects output blocks
      - ask(prompt): returns scripted answers based on prompt text

    Also records ordering so tests can enforce:
      preview for a profile is written BEFORE we ever answer its include prompt.
    """

    # If None, accept default folder name (empty input).
    project_name: str | None = None

    # Mode selection: "1" for minimal, "2" for blank
    mode_choice: str = "1"

    # For minimal mode: map profile -> y/n
    include_profiles: dict[str, bool] = field(default_factory=dict)

    # Final confirm: True => "y", False => "n"
    confirm: bool = True

    # Captured outputs and prompts
    writes: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)

    # For enforcing preview-before-include
    preview_written_for: set[str] = field(default_factory=set)
    include_prompted_for: list[str] = field(default_factory=list)

    # Captured summary
    summary_text: str | None = None

    def write(self, text: str) -> None:
        self.writes.append(text)

        # Capture summary for later assertions
        if "Initialization summary:" in text:
            self.summary_text = text

        # Detect previews (they start with separator + "Panel: <name>")
        # init._build_profile_preview starts with a line of '─' and contains "Panel: {profile_name}"
        if "Panel:" in text and "Aliases:" in text:
            # Extract profile name from the first "Panel: X" line
            for line in text.splitlines():
                if line.startswith("Panel:"):
                    profile = line.split("Panel:", 1)[1].strip()
                    if profile:
                        self.preview_written_for.add(profile)
                    break

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)

        # Project name prompt: "Project name [<default>]: "
        if prompt.startswith("Project name ["):
            if self.project_name is None:
                return ""  # accept default
            return self.project_name

        # Mode selection: "Select [1/2]: "
        if prompt.startswith("Select [1/2]"):
            return self.mode_choice

        # Include prompt: "Would you like to include <profile> aliases? [y/N]: "
        if prompt.startswith("Would you like to include ") and " aliases?" in prompt:
            middle = prompt[len("Would you like to include ") :]
            profile = middle.split(" aliases?", 1)[0].strip()
            self.include_prompted_for.append(profile)

            # Enforce: preview must have been written before include is asked
            # (This is the UX invariant you care about.)
            assert (
                profile in self.preview_written_for
            ), f"Include prompt for '{profile}' happened before preview was written."

            want = self.include_profiles.get(profile, False)
            return "y" if want else "n"

        # Final confirmation: "Proceed with initialization? [Y/n]: "
        if prompt.startswith("Proceed with initialization?"):
            return "y" if self.confirm else "n"

        # Any unknown prompt means init flow changed
        raise AssertionError(f"Unexpected prompt: {prompt!r}")


# ---------------------------------------------------------------------------
# Hard boundary: init must not depend on Kernel/CLI/UI/Store
# ---------------------------------------------------------------------------


def test_init_module_has_no_cli_ui_kernel_or_store_dependencies() -> None:
    src = Path(init_mod.__file__).read_text(encoding="utf-8")

    forbidden = [
        "from .kernel import",
        "import repos.kernel",
        "from .ui import",
        "import repos.ui",
        "from .cli import",
        "import repos.cli",
        "from .store import",
        "import repos.store",
        "Kernel(",
        "PromptToolkitUI",
    ]
    hits = [s for s in forbidden if s in src]
    assert not hits, f"repos.init must not depend on CLI/UI/Kernel/Store. Found: {hits}"


# ---------------------------------------------------------------------------
# Core resolution behavior
# ---------------------------------------------------------------------------


def test_core_db_is_created_when_missing(repos_data_home: Path, tmp_path: Path) -> None:
    cwd = tmp_path / "outside"
    cwd.mkdir(parents=True, exist_ok=True)

    db_path = ensure_core_db(cwd=cwd)
    expected = core_db_path(repos_data_home)

    assert db_path == expected
    assert expected.exists()


def test_repos_uses_core_db_when_no_repos_file_exists(
    repos_data_home: Path, tmp_path: Path
) -> None:
    cwd = tmp_path / "outside"
    cwd.mkdir(parents=True, exist_ok=True)

    db_path = ensure_active_db(cwd=cwd)

    expected = core_db_path(repos_data_home)
    assert db_path == expected
    assert expected.exists()


def test_project_db_resolved_from_repos_file(
    repos_data_home: Path, project_dir: Path, nested_dir: Path
) -> None:
    payload = {
        "project_id": "6f2a9c1e",
        "project_name": "project",
        "repos_data_home": None,
        "metadata": {
            "created_at": "now",
            "created_by": "local",
            "project_root": str(project_dir),
        },
        "seeded_profiles": [],
    }
    (project_dir / ".repos").write_text(json.dumps(payload), encoding="utf-8")

    db_path = ensure_active_db(cwd=nested_dir)

    expected = project_db_path(repos_data_home, payload["project_id"], payload["project_name"])
    assert db_path == expected
    assert expected.exists()


# ---------------------------------------------------------------------------
# repos init behavior: filesystem + registry
# ---------------------------------------------------------------------------


def test_repos_init_creates_repos_file_with_required_fields(
    repos_data_home: Path, project_dir: Path
) -> None:
    interviewer = FakeIOInterviewer(mode_choice="2", confirm=True)  # blank
    project_id, db_path = init_project(cwd=project_dir, interviewer=interviewer)

    repos_file = project_dir / ".repos"
    assert repos_file.exists()

    payload = load_json(repos_file)
    assert payload["project_id"] == project_id
    assert isinstance(payload["project_name"], str)
    assert "repos_data_home" in payload
    assert isinstance(payload.get("metadata"), dict)
    assert "seeded_profiles" in payload
    assert isinstance(payload["seeded_profiles"], list)

    for key in ("created_at", "created_by", "project_root"):
        assert key in payload["metadata"]

    assert db_path.exists()


def test_repos_init_creates_project_db_in_central_store(
    repos_data_home: Path, project_dir: Path
) -> None:
    interviewer = FakeIOInterviewer(mode_choice="2", confirm=True)  # blank
    project_id, db_path = init_project(cwd=project_dir, interviewer=interviewer)

    # Read .repos file to get project_name
    repos_file = project_dir / ".repos"
    payload = load_json(repos_file)
    project_name = payload["project_name"]

    expected = project_db_path(repos_data_home, project_id, project_name)
    assert db_path == expected
    assert expected.exists()


def test_repos_init_creates_core_db_if_missing(repos_data_home: Path, project_dir: Path) -> None:
    core = core_db_path(repos_data_home)
    assert not core.exists()

    interviewer = FakeIOInterviewer(mode_choice="2", confirm=True)  # blank
    init_project(cwd=project_dir, interviewer=interviewer)

    assert core.exists()


def test_project_is_registered_in_core_db(repos_data_home: Path, project_dir: Path) -> None:
    interviewer = FakeIOInterviewer(mode_choice="2", confirm=True)  # blank
    project_id, _ = init_project(cwd=project_dir, interviewer=interviewer)

    core = core_db_path(repos_data_home)
    cols = table_columns(core, "projects")

    required = {
        "project_id",
        "project_name",
        "origin_root_path",
        "last_known_root_path",
        "db_path",
        "created_at",
        "last_used_at",
    }
    assert required.issubset(cols)
    assert project_id


def test_registry_updates_when_project_moves(repos_data_home: Path, tmp_path: Path) -> None:
    original = tmp_path / "original"
    original.mkdir(parents=True, exist_ok=True)

    interviewer = FakeIOInterviewer(mode_choice="2", confirm=True)  # blank
    project_id, _ = init_project(cwd=original, interviewer=interviewer)
    payload = load_json(original / ".repos")

    new_root = tmp_path / "moved"
    new_root.mkdir(parents=True, exist_ok=True)
    (new_root / ".repos").write_text(
        json.dumps({**payload, "metadata": {**payload["metadata"], "project_root": str(new_root)}}),
        encoding="utf-8",
    )

    nested = new_root / "x" / "y"
    nested.mkdir(parents=True, exist_ok=True)

    ensure_active_db(cwd=nested)

    core = core_db_path(repos_data_home)
    conn = sqlite3.connect(str(core))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT last_known_root_path FROM projects WHERE project_id = ?",
            (project_id,),
        )
        row = cur.fetchone()
        assert row is not None
        assert Path(row[0]) == new_root
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Wizard UX & seeding behavior
# ---------------------------------------------------------------------------


def test_minimal_mode_enforces_preview_before_include_prompt(
    repos_data_home: Path, project_dir: Path
) -> None:
    # minimal mode: choose 1
    interviewer = FakeIOInterviewer(
        project_name="my-project",
        mode_choice="1",
        include_profiles={},  # decline all
        confirm=True,
    )
    init_project(cwd=project_dir, interviewer=interviewer)

    # Sanity: we should have written at least one preview and asked includes
    assert interviewer.preview_written_for, "Expected at least one profile preview to be written"
    assert interviewer.include_prompted_for, "Expected include prompts for profiles"

    # Summary must be produced
    assert interviewer.summary_text is not None
    assert "Initialization summary:" in interviewer.summary_text


def test_blank_mode_seeds_nothing_and_records_seeded_profiles_empty(
    repos_data_home: Path, project_dir: Path
) -> None:
    interviewer = FakeIOInterviewer(mode_choice="2", confirm=True)  # blank
    project_id, db_path = init_project(cwd=project_dir, interviewer=interviewer)

    assert db_path.exists()
    assert count_rows(db_path, "aliases") == 0

    payload = load_json(project_dir / ".repos")
    assert payload["project_id"] == project_id
    assert payload["seeded_profiles"] == []


def test_minimal_mode_selecting_git_seeds_aliases_and_records_seeded_profiles(
    repos_data_home: Path, project_dir: Path
) -> None:
    interviewer = FakeIOInterviewer(
        mode_choice="1",  # minimal
        include_profiles={"git": True},
        confirm=True,
    )
    project_id, db_path = init_project(cwd=project_dir, interviewer=interviewer)

    assert db_path.exists()
    assert count_rows(db_path, "aliases") > 0

    keys = list_alias_keys(db_path)
    assert any(
        k in keys for k in {"gs", "ga", "gc", "gp", "gl"}
    ), f"Expected git-ish aliases, got: {sorted(keys)}"

    payload = load_json(project_dir / ".repos")
    assert payload["project_id"] == project_id
    assert "git" in payload["seeded_profiles"]


def test_minimal_mode_declining_all_seeds_nothing_and_records_seeded_profiles_empty(
    repos_data_home: Path, project_dir: Path
) -> None:
    interviewer = FakeIOInterviewer(
        mode_choice="1",  # minimal
        include_profiles={},  # all declined by default
        confirm=True,
    )
    _project_id, db_path = init_project(cwd=project_dir, interviewer=interviewer)

    assert db_path.exists()
    assert count_rows(db_path, "aliases") == 0

    payload = load_json(project_dir / ".repos")
    assert payload["seeded_profiles"] == []


def test_init_aborts_when_user_declines_final_confirmation(
    repos_data_home: Path, project_dir: Path
) -> None:
    interviewer = FakeIOInterviewer(
        mode_choice="1",
        include_profiles={"git": True},
        confirm=False,  # user says no at final confirm
    )

    with pytest.raises(Exception, match="Initialization aborted"):
        init_project(cwd=project_dir, interviewer=interviewer)

    # Should not create project config
    assert not (project_dir / ".repos").exists()

    # Should not create any project db file in central store
    db_dir = repos_data_home / "repos" / "db"
    if db_dir.exists():
        assert list(db_dir.glob("*.db")) == []
