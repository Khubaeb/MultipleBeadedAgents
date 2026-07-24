"""Tests for worker-origin human-needed handoff."""

from __future__ import annotations

import subprocess
from pathlib import Path

from mba_runtime.human_handoff import (
    raise_human_needed,
    render_human_handoff_comment,
)


def test_render_human_handoff_comment_is_short_and_complete() -> None:
    text = render_human_handoff_comment(
        decision_needed="Choose whether to commit generated docs.",
        options=("approve commit", "keep local only"),
        recommendation="keep local until review",
    )
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) == 5
    assert "bd human list" in text
    assert "no separate file" in text


def test_raise_human_needed_posts_comment_and_marks_bead(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, tuple[str, ...], Path]] = []

    def fake_call(bd_binary: str, *, args, cwd: Path, **kwargs):
        calls.append((bd_binary, tuple(args), cwd))
        return subprocess.CompletedProcess(
            args=[bd_binary, *args],
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr("mba_runtime.human_handoff.bd_client.call", fake_call)

    comment_text = render_human_handoff_comment(
        decision_needed="Pick release target.",
        options=("GitHub public", "private only"),
        recommendation="private only",
    )
    comment_path = tmp_path / ".mba-work" / "example-025.1" / "auditor" / "comment.md"
    result = raise_human_needed(
        cwd=tmp_path,
        bead_id="example-025.1",
        actor="Workflow Auditor",
        comment_text=comment_text,
        comment_path=comment_path,
    )

    assert result.comment_returncode == 0
    assert result.update_returncode == 0
    assert comment_path.exists()
    assert calls[0][1][:3] == ("comments", "add", "example-025.1")
    update_args = calls[1][1]
    assert update_args[:2] == ("update", "example-025.1")
    assert "--status" in update_args
    assert "blocked" in update_args
    assert "--add-label" in update_args
    assert "human" in update_args
    assert "--assignee" in update_args
    assert "Human" in update_args
    assert "--actor" in update_args
    assert "Workflow Auditor" in update_args
