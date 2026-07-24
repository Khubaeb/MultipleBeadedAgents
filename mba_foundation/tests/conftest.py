"""Shared pytest fixtures for the Foundation test suite."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

# Make the package importable when tests run from the repo root without
# a developer-level `pip install -e .` step. This mirrors the strict-
# portable rule: stdlib-only code that the user can run without any
# optional deps.
PACKAGE_PARENT = Path(__file__).resolve().parents[2]
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))


@pytest.fixture()
def workspace_dir(tmp_path: Path) -> Path:
    """An isolated empty workspace for the test."""

    return tmp_path


@pytest.fixture()
def fake_beads_dir(workspace_dir: Path) -> Path:
    """A workspace with a synthetic valid `.beads/` folder.

    Mirrors the structure `bd init` produces in embedded-Dolt mode so the
    detect / sync-guard / install-block paths can exercise the happy case.
    """

    beads = workspace_dir / ".beads"
    beads.mkdir()
    (beads / "metadata.json").write_text(
        '{"database":"dolt","backend":"dolt","dolt_mode":"embedded",'
        '"dolt_database":"test","project_id":"unit-test"}',
        encoding="utf-8",
    )
    (beads / "config.yaml").write_text(
        "actor: \"test\"\n"
        "sync.remote: \"\"\n"
        "export.auto: false\n",
        encoding="utf-8",
    )
    (beads / "issues.jsonl").write_text("", encoding="utf-8")
    return beads


def is_bd_available() -> bool:
    """True iff the ``bd`` binary is on PATH."""

    return shutil.which("bd") is not None


@pytest.fixture()
def skip_unless_bd_available():
    if not is_bd_available():
        pytest.skip("`bd` binary not on PATH")


@pytest.fixture()
def allow_permission() -> None:
    """Stat helper used by tests that need to delete AGENTS.md before
    re-creating it (Windows file locks)."""

    if os.name == "nt":
        # noop on Windows; chmod is unreliable there.
        return
