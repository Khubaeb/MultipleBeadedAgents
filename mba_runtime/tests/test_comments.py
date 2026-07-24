"""Tests for ``mba_runtime.comments``."""

from __future__ import annotations

from pathlib import Path

import pytest

from mba_runtime import comments
from mba_runtime.comments import (
    CommentFormatError,
    render_orchestrator_comment_text,
    render_role_comment_text,
)


def test_render_role_comment_text_shape() -> None:
    text = render_role_comment_text(
        organisational_role="Engineer",
        result_line="Doer produced working.md + result.md",
        changes=["working.md: 5 lines", "result.md: 4 lines"],
        verification="read-back verifier passed",
        next_state="await auditor review",
        path_tail=".mba-work/sample-engineer/working.md",
    )
    non_blank_lines = [ln for ln in text.splitlines() if ln.strip()]
    # Normal comments are brief and useful; the current Charter cap is
    # 16 non-blank structured lines.
    assert 4 <= len(non_blank_lines) <= 16, non_blank_lines
    assert "- **details:** `.mba-work/sample-engineer/working.md`" in text
    # Role is NOT repeated in the body — the comment is attributed
    # via --actor=<role> on `bd comments add`, never via a body line.
    assert "**role:**" not in text


def test_render_role_comment_text_minimal_shape() -> None:
    # Without ``changes``, the helper drops the optional line and the
    # comment lands at exactly 4 short Markdown lines.
    text = render_role_comment_text(
        organisational_role="Engineer",
        result_line="Deliverable recorded.",
        changes=[],
        verification="read-back passes",
        next_state="await auditor",
        path_tail=None,
    )
    non_blank_lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(non_blank_lines) == 4, non_blank_lines
    assert "Bead comment is complete; no separate file." in text


def test_render_role_comment_text_rejects_too_long_text() -> None:
    long_changes = [f"change item {i} " + "x" * 40 for i in range(8)]
    long_text = " word ".join(["alpha"] * 200)
    with pytest.raises(CommentFormatError, match="caps at"):
        render_role_comment_text(
            organisational_role="Engineer",
            result_line=long_text,
            changes=long_changes,
            verification=long_text,
            next_state=long_text,
            path_tail=".mba-work/sample/working.md",
        )


def test_validate_comment_text_allows_useful_structured_summary() -> None:
    text = "\n".join(
        [
            "- **result:** completed the Bead",
            "- **changes:** file A and file B updated",
            "- **evidence:** tests pass",
            "- **risk:** none known",
            "- **verification:** auditor accepted",
            "- **next:** closed",
            "- **details:** Bead comment is complete; no separate file.",
        ]
    ) + "\n"

    comments._validate_comment_text(text)


def test_validate_comment_text_rejects_more_than_16_lines() -> None:
    text = "\n".join(
        [f"- **item{i}:** value" for i in range(1, 17)]
        + ["- **details:** Bead comment is complete; no separate file."]
    ) + "\n"

    with pytest.raises(CommentFormatError, match="4 to 16"):
        comments._validate_comment_text(text)


def test_render_orchestrator_comment_text_shape() -> None:
    text = render_orchestrator_comment_text(
        result_line="Runtime converged on sample-1 in pattern b",
        material_change="2 Doer sessions, 1 Auditor session",
        verification="`bd dep list` + `bd dep cycles` clean",
        next_state="closed",
        path_tail=".mba-work/sample-1/final/convergence.md",
    )
    # Orchestrator helper also drops the redundant role line; the
    # shape is a 5-line Markdown structure.
    non_blank_lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(non_blank_lines) == 5, non_blank_lines
    assert "- **details:** `.mba-work/sample-1/final/convergence.md`" in text


def test_post_role_comment_invokes_bd_with_actor(
    fake_bd_dir: Path, monkeypatch, tmp_path: Path
) -> None:
    # Configure the stub bd to log comments to a tmp file.
    log_path = tmp_path / "bd_comments.log"
    monkeypatch.setenv("BD_COMMENT_LOG", str(log_path))

    cwd = fake_bd_dir
    bead_dir = cwd / ".mba-work" / "sample-engineer-1"
    bead_dir.mkdir(parents=True, exist_ok=True)

    posted = comments.post_role_comment(
        bead_id="sample-engineer-1",
        session_dir=bead_dir,
        organisational_role="Engineer",
        result_line="Doer produced working.md + result.md",
        changes=["working.md: 5 lines"],
        verification="read-back verifier passed",
        next_state="await auditor review",
        cwd=cwd,
        bd_binary="bd",
    )
    assert posted.returncode == 0
    assert posted.path_tail.startswith(".mba-work/")
    assert posted.text_path.exists()
    rows = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 1
    fields = rows[0].split("\t")
    assert fields[0] == "sample-engineer-1"
    assert fields[1] == "Engineer"
    assert fields[2].endswith("comment.md")
