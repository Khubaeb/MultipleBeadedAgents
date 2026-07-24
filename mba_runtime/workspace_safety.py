"""Worker workspace and disposable-Beads safety guards.

The helpers here are intentionally small and conservative. They do not
try to sandbox arbitrary workers; they prevent the specific class of
mistake that was found in the MBA smoke run: running a destructive
``bd init`` command from a path whose ancestor already owns Beads.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


class WorkspaceSafetyError(RuntimeError):
    """Raised when a worker workspace or command is unsafe."""


DESTRUCTIVE_BD_INIT_FLAGS: frozenset[str] = frozenset(
    {
        "--reinit-local",
        "--force",
    }
)


@dataclass(frozen=True)
class DisposableWorkspaceCheck:
    """Result of a safe-disposable-root check."""

    root: Path
    safe: bool
    reason: str
    ancestor_beads: Path | None = None


def has_beads_ancestor(path: Path) -> Path | None:
    """Return the nearest ``.beads`` ancestor for ``path`` if present."""

    here = path.resolve()
    for candidate in (here, *here.parents):
        beads = candidate / ".beads"
        if beads.exists():
            return beads
    return None


def check_disposable_workspace(root: Path) -> DisposableWorkspaceCheck:
    """Check whether ``root`` is safe for a disposable ``bd init`` test.

    A safe disposable root must not be inside an existing Beads workspace.
    If an ancestor ``.beads`` exists, Beads auto-discovery may target the
    ancestor database instead of the intended temporary repository.
    """

    resolved = root.resolve()
    ancestor = has_beads_ancestor(resolved)
    if ancestor is not None:
        return DisposableWorkspaceCheck(
            root=resolved,
            safe=False,
            reason=(
                "disposable workspace has an ancestor .beads; choose a "
                "different temporary root before running bd init"
            ),
            ancestor_beads=ancestor,
        )
    return DisposableWorkspaceCheck(
        root=resolved,
        safe=True,
        reason="no ancestor .beads detected",
        ancestor_beads=None,
    )


def assert_disposable_workspace_safe(root: Path) -> DisposableWorkspaceCheck:
    """Return the check result or raise :class:`WorkspaceSafetyError`."""

    result = check_disposable_workspace(root)
    if not result.safe:
        raise WorkspaceSafetyError(
            f"{result.reason}; root={result.root}; "
            f"ancestor_beads={result.ancestor_beads}"
        )
    return result


def contains_destructive_bd_init(argv: Sequence[str] | Iterable[str]) -> bool:
    """Return True when argv contains ``bd init`` plus a destructive flag."""

    tokens = [str(item) for item in argv]
    lowered = [item.lower() for item in tokens]
    if "bd" not in lowered or "init" not in lowered:
        return False
    return any(flag in lowered for flag in DESTRUCTIVE_BD_INIT_FLAGS)


def assert_no_destructive_bd_init(
    argv: Sequence[str] | Iterable[str],
    *,
    approved_disposable: bool = False,
) -> None:
    """Reject destructive ``bd init`` unless an approved disposable root exists."""

    if approved_disposable:
        return
    if contains_destructive_bd_init(argv):
        raise WorkspaceSafetyError(
            "destructive Beads init flags are refused without an approved "
            f"disposable workspace; forbidden={sorted(DESTRUCTIVE_BD_INIT_FLAGS)}"
        )


def assert_path_inside(base: Path, path: Path, *, label: str) -> Path:
    """Resolve ``path`` and require it to be inside ``base``."""

    resolved_base = base.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_base)
    except ValueError as exc:
        raise WorkspaceSafetyError(
            f"{label} must stay inside project root; "
            f"path={resolved_path}; project={resolved_base}"
        ) from exc
    return resolved_path
