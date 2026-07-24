"""Shared pytest fixtures for the Primitives test suite."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

PACKAGE_PARENT = Path(__file__).resolve().parents[2]
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))


def is_bd_available() -> bool:
    return shutil.which("bd") is not None


@pytest.fixture()
def workspace_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def fake_beads_dir(workspace_dir: Path) -> Path:
    beads = workspace_dir / ".beads"
    beads.mkdir()
    (beads / "metadata.json").write_text(
        '{"database":"dolt","backend":"dolt","dolt_mode":"embedded",'
        '"dolt_database":"test","project_id":"unit-test"}',
        encoding="utf-8",
    )
    (beads / "config.yaml").write_text(
        'actor: "test"\nsync.remote: ""\nexport.auto: false\n',
        encoding="utf-8",
    )
    (beads / "issues.jsonl").write_text("", encoding="utf-8")
    return beads


@pytest.fixture()
def skip_unless_bd_available():
    if not is_bd_available():
        pytest.skip("`bd` binary not on PATH")
