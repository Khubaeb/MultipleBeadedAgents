"""`ensure_layout` tests (AC #5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mba_primitives import records_layout
from mba_primitives.records_layout import LayoutError


def test_ensure_layout_creates_three_directories(tmp_path: Path) -> None:
    layout = records_layout.ensure_layout(
        "example-004.1", ["engineer", "auditor"], base_dir=tmp_path
    )
    assert (tmp_path / ".mba-work" / "example-004.1" / "orchestrator").is_dir()
    assert (tmp_path / ".mba-work" / "example-004.1" / "final").is_dir()
    assert (tmp_path / ".mba-work" / "example-004.1" / "engineer").is_dir()
    assert (tmp_path / ".mba-work" / "example-004.1" / "auditor").is_dir()
    assert layout["bead_dir"] == tmp_path / ".mba-work" / "example-004.1"
    assert layout["orchestrator"] == tmp_path / ".mba-work" / "example-004.1" / "orchestrator"
    assert layout["final"] == tmp_path / ".mba-work" / "example-004.1" / "final"
    assert layout["sessions"]["engineer"] == tmp_path / ".mba-work" / "example-004.1" / "engineer"
    assert layout["sessions"]["auditor"] == tmp_path / ".mba-work" / "example-004.1" / "auditor"


def test_ensure_layout_preserves_existing_directories(tmp_path: Path) -> None:
    orch = tmp_path / ".mba-work" / "example-004.1" / "orchestrator"
    orch.mkdir(parents=True)
    sentinel = orch / "sentinel.md"
    sentinel.write_text("do not delete\n", encoding="utf-8")

    engineer = tmp_path / ".mba-work" / "example-004.1" / "engineer"
    engineer.mkdir(parents=True)
    working = engineer / "working.md"
    working.write_text("prior working\n", encoding="utf-8")

    layout = records_layout.ensure_layout(
        "example-004.1", ["engineer"], base_dir=tmp_path
    )

    assert sentinel.is_file()
    assert sentinel.read_text(encoding="utf-8") == "do not delete\n"
    assert working.read_text(encoding="utf-8") == "prior working\n"
    assert layout["orchestrator"] == orch
    assert layout["sessions"]["engineer"] == engineer


def test_ensure_layout_is_idempotent(tmp_path: Path) -> None:
    records_layout.ensure_layout("example-004.1", ["engineer"], base_dir=tmp_path)
    records_layout.ensure_layout("example-004.1", ["engineer"], base_dir=tmp_path)
    records_layout.ensure_layout(
        "example-004.1", ["engineer", "auditor"], base_dir=tmp_path
    )
    assert (tmp_path / ".mba-work" / "example-004.1" / "orchestrator").is_dir()
    assert (tmp_path / ".mba-work" / "example-004.1" / "engineer").is_dir()
    assert (tmp_path / ".mba-work" / "example-004.1" / "auditor").is_dir()
    assert (tmp_path / ".mba-work" / "example-004.1" / "final").is_dir()


def test_ensure_layout_handles_empty_session_list(tmp_path: Path) -> None:
    layout = records_layout.ensure_layout("example-004.1", [], base_dir=tmp_path)
    assert (tmp_path / ".mba-work" / "example-004.1" / "orchestrator").is_dir()
    assert (tmp_path / ".mba-work" / "example-004.1" / "final").is_dir()
    assert layout["sessions"] == {}


def test_ensure_layout_refuses_empty_bead_id(tmp_path: Path) -> None:
    with pytest.raises(LayoutError):
        records_layout.ensure_layout("", ["engineer"], base_dir=tmp_path)


def test_ensure_layout_refuses_path_separators(tmp_path: Path) -> None:
    with pytest.raises(LayoutError):
        records_layout.ensure_layout("../evil", ["engineer"], base_dir=tmp_path)
    with pytest.raises(LayoutError):
        records_layout.ensure_layout("example-004.1", ["engineer/sub"], base_dir=tmp_path)


def test_ensure_layout_refuses_empty_session_name(tmp_path: Path) -> None:
    with pytest.raises(LayoutError):
        records_layout.ensure_layout("example-004.1", [""], base_dir=tmp_path)
