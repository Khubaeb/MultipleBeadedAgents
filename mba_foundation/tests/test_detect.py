"""Detect Beads → install/initialise + nested-init guard (AC #3, AC #4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mba_foundation import detect

from ._isolation_helpers import isolate_nested_init_walk


def test_detect_returns_invalid_when_beads_missing(tmp_path: Path) -> None:
    outcome = detect.detect_beads(tmp_path)
    assert outcome.valid is False
    assert "not present" in outcome.reason
    assert outcome.backend is None
    assert outcome.sync_remote is None


def test_detect_returns_invalid_when_metadata_missing(tmp_path: Path) -> None:
    (tmp_path / ".beads").mkdir()
    outcome = detect.detect_beads(tmp_path)
    assert outcome.valid is False
    assert "metadata.json" in outcome.reason


def test_detect_returns_invalid_on_malformed_metadata(tmp_path: Path) -> None:
    beads = tmp_path / ".beads"
    beads.mkdir()
    (beads / "metadata.json").write_text("not-json", encoding="utf-8")
    outcome = detect.detect_beads(tmp_path)
    assert outcome.valid is False
    assert "malformed" in outcome.reason


def test_detect_returns_valid_for_embedded_dolt(tmp_path: Path) -> None:
    beads = tmp_path / ".beads"
    beads.mkdir()
    (beads / "metadata.json").write_text(
        '{"database":"dolt","backend":"dolt","dolt_mode":"embedded"}',
        encoding="utf-8",
    )
    (beads / "config.yaml").write_text(
        'sync.remote: "git+https://example.com/repo"\n', encoding="utf-8"
    )
    outcome = detect.detect_beads(tmp_path)
    assert outcome.valid is True
    assert outcome.backend == "dolt"
    assert outcome.sync_remote == "git+https://example.com/repo"


def test_nested_init_guard_blocks_when_ancestor_has_remote(tmp_path: Path) -> None:
    # Create an ancestor Beads workspace with sync.remote.
    parent = tmp_path / "parent"
    (parent / ".beads").mkdir(parents=True)
    (parent / ".beads" / "config.yaml").write_text(
        'sync.remote: "git+https://example.com/parent"\n', encoding="utf-8"
    )
    nested = parent / "child" / "grandchild"
    nested.mkdir(parents=True)

    result = detect.check_nested_init(nested)
    assert result.blocked is True
    assert result.ancestor_beads_dir == (parent / ".beads").resolve()
    assert result.ancestor_sync_remote == "git+https://example.com/parent"
    assert "refuse" in result.reason
    assert "sync.remote" in result.reason


def test_nested_init_guard_allows_when_ancestor_has_no_remote(
    tmp_path: Path, isolate_nested_init_walk
) -> None:
    parent = tmp_path / "parent"
    (parent / ".beads").mkdir(parents=True)
    (parent / ".beads" / "config.yaml").write_text(
        'actor: "test"\nsync.remote: ""\n', encoding="utf-8"
    )
    nested = parent / "child"
    nested.mkdir()
    result = detect.check_nested_init(nested)
    assert result.blocked is False
    assert result.ancestor_sync_remote is None


def test_nested_init_guard_allows_when_no_ancestor_beads(
    tmp_path: Path, isolate_nested_init_walk
) -> None:
    result = detect.check_nested_init(tmp_path)
    assert result.blocked is False
    assert result.ancestor_beads_dir is None


def test_install_refuses_without_user_authority(
    tmp_path: Path, isolate_nested_init_walk
) -> None:
    """AC #3: no silent install/init."""

    outcome = detect.install_or_initialize(tmp_path, authority=False)
    assert outcome.returncode != 0
    assert "no user authority recorded" in outcome.stderr


def test_install_refuses_with_unanswered_prompt(
    tmp_path: Path, isolate_nested_init_walk
) -> None:
    def always_no(_prompt: str) -> bool:
        return False

    outcome = detect.install_or_initialize(tmp_path, prompt_fn=always_no)
    assert outcome.returncode != 0
    assert "no user authority recorded" in outcome.stderr


def test_install_refuses_inside_nested_workspace(tmp_path: Path) -> None:
    """Nested-init guard wins over authority — refuse even when granted."""

    parent = tmp_path / "parent"
    (parent / ".beads").mkdir(parents=True)
    (parent / ".beads" / "config.yaml").write_text(
        'sync.remote: "git+https://example.com/parent"\n', encoding="utf-8"
    )
    nested = parent / "child"
    nested.mkdir()
    outcome = detect.install_or_initialize(nested, authority=True)
    assert outcome.returncode != 0
    assert "ancestor Beads workspace" in outcome.stderr
    assert "sync.remote" in outcome.stderr


def test_install_invokes_bd_when_authority_granted(
    tmp_path: Path, monkeypatch, isolate_nested_init_walk
) -> None:
    """Synthetic: a fake `bd` binary succeeds; we confirm `subprocess.run`
    was called with the canonical init flags."""

    calls: list[list[str]] = []

    class _Fake:
        returncode = 0
        stdout = "initialized\n"
        stderr = ""

    def fake_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(cmd))
        return _Fake()

    import mba_foundation.detect as detect_mod

    monkeypatch.setattr(detect_mod.subprocess, "run", fake_run)
    outcome = detect.install_or_initialize(
        tmp_path, authority=True, prefix="unit"
    )
    assert outcome.returncode == 0
    assert calls
    cmd = calls[0]
    assert cmd[0] == "bd"
    assert "--non-interactive" in cmd
    assert "--prefix" in cmd
    assert "unit" in cmd


# ---------------------------------------------------------------------------
# F2 (turn-2) — empty quoted YAML scalar normalization
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_yaml_scalar_double_quoted_empty_returns_none(tmp_path: Path) -> None:
    """`sync.remote: ""` must normalize to None, not the empty string."""

    cfg = tmp_path / ".beads" / "config.yaml"
    _write_yaml(cfg, 'sync.remote: ""\n')
    assert detect._read_yaml_scalar(cfg, "sync.remote") is None


def test_yaml_scalar_single_quoted_empty_returns_none(tmp_path: Path) -> None:
    cfg = tmp_path / ".beads" / "config.yaml"
    _write_yaml(cfg, "sync.remote: ''\n")
    assert detect._read_yaml_scalar(cfg, "sync.remote") is None


def test_yaml_scalar_bare_empty_returns_none(tmp_path: Path) -> None:
    cfg = tmp_path / ".beads" / "config.yaml"
    _write_yaml(cfg, "sync.remote:\n")
    assert detect._read_yaml_scalar(cfg, "sync.remote") is None


def test_yaml_scalar_non_empty_quoted_value_preserved(tmp_path: Path) -> None:
    cfg = tmp_path / ".beads" / "config.yaml"
    _write_yaml(cfg, 'sync.remote: "git+https://example.com/repo"\n')
    assert (
        detect._read_yaml_scalar(cfg, "sync.remote")
        == "git+https://example.com/repo"
    )


def test_yaml_scalar_unquoted_value_preserved(tmp_path: Path) -> None:
    cfg = tmp_path / ".beads" / "config.yaml"
    _write_yaml(cfg, "sync.remote: git+https://example.com/repo\n")
    assert (
        detect._read_yaml_scalar(cfg, "sync.remote")
        == "git+https://example.com/repo"
    )


def test_nested_init_guard_skips_empty_quote_ancestor(
    tmp_path: Path, isolate_nested_init_walk
) -> None:
    """An empty-quoted ``sync.remote: ""`` must not trigger the guard."""

    parent = tmp_path / "parent"
    (parent / ".beads").mkdir(parents=True)
    (parent / ".beads" / "config.yaml").write_text(
        'sync.remote: ""\n', encoding="utf-8"
    )
    nested = parent / "child"
    nested.mkdir()
    result = detect.check_nested_init(nested)
    assert result.blocked is False
    assert result.ancestor_sync_remote is None
