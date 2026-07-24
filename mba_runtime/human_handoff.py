"""Worker-origin human-needed handoff.

This module gives a Doer or Auditor a narrow Beads write path for the
case where work cannot continue without a human decision. It keeps the
Orchestrator out of routine relay work while still using native Beads
surface area: a structured comment, ``human`` label, blocked status, and
``Human`` assignee.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import bd_client
from .comments import _validate_comment_text, write_comment_text


@dataclass(frozen=True)
class HumanHandoff:
    """Result of a human-needed Beads handoff."""

    bead_id: str
    actor: str
    comment_path: Path
    update_returncode: int
    comment_returncode: int


def render_human_handoff_comment(
    *,
    decision_needed: str,
    options: tuple[str, ...],
    recommendation: str,
    detail: str | None = None,
) -> str:
    """Render the 4-5 line Markdown comment shown on the Bead."""

    option_text = "; ".join(options) if options else "User decision required."
    details = detail or "Bead comment is complete; no separate file."
    text = (
        f"- **decision needed:** {decision_needed.strip()}\n"
        f"- **options:** {option_text.strip()}\n"
        f"- **recommendation:** {recommendation.strip()}\n"
        "- **status:** blocked for human response; visible in `bd human list`\n"
        f"- **details:** {details.strip()}\n"
    )
    _validate_comment_text(text)
    return text


def raise_human_needed(
    *,
    cwd: Path,
    bead_id: str,
    actor: str,
    comment_text: str,
    comment_path: Path,
    assignee: str = "Human",
    bd_binary: str = "bd",
) -> HumanHandoff:
    """Post a human-needed comment and mark the Bead for native review."""

    _validate_comment_text(comment_text)
    write_comment_text(comment_path, comment_text)
    comment_proc = bd_client.call(
        bd_binary,
        args=[
            "comments",
            "add",
            bead_id,
            "-f",
            str(comment_path),
            "--actor",
            actor,
        ],
        cwd=cwd,
    )
    update_proc = bd_client.call(
        bd_binary,
        args=[
            "update",
            bead_id,
            "--status",
            "blocked",
            "--add-label",
            "human",
            "--assignee",
            assignee,
            "--actor",
            actor,
        ],
        cwd=cwd,
    )
    return HumanHandoff(
        bead_id=bead_id,
        actor=actor,
        comment_path=comment_path,
        update_returncode=update_proc.returncode,
        comment_returncode=comment_proc.returncode,
    )
