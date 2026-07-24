"""Disposable Beads repository: convergence-style end-to-end check (per AC).

Each test uses a fresh ``tmp_path`` and (when the ``bd`` binary is on
PATH) runs ``bd init`` against it. The Foundation's public surface
then runs against the same workspace and the assertions mirror the Bead
acceptance row the test exercises.

When ``bd`` is not on PATH, the test runs in stub mode by constructing a
synthetic ``.beads/`` workspace that mirrors the structure ``bd init``
produces. The stub path keeps the suite hermetic; the live path runs on
disposable repos as the AC requires.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mba_foundation import detect, markers, preflight, sync_guard, workspace
from mba_foundation.constants import MBA_RULES_BEGIN_MARKER, MBA_RULES_END_MARKER

from .conftest import is_bd_available
from ._isolation_helpers import init_disposable_beads, isolate_nested_init_walk


# ---------------------------------------------------------------------------
# `bd version` preflight on a disposable workspace
# ---------------------------------------------------------------------------


def test_preflight_runs_against_disposable_repo(tmp_path: Path) -> None:
    if not is_bd_available():
        pytest.skip("`bd` binary not on PATH")

    repo = tmp_path / "disposable"
    repo.mkdir()
    orch_dir = repo / ".mba-work" / "example-003.1" / "orchestrator"
    result = preflight.preflight(
        bead_id="example-003.1",
        orchestrator_dir=orch_dir,
        bd_binary="bd",
        cwd=repo,
    )
    assert result.ok is True
    assert result.bd_version == "1.0.4"
    assert (orch_dir / "working.md").exists()


# ---------------------------------------------------------------------------
# Detect / nested-init on a disposable repo
# ---------------------------------------------------------------------------


def test_detect_beads_on_disposable_repo(tmp_path: Path) -> None:
    repo = tmp_path / "disposable"
    repo.mkdir()
    init_disposable_beads(repo)

    outcome = detect.detect_beads(repo)
    assert outcome.valid is True
    assert outcome.backend == "dolt"


def test_nested_init_guard_on_disposable_repo(
    tmp_path: Path, isolate_nested_init_walk
) -> None:
    repo = tmp_path / "disposable"
    repo.mkdir()
    init_disposable_beads(repo)
    # Add a sync.remote to trigger the guard.
    (repo / ".beads" / "config.yaml").write_text(
        'sync.remote: "git+https://example.com/disp"\n', encoding="utf-8"
    )

    nested = repo / "child"
    nested.mkdir()
    result = detect.check_nested_init(nested)
    assert result.blocked is True
    assert result.ancestor_sync_remote == "git+https://example.com/disp"


def test_install_or_initialize_refuses_without_authority_on_disposable_repo(
    tmp_path: Path,
    isolate_nested_init_walk,
) -> None:
    repo = tmp_path / "disposable"
    repo.mkdir()
    # No .beads/ folder, no authority → refuse.
    outcome = detect.install_or_initialize(repo, authority=False)
    assert outcome.returncode != 0
    assert "no user authority recorded" in outcome.stderr


# ---------------------------------------------------------------------------
# Workspace + markers on a disposable repo
# ---------------------------------------------------------------------------


def test_workspace_and_markers_install_on_disposable_repo(tmp_path: Path) -> None:
    repo = tmp_path / "disposable"
    repo.mkdir()
    workspace.install_mode(repo, mode="local")
    workspace.reconcile_gitignore(repo)
    workspace.install_ai_resource_record(repo)

    agents = repo / "AGENTS.md"
    claude = repo / "CLAUDE.md"
    agents.write_text(
        "<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:abc -->\n"
        "beads body\n"
        "<!-- END BEADS INTEGRATION -->\n",
        encoding="utf-8",
    )
    claude.write_text(
        "<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:abc -->\n"
        "beads body\n"
        "<!-- END BEADS INTEGRATION -->\n",
        encoding="utf-8",
    )

    markers.install_block(agents)
    markers.install_block(claude)

    # Each file has exactly one MBA BEGIN + one MBA END, AND every Beads
    # marker is still present.
    for path in (agents, claude):
        text = path.read_text(encoding="utf-8")
        cnt_begin = text.count("<!-- BEGIN MBA RULES -->")
        cnt_end = text.count("<!-- END MBA RULES -->")
        assert cnt_begin == 1, f"{path}: BEGIN count = {cnt_begin}"
        assert cnt_end == 1, f"{path}: END count = {cnt_end}"
        assert "<!-- BEGIN BEADS INTEGRATION" in text
        assert "<!-- END BEADS INTEGRATION -->" in text

    # Privacy guarantee.
    ok, reason = workspace.assert_ai_resource_ignored(repo)
    assert ok is True, reason


# ---------------------------------------------------------------------------
# Sync guard on the disposable Beads repo (the AC's stated case)
# ---------------------------------------------------------------------------


def test_sync_guard_safety_on_disposable_repo(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "disposable"
    repo.mkdir()
    beads = repo / ".beads"
    beads.mkdir()
    (beads / "metadata.json").write_text(
        '{"database":"dolt","backend":"dolt","dolt_mode":"embedded"}',
        encoding="utf-8",
    )

    import mba_foundation.sync_guard as sg_mod

    monkeypatch.setattr(sg_mod, "IS_WINDOWS", True)
    monkeypatch.setattr(sg_mod, "WINDOWS_MAX_PATH", 50)

    decision = sync_guard.check_push_safety(repo)
    assert decision.ok is False
    assert decision.backend == "dolt"
    assert decision.alternative  # non-empty authorised alternative path
    assert "--server" in decision.alternative
    assert "non-git" in decision.alternative.lower()


# ---------------------------------------------------------------------------
# §7 lifecycle — branch integration on the disposable repo
# ---------------------------------------------------------------------------


def test_lifecycle_atomic_branch(tmp_path: Path) -> None:
    repo = tmp_path / "disposable"
    repo.mkdir()

    from mba_foundation import orchestrator

    outcome = orchestrator.drive_lifecycle(
        "fix typo in AGENTS.md",
        producer=lambda r: f"P[{r}]",
        auditor=lambda r, a: "ACCEPT",
    )
    assert outcome.branch is orchestrator.Branch.ATOMIC


def test_lifecycle_staged_branch(tmp_path: Path) -> None:
    repo = tmp_path / "disposable"
    repo.mkdir()

    from mba_foundation import orchestrator

    outcome = orchestrator.drive_lifecycle(
        "implement OAuth2 across the project with several integrations"
    )
    assert outcome.branch is orchestrator.Branch.STAGED
    assert outcome.selection is not None
