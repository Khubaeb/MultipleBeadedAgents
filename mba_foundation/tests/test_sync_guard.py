"""Windows sync guard (AC #5)."""

from __future__ import annotations

from pathlib import Path

from mba_foundation import sync_guard


def test_check_push_safety_safe_when_not_embedded(tmp_path: Path) -> None:
    beads = tmp_path / ".beads"
    beads.mkdir()
    (beads / "metadata.json").write_text(
        '{"database":"dolt","backend":"dolt","dolt_mode":"server"}',
        encoding="utf-8",
    )
    decision = sync_guard.check_push_safety(tmp_path)
    # server-mode path is safe regardless of platform length.
    assert decision.ok is True


def test_check_push_safety_safe_when_no_beads_dir(tmp_path: Path) -> None:
    decision = sync_guard.check_push_safety(tmp_path)
    assert decision.ok is True


def test_check_push_safety_safe_when_path_below_threshold(tmp_path: Path) -> None:
    beads = tmp_path / ".beads"
    beads.mkdir()
    (beads / "metadata.json").write_text(
        '{"database":"dolt","backend":"dolt","dolt_mode":"embedded"}',
        encoding="utf-8",
    )
    decision = sync_guard.check_push_safety(tmp_path, max_path=10_000)
    assert decision.ok is True


def test_check_push_safety_refuses_when_path_exceeds_threshold(
    tmp_path: Path, monkeypatch
) -> None:
    # Simulate Windows + embedded + over-threshold path length.
    beads = tmp_path / ".beads"
    beads.mkdir()
    (beads / "metadata.json").write_text(
        '{"database":"dolt","backend":"dolt","dolt_mode":"embedded"}',
        encoding="utf-8",
    )

    import mba_foundation.sync_guard as sg_mod

    monkeypatch.setattr(sg_mod, "IS_WINDOWS", True)
    monkeypatch.setattr(sg_mod, "WINDOWS_MAX_PATH", 50)

    decision = sync_guard.check_push_safety(tmp_path)
    assert decision.ok is False
    assert decision.backend == "dolt"
    assert decision.resolved_path_length > 50
    assert "MAX_PATH" in decision.reason or "path length" in decision.reason
    assert "--server" in decision.alternative
    assert "user authority" in decision.alternative.lower()


def test_refusal_message_includes_alternative(tmp_path: Path, monkeypatch) -> None:
    beads = tmp_path / ".beads"
    beads.mkdir()
    (beads / "metadata.json").write_text(
        '{"database":"dolt","backend":"dolt","dolt_mode":"embedded"}',
        encoding="utf-8",
    )

    import mba_foundation.sync_guard as sg_mod

    monkeypatch.setattr(sg_mod, "IS_WINDOWS", True)
    monkeypatch.setattr(sg_mod, "WINDOWS_MAX_PATH", 50)
    decision = sync_guard.refuse_push(tmp_path)
    msg = decision.to_refusal_message()
    assert "refuse" in msg.lower()
    assert "alternative" in msg.lower()
