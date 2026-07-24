"""example-017.1 hardening tests for ``mba_primitives.records_layout``.

These tests pin the path-traversal guarantee: any segment the
runtime joins onto a project-controlled root must not escape the
root via parent (``..``), absolute paths, or separators. The
pre-packaging threat model assumed a malicious bead id or session
name could write outside the intended ``.mba-work`` /
project roots.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mba_primitives import records_layout
from mba_primitives.records_layout import LayoutError, safe_path_under_root


# ---------------------------------------------------------------------------
# ensure_layout: bead_id validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bead_id",
    [
        "..",
        "../foo",
        "../foo/bar",
        "./foo",
        "C:/evil",
        "C:\\evil",
        "/etc/passwd",
    ],
)
def test_ensure_layout_refuses_traversal_bead_id(
    tmp_path: Path, bead_id: str
) -> None:
    """Traversal-style bead_ids are refused up front.

    Closing the hole ``record_bd_version`` previously had:
    ``bead_id=".."`` slipped past the separator check, created
    ``<base>/.mba-work/..`` = ``<base>``, and let the writer
    drop files into the project root.
    """

    with pytest.raises(LayoutError):
        records_layout.ensure_layout(bead_id, ["engineer"], base_dir=tmp_path)


@pytest.mark.parametrize(
    "session_name",
    [
        "..",
        "../evil",
        "./evil",
        "good/../../escape",
        "C:\\evil",
    ],
)
def test_ensure_layout_refuses_traversal_session_name(
    tmp_path: Path, session_name: str
) -> None:
    """A session name with traversal semantics is refused."""

    with pytest.raises(LayoutError):
        records_layout.ensure_layout("example-017.1", [session_name], base_dir=tmp_path)


def test_ensure_layout_refuses_absolute_bead_id(tmp_path: Path) -> None:
    """Absolute-path bead ids are refused outright."""

    absolute = tmp_path.resolve()
    with pytest.raises(LayoutError):
        records_layout.ensure_layout(str(absolute), ["engineer"], base_dir=tmp_path)


def test_ensure_layout_refuses_absolute_session_name(tmp_path: Path) -> None:
    """Absolute-path session names are refused."""

    absolute = tmp_path.resolve()
    with pytest.raises(LayoutError):
        records_layout.ensure_layout("example-017.1", [str(absolute)])


# ---------------------------------------------------------------------------
# ensure_layout: traversal style bead_id cannot create directories outside
# .mba-work
# ---------------------------------------------------------------------------


def test_traversal_bead_id_does_not_create_root_directory(
    tmp_path: Path,
) -> None:
    """Attempting ``bead_id=".."`` must not touch the project root.

    After the failed call, the project root must be untouched (no
    spurious ``.mba-work`` or treated-as-root directories).
    """

    # The "." / ".." refusal raises early; nothing is created.
    with pytest.raises(LayoutError):
        records_layout.ensure_layout("..", ["engineer"], base_dir=tmp_path)
    assert not (tmp_path / ".mba-work").exists(), (
        "refused bead_id must not create any .mba-work directory"
    )


# ---------------------------------------------------------------------------
# safe_path_under_root: public join helper
# ---------------------------------------------------------------------------


def test_safe_path_under_root_accepts_clean_segment(tmp_path: Path) -> None:
    """A clean, single segment joins onto the root."""

    target = safe_path_under_root(tmp_path, "engineer")
    assert target == tmp_path.resolve() / "engineer"


def test_safe_path_under_root_accepts_multiple_clean_segments(tmp_path: Path) -> None:
    """Several clean segments join onto the root."""

    target = safe_path_under_root(tmp_path, "engineer", "v1")
    assert target == tmp_path.resolve() / "engineer" / "v1"


@pytest.mark.parametrize(
    "part",
    [
        "..",
        ".",
        "../escape",
        "C:/evil",
        "good/../bad",
        "/abs",
    ],
)
def test_safe_path_under_root_rejects_traversal_segment(
    tmp_path: Path, part: str
) -> None:
    """Each traversal-style segment is refused."""

    with pytest.raises(LayoutError):
        safe_path_under_root(tmp_path, part)


def test_safe_path_under_root_rejects_empty_segment(tmp_path: Path) -> None:
    """Empty / whitespace segments are refused (no trailing-slash form)."""

    with pytest.raises(LayoutError):
        safe_path_under_root(tmp_path, "")


def test_safe_path_under_root_rejects_non_string(tmp_path: Path) -> None:
    """Non-string segments are refused (the runtime only ever joins strings)."""

    with pytest.raises(LayoutError):
        safe_path_under_root(tmp_path, None)  # type: ignore[arg-type]


def test_safe_path_under_root_via_in_part_symlink_does_not_escape(
    tmp_path: Path,
) -> None:
    """An in-part symlink pointing outside the root is refused.

    The adversary drops a directory symlink (POSIX) or junction
    (Windows) inside the legitimate root that points outside;
    ``safe_path_under_root`` must follow the link and refuse the
    resolved target. The link is placed inside the root and is
    passed as a *part*, not as the root — the root stays a clean
    absolute path. This is the exact attack the round-2 prompt
    names.
    """

    import subprocess
    import sys

    outside = tmp_path / "_outside"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("payload", encoding="utf-8")

    root = tmp_path / "root"
    root.mkdir(exist_ok=True)

    alias = root / "alias"

    if sys.platform == "win32":
        # Junction (no admin required on Windows):
        # ``mklink /J <link> <target>``.
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(alias), str(outside)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            # Some Windows hosts disable junctions in restricted
            # sandboxes; fall back to a real directory symlink
            # (requires developer mode). Skip if neither works.
            if not _can_create_symlinks():
                pytest.skip(
                    f"Windows host cannot create directory "
                    f"junctions or symlinks without developer "
                    f"privilege (mklink rc={result.returncode}; "
                    f"stderr={result.stderr.strip()!r})"
                )
            alias.symlink_to(outside, target_is_directory=True)
        try:
            with pytest.raises(LayoutError):
                safe_path_under_root(root, "alias", "secret.txt")
        finally:
            # ``mklink /J``-created junctions are themselves
            # directories; an unlink is enough to remove the
            # redirection. Falls back to rmdir if a real
            # symlink was created.
            if alias.is_symlink() or alias.is_junction():
                try:
                    alias.unlink()
                except OSError:
                    import os as _os

                    _os.rmdir(alias)
    else:
        # POSIX: a directory symlink.
        alias.symlink_to(outside, target_is_directory=True)
        with pytest.raises(LayoutError):
            safe_path_under_root(root, "alias", "secret.txt")
        alias.unlink()


def _can_create_symlinks() -> bool:
    """Best-effort probe: can this process create a directory symlink?"""

    import tempfile

    with tempfile.TemporaryDirectory() as base:
        src = Path(base) / "linkparent"
        src.mkdir()
        target = Path(base) / "linktarget"
        target.mkdir()
        link = src / "link"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            return False
        return True


# ---------------------------------------------------------------------------
# Regressions on the canonical happy path
# ---------------------------------------------------------------------------


def test_ensure_layout_still_creates_three_directories(tmp_path: Path) -> None:
    """Happy-path regression: a clean bead_id still creates the §10 layout."""

    layout = records_layout.ensure_layout(
        "example-017.1", ["engineer", "auditor"], base_dir=tmp_path
    )
    assert (tmp_path / ".mba-work" / "example-017.1" / "orchestrator").is_dir()
    assert (tmp_path / ".mba-work" / "example-017.1" / "final").is_dir()
    assert (tmp_path / ".mba-work" / "example-017.1" / "engineer").is_dir()
    assert (tmp_path / ".mba-work" / "example-017.1" / "auditor").is_dir()


def test_ensure_layout_sanitises_but_accepts_legal_dots(tmp_path: Path) -> None:
    """``example-004.1`` (the version-style dotted id) still works.

    The validator refuses the bare string ``".."`` and ``"."`` but
    does not refuse all dots — ``example-004.1`` has dots inside the
    identifier and is acceptable.
    """

    layout = records_layout.ensure_layout(
        "example-004.1", ["engineer"], base_dir=tmp_path
    )
    assert (tmp_path / ".mba-work" / "example-004.1" / "engineer").is_dir()
    assert layout["bead_dir"].name == "example-004.1"
