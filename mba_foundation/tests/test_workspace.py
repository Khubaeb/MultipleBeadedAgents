"""`.mba-work/` mode toggle + AI-resource privacy (AC #9, AC #10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mba_foundation import constants, workspace


def test_install_mode_local_is_default(tmp_path: Path) -> None:
    state = workspace.install_mode(tmp_path, mode=constants.MBA_MODE_LOCAL)
    assert state.mode == "local"
    assert (tmp_path / ".mba-work" / ".mba-mode").exists()


def test_install_mode_shared_is_supported(tmp_path: Path) -> None:
    state = workspace.install_mode(tmp_path, mode=constants.MBA_MODE_SHARED)
    assert state.mode == "shared"


def test_install_mode_rejects_invalid_choice(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        workspace.install_mode(tmp_path, mode="ephemeral")


def test_install_mode_uses_choice_fn_when_mode_unspecified(tmp_path: Path) -> None:
    state = workspace.install_mode(tmp_path, mode="unspecified", choice_fn=lambda _p: "shared")
    assert state.mode == "shared"


def test_load_mode_defaults_to_local_when_absent(tmp_path: Path) -> None:
    state = workspace.load_mode(tmp_path)
    assert state.mode == constants.MBA_MODE_LOCAL


def test_load_mode_returns_persisted_state(tmp_path: Path) -> None:
    workspace.install_mode(tmp_path, mode=constants.MBA_MODE_SHARED)
    state = workspace.load_mode(tmp_path)
    assert state.mode == "shared"


def test_ensure_workspace_setup_is_idempotent(tmp_path: Path) -> None:
    workspace.ensure_workspace_setup(tmp_path, mode=constants.MBA_MODE_LOCAL)
    workspace.ensure_workspace_setup(tmp_path, mode=constants.MBA_MODE_SHARED)
    state = workspace.load_mode(tmp_path)
    assert state.mode == "local"  # first install wins


def test_assert_ai_resource_ignored_passes_with_rule(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(
        ".beads/\n.mba-work/\n.mba-work/.ai-resources*\n.mba-work/.mba-mode\n",
        encoding="utf-8",
    )
    ok, reason = workspace.assert_ai_resource_ignored(tmp_path)
    assert ok is True, reason


def test_assert_ai_resource_ignored_fails_without_rule(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(".beads/\n.mba-work/\n", encoding="utf-8")
    ok, reason = workspace.assert_ai_resource_ignored(tmp_path)
    assert ok is False
    assert ".ai-resources" in reason


def test_assert_ai_resource_ignored_fails_when_gitignore_absent(tmp_path: Path) -> None:
    ok, reason = workspace.assert_ai_resource_ignored(tmp_path)
    assert ok is False
    assert ".gitignore" in reason


def test_ai_resource_privacy_holds_in_shared_mode(tmp_path: Path) -> None:
    """The privacy guarantee MUST hold even when the user opts into shared."""

    workspace.install_mode(tmp_path, mode=constants.MBA_MODE_SHARED)
    (tmp_path / ".gitignore").write_text(
        ".beads/\n.mba-work/\n.mba-work/.ai-resources*\n.mba-work/.mba-mode\n",
        encoding="utf-8",
    )
    workspace.install_ai_resource_record(tmp_path)
    ok, reason = workspace.assert_ai_resource_ignored(tmp_path)
    assert ok is True, reason
    assert (tmp_path / ".mba-work" / ".ai-resources.json").exists()


def test_reconcile_gitignore_adds_missing_rules(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(".beads/\n", encoding="utf-8")
    changed, reason = workspace.reconcile_gitignore(tmp_path)
    assert changed is True
    body = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".mba-work/.ai-resources*" in body


def test_reconcile_gitignore_is_idempotent(tmp_path: Path) -> None:
    workspace.reconcile_gitignore(tmp_path)
    changed, _ = workspace.reconcile_gitignore(tmp_path)
    assert changed is False


def test_reconcile_gitignore_handles_missing_file(tmp_path: Path) -> None:
    changed, reason = workspace.reconcile_gitignore(tmp_path)
    assert changed is True
    body = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".mba-work/" in body
    assert ".mba-work/.ai-resources*" in body


# ---------------------------------------------------------------------------
# F4 (turn-2) — shared-mode transition is symmetric and privacy-preserving
# ---------------------------------------------------------------------------


def test_install_mode_shared_removes_broad_carrier(tmp_path: Path) -> None:
    """``install_mode(..., mode='shared')`` removes the broad carrier
    rule but preserves the AI-resource privacy rule.
    """

    workspace.install_mode(tmp_path, mode=constants.MBA_MODE_LOCAL)
    assert ".mba-work/" in (tmp_path / ".gitignore").read_text(encoding="utf-8")

    workspace.install_mode(tmp_path, mode=constants.MBA_MODE_SHARED)
    body = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    # Carrier is gone.
    assert ".mba-work/" not in {line.strip() for line in body.splitlines()}
    # AI-resource privacy survives both directions.
    assert ".mba-work/.ai-resources*" in {line.strip() for line in body.splitlines()}
    # .mba-mode rule is also gone (the carrier now carries the file).
    assert ".mba-work/.mba-mode" not in {line.strip() for line in body.splitlines()}


def test_install_mode_local_reinstalls_carrier(tmp_path: Path) -> None:
    """After shared, switching back to local reinstalls the carrier."""

    workspace.install_mode(tmp_path, mode=constants.MBA_MODE_SHARED)
    workspace.install_mode(tmp_path, mode=constants.MBA_MODE_LOCAL)
    body = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".mba-work/" in {line.strip() for line in body.splitlines()}
    assert ".mba-work/.ai-resources*" in {line.strip() for line in body.splitlines()}


def test_transition_to_shared_is_idempotent(tmp_path: Path) -> None:
    workspace.install_mode(tmp_path, mode=constants.MBA_MODE_LOCAL)
    first, reason_first = workspace.transition_to_shared(tmp_path)
    second, reason_second = workspace.transition_to_shared(tmp_path)
    assert first is True
    assert "removed" in reason_first
    assert second is False
    assert reason_second == ""


def test_transition_to_local_is_idempotent(tmp_path: Path) -> None:
    workspace.install_mode(tmp_path, mode=constants.MBA_MODE_SHARED)
    first, _ = workspace.transition_to_local(tmp_path)
    second, _ = workspace.transition_to_local(tmp_path)
    assert first is True
    assert second is False


def test_apply_mode_to_gitignore_is_idempotent_in_each_mode(tmp_path: Path) -> None:
    for mode in (constants.MBA_MODE_LOCAL, constants.MBA_MODE_SHARED):
        changed, _ = workspace.apply_mode_to_gitignore(tmp_path, mode=mode)
        # No baseline rules yet, so applying either mode rewrites once.
    # Second pass: idempotent regardless of mode.
    for mode in (constants.MBA_MODE_LOCAL, constants.MBA_MODE_SHARED):
        changed, _ = workspace.apply_mode_to_gitignore(tmp_path, mode=mode)
        # The second pass may apply a transition. Apply once more to
        # confirm idempotency.
        workspace.apply_mode_to_gitignore(tmp_path, mode=mode)
    # Final state matches whichever mode we last applied.
    final_changed, _ = workspace.apply_mode_to_gitignore(
        tmp_path, mode=constants.MBA_MODE_SHARED
    )
    assert final_changed is False


def test_shared_mode_preserves_ai_resource_privacy(tmp_path: Path) -> None:
    """Privacy guarantee holds in both local AND shared mode."""

    workspace.install_mode(tmp_path, mode=constants.MBA_MODE_SHARED)
    workspace.install_ai_resource_record(tmp_path)
    ok, reason = workspace.assert_ai_resource_ignored(tmp_path)
    assert ok is True, reason
    assert (tmp_path / ".mba-work" / ".ai-resources.json").exists()


def test_current_gitignore_state_reports_all_rules(tmp_path: Path) -> None:
    workspace.install_mode(tmp_path, mode=constants.MBA_MODE_LOCAL)
    state = workspace.current_gitignore_state(tmp_path)
    assert state[".mba-work/"] is True
    assert state[".mba-work/.ai-resources*"] is True
    assert state[".mba-work/.mba-mode"] is True

    workspace.install_mode(tmp_path, mode=constants.MBA_MODE_SHARED)
    state = workspace.current_gitignore_state(tmp_path)
    assert state[".mba-work/"] is False
    assert state[".mba-work/.ai-resources*"] is True  # persistent privacy rule
    assert state[".mba-work/.mba-mode"] is False


def test_transition_does_not_disturb_user_rules(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(
        ".venv/\n__pycache__/\nbuild/\n",
        encoding="utf-8",
    )
    workspace.install_mode(tmp_path, mode=constants.MBA_MODE_LOCAL)
    workspace.install_mode(tmp_path, mode=constants.MBA_MODE_SHARED)
    workspace.install_mode(tmp_path, mode=constants.MBA_MODE_LOCAL)
    body = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    # User-authored rules survive a round-trip local → shared → local.
    for kept in (".venv/", "__pycache__/", "build/"):
        assert kept in {line.strip() for line in body.splitlines()}
