"""Tests for worker/disposable workspace safety guards."""

from __future__ import annotations

from pathlib import Path

import pytest

from mba_runtime.workspace_safety import (
    WorkspaceSafetyError,
    assert_disposable_workspace_safe,
    assert_no_destructive_bd_init,
    check_disposable_workspace,
    contains_destructive_bd_init,
)


def test_disposable_workspace_rejects_ancestor_beads(tmp_path: Path) -> None:
    (tmp_path / ".beads").mkdir()
    child = tmp_path / "child" / "run"
    child.mkdir(parents=True)

    result = check_disposable_workspace(child)
    assert result.safe is False
    assert result.ancestor_beads == tmp_path / ".beads"
    with pytest.raises(WorkspaceSafetyError, match="ancestor .beads"):
        assert_disposable_workspace_safe(child)


def test_disposable_workspace_accepts_clean_root(authorized_workspace: Path) -> None:
    result = assert_disposable_workspace_safe(authorized_workspace)
    assert result.safe is True
    assert result.ancestor_beads is None


def test_destructive_bd_init_guard() -> None:
    assert contains_destructive_bd_init(["bd", "init", "--reinit-local"]) is True
    assert contains_destructive_bd_init(["bd", "init", "--non-interactive"]) is False
    with pytest.raises(WorkspaceSafetyError, match="destructive Beads init"):
        assert_no_destructive_bd_init(["bd", "init", "--reinit-local"])
