"""Records-layout helpers (``ensure_layout``).

Acceptance row coverage (Primitives AC #5):

* Creates ``.mba-work/<bead>/orchestrator/``,
  ``.mba-work/<bead>/<session>/``, and ``.mba-work/<bead>/final/`` as
  required; existing directories are preserved.

The helper is idempotent: every directory is created with
``mkdir(parents=True, exist_ok=True)``. Existing files inside the
directories are never touched.

Path-traversal hardening (example-017.1):

* :func:`_validate` now rejects every ``.`` and ``..`` segment in
  ``bead_id`` and session names, not just ``/`` / ``\\``. A
  caller that supplies ``bead_id=".."`` to escape
  ``.mba-work/`` is refused up front.
* :func:`safe_path_under_root` is the public join helper. It
  rejects separators, ``.`` / ``..`` segments, and absolute
  paths, then verifies the resolved result stays under
  ``root``. The runtime uses it wherever a Bead-controlled
  filename is joined to a project-supplied directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from .constants import FINAL_DIR, ORCHESTRATOR_DIR


class LayoutError(ValueError):
    """Raised when ``ensure_layout`` receives unusable input."""


# Path segments the runtime refuses outright because they would
# either be a no-op (``.``) or break out of the destination
# directory (``..``).
_FORBIDDEN_SEGMENTS: frozenset[str] = frozenset({".", ".."})

# Separator characters the validator refuses. Windows accepts both
# ``/`` and ``\\`` as path separators.
_PATH_SEPARATORS: tuple[str, ...] = ("/", "\\")


def _validate_segment(name: str, *, kind: str) -> str:
    """Return ``name`` if it is a safe single path segment.

    Raises :class:`LayoutError` when the segment is empty,
    contains a path separator, is an absolute path, or equals
    ``.`` / ``..``. The check is intentionally stricter than
    :mod:`os.path` because the runtime only ever joins these
    strings onto a controlled base directory; the absolute-path
    guard refuses any drive-letter form (``C:``, ``\\\\host``)
    or POSIX root prefix.
    """

    if not isinstance(name, str) or not name.strip():
        raise LayoutError(f"{kind} must be a non-empty string; got {name!r}")
    stripped = name.strip()
    if any(sep in stripped for sep in _PATH_SEPARATORS):
        raise LayoutError(
            f"{kind} must not contain path separators; got {name!r}"
        )
    if stripped in _FORBIDDEN_SEGMENTS:
        raise LayoutError(
            f"{kind} must not be {stripped!r}; traversal segments are "
            f"refused (example-017.1 path-traversal hardening)"
        )
    if Path(stripped).is_absolute():
        raise LayoutError(
            f"{kind} must not be an absolute path; got {name!r}"
        )
    return stripped


def _validate(bead_id: str, sessions: Iterable[str]) -> tuple[str, list[str]]:
    bead_id = _validate_segment(bead_id, kind="bead_id")
    seen: list[str] = []
    for name in sessions:
        seen.append(_validate_segment(name, kind="session name"))
    return bead_id, seen


def safe_path_under_root(root: Path, *parts: str) -> Path:
    """Join ``parts`` onto ``root`` and reject escapes.

    Each part is checked via :func:`_validate_segment` semantics
    (no separators, no ``.`` / ``..``, no absolute paths). The
    candidate is **resolved** (following symlinks / junctions /
    Windows mount points) before the containment check, so a
    malicious in-part alias that points outside the root is
    caught even though its lexical path looks fine.

    Containment is verified twice:

    1. Lexically (``candidate_unresolved.relative_to(root)``) —
       catches plaintext ``..`` and absolute escapes that the
       segment validator already rejected; this is defence in
       depth.
    2. After :func:`Path.resolve` — follows symlinks and
       junctions placed **inside** the root. If the resolved
       candidate lives outside the resolved root the helper
       refuses.

    The helper is intentionally strict; runtime code uses it
    wherever a Bead-controlled directory is concatenated with a
    project-supplied name. Tests use it for any path a Bead
    write relies on.
    """

    resolved_root = Path(root).resolve()
    validated: list[str] = [
        _validate_segment(part, kind="path segment") for part in parts
    ]
    candidate_unresolved = resolved_root.joinpath(*validated)
    try:
        candidate_unresolved.relative_to(resolved_root)
    except ValueError as exc:
        raise LayoutError(
            f"path {candidate_unresolved!s} escapes root "
            f"{resolved_root!s} lexically; refusing a "
            f"traversal-style write"
        ) from exc
    # Resolve the candidate so symlinks / junctions inside the
    # root pointing outside are caught. ``strict=False`` lets
    # the helper validate paths that do not exist yet (e.g. a
    # scratch file the caller is about to create); the
    # containment check still applies to the resolved absolute
    # path, which is enough to catch the alias-in-part vector.
    candidate_resolved = candidate_unresolved.resolve(strict=False)
    try:
        candidate_resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise LayoutError(
            f"path {candidate_unresolved!s} resolves to "
            f"{candidate_resolved!s}, which escapes root "
            f"{resolved_root!s}; refusing an alias-style write"
        ) from exc
    return candidate_unresolved


def ensure_layout(
    bead_id: str,
    sessions: Sequence[str] | Iterable[str],
    *,
    base_dir: Path | None = None,
) -> dict[str, Path]:
    """Create the §10 directory layout for ``bead_id`` and return its paths.

    Returns a mapping ``{"bead_dir": ..., "orchestrator": ...,
    "final": ..., "sessions": {name: path, ...}}``. The mapping is
    informational; the directory creation is the contract.

    Existing directories are preserved (``mkdir(exist_ok=True)``). The
    helper never deletes or overwrites a pre-existing directory or file.
    """

    bead_id, names = _validate(bead_id, sessions)
    root = (base_dir or Path.cwd()).resolve()
    mba_work = safe_path_under_root(root, ".mba-work")
    bead_dir = safe_path_under_root(mba_work, bead_id)

    orchestrator = safe_path_under_root(bead_dir, ORCHESTRATOR_DIR)
    final = safe_path_under_root(bead_dir, FINAL_DIR)

    created: list[Path] = []
    for path in (mba_work, bead_dir, orchestrator, final):
        path.mkdir(parents=True, exist_ok=True)
        created.append(path)

    session_paths: dict[str, Path] = {}
    for name in names:
        path = safe_path_under_root(bead_dir, name)
        path.mkdir(parents=True, exist_ok=True)
        created.append(path)
        session_paths[name] = path

    return {
        "bead_dir": bead_dir,
        "orchestrator": orchestrator,
        "final": final,
        "sessions": session_paths,
        "created": created,
    }
