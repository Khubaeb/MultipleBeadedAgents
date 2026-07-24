"""Worker-comment helpers.

Bead comments are the normal complete human-facing record. A comment is
brief, role-attributed, structured Markdown. It may link to a file when
there is bulky evidence or a generated artefact, but a separate
``.mba-work`` file is not required for ordinary human review.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import bd_client
from .constants import COMMENT_MAX_LINES, COMMENT_MAX_WORDS, COMMENT_MIN_LINES


class CommentFormatError(ValueError):
    """Raised when a comment does not satisfy the Charter section 10 shape."""


@dataclass(frozen=True)
class PostedComment:
    """Outcome of :func:`post_role_comment`."""

    bead_id: str
    actor: str
    text_path: Path
    path_tail: str
    returncode: int
    stdout: str
    stderr: str


def _slugify_session_dir(session_dir: Path) -> str:
    """Return a compact path tail, preferring ``.mba-work/...`` when present."""

    parts = session_dir.parts
    for index in range(len(parts) - 1, -1, -1):
        if parts[index] == ".mba-work":
            return "/".join(parts[index:])
    return session_dir.as_posix()


def render_role_comment_text(
    *,
    organisational_role: str,
    result_line: str,
    changes: list[str],
    verification: str,
    next_state: str,
    path_tail: str | None = None,
) -> str:
    """Render a brief role-attributed Markdown comment.

    The actor/role is supplied to ``bd comments add --actor`` and is not
    repeated inside the body. The final details line either points to a
    bulky record or states that the Bead comment is complete.
    """

    lines: list[str] = [f"- **result:** {result_line.strip()}"]
    if changes:
        lines.append(
            "- **changes:** "
            + "; ".join(item.strip() for item in changes if item.strip())
        )
    lines.append(f"- **verification:** {verification.strip()}")
    lines.append(f"- **next:** {next_state.strip()}")
    if path_tail:
        lines.append(f"- **details:** `{path_tail}`")
    else:
        lines.append("- **details:** Bead comment is complete; no separate file.")
    text = "\n".join(lines) + "\n"
    _validate_comment_text(text)
    _ = organisational_role
    return text


def render_orchestrator_comment_text(
    *,
    result_line: str,
    material_change: str,
    verification: str,
    next_state: str,
    path_tail: str | None = None,
) -> str:
    """Render a brief Orchestrator comment for material events only."""

    details = (
        f"`{path_tail}`"
        if path_tail
        else "Bead comment is complete; no separate file."
    )
    text = (
        f"- **result:** {result_line.strip()}\n"
        f"- **coordination:** {material_change.strip()}\n"
        f"- **verification:** {verification.strip()}\n"
        f"- **next:** {next_state.strip()}\n"
        f"- **details:** {details}\n"
    )
    _validate_comment_text(text)
    return text


def _validate_comment_text(text: str) -> None:
    """Validate the normal MBA comment shape."""

    word_count = len(text.split())
    if word_count > COMMENT_MAX_WORDS:
        raise CommentFormatError(
            f"comment is {word_count} words; Charter section 10 caps at "
            f"{COMMENT_MAX_WORDS} words total"
        )
    non_blank_lines = [ln for ln in text.splitlines() if ln.strip()]
    if not (COMMENT_MIN_LINES <= len(non_blank_lines) <= COMMENT_MAX_LINES):
        raise CommentFormatError(
            f"comment has {len(non_blank_lines)} non-blank lines; "
            f"Charter section 10 calls for {COMMENT_MIN_LINES} to "
            f"{COMMENT_MAX_LINES}"
        )
    if not any(ln.strip().startswith("- **details:**") for ln in non_blank_lines):
        raise CommentFormatError("comment must include a details line")


def write_comment_text(path: Path, text: str) -> Path:
    """Write ``text`` to ``path`` (UTF-8)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run_bd_comments_add(
    *, bd_binary: str, bead_id: str, text_file: Path, actor: str, cwd: Path
) -> subprocess.CompletedProcess[str]:
    """Run ``bd comments add <bead_id> -f <file> --actor=<role>``."""

    return bd_client.call(
        bd_binary,
        args=[
            "comments",
            "add",
            bead_id,
            "-f",
            str(text_file),
            "--actor",
            actor,
        ],
        cwd=cwd,
    )


def post_role_comment(
    *,
    bead_id: str,
    session_dir: Path,
    organisational_role: str,
    result_line: str,
    changes: list[str],
    verification: str,
    next_state: str,
    cwd: Path,
    bd_binary: str = "bd",
    path_tail: str | None = None,
) -> PostedComment:
    """Render, persist and post a brief role-attributed Bead comment."""

    detail_tail = path_tail if path_tail is not None else _slugify_session_dir(session_dir)
    text = render_role_comment_text(
        organisational_role=organisational_role,
        result_line=result_line,
        changes=changes,
        verification=verification,
        next_state=next_state,
        path_tail=detail_tail,
    )

    text_path = session_dir / "comment.md"
    write_comment_text(text_path, text)

    proc = _run_bd_comments_add(
        bd_binary=bd_binary,
        bead_id=bead_id,
        text_file=text_path,
        actor=organisational_role,
        cwd=cwd,
    )
    return PostedComment(
        bead_id=bead_id,
        actor=organisational_role,
        text_path=text_path,
        path_tail=detail_tail or "",
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )
