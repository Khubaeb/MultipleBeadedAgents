"""Windows sync guard for `bd dolt push` in embedded mode.

Acceptance row coverage (Foundation AC #5):

* The Windows sync guard refuses ``bd dolt push`` in embedded mode when
  the path length exceeds the validated Windows ``MAX_PATH`` threshold;
  it offers the authorised server-mode / non-git remote alternative
  instead.

This module is rule-driven (it inspects the Beads workspace, decides,
and explains) but **never** executes ``bd dolt push``. The runtime that
calls this function (Foundation, later Primitives / Runtime) is what
must observe the refusal and offer the alternative to the user.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .constants import IS_WINDOWS, WINDOWS_MAX_PATH
from .detect import detect_beads


@dataclass(frozen=True)
class SyncGuardDecision:
    """Outcome of the Windows sync guard for a given push attempt."""

    ok: bool                                         # True ⇢ safe to push.
    cwd: Path
    backend: str | None                              # `embedded` / `server` / None.
    resolved_path_length: int                        # `.beads/dolt/` length (or 0).
    max_path_threshold: int                          # The validated threshold.
    reason: str                                      # Empty on `ok=True`.
    alternative: str                                 # Suggested authorised path when `ok=False`.

    def to_refusal_message(self) -> str:
        return (
            f"refuse `bd dolt push` at {self.cwd}: {self.reason}\n"
            f"Authorised alternative: {self.alternative}"
        )


def _resolved_dolt_path_length(cwd: Path) -> int:
    """Length of the resolved absolute path to ``<cwd>/.beads/dolt``.

    On Windows, embedded Dolt's storage lives in a deeply nested directory
    tree under ``.beads/dolt/``; very long repo paths trip the Win32
    MAX_PATH limit. We measure the storage root conservatively.
    """

    beads_dolt = (cwd / ".beads" / "dolt").resolve()
    return len(str(beads_dolt))


def check_push_safety(cwd: Path, *, max_path: int | None = None) -> SyncGuardDecision:
    """Decide whether a `bd dolt push` is safe in the current environment.

    Rule (verbatim AC):

    * If ``mode == 'embedded'`` AND ``resolved_path_length > threshold``
      AND we are running on Windows → refuse; suggest server-mode or
      non-git remote.
    * Otherwise → ok.
    """

    threshold = WINDOWS_MAX_PATH if max_path is None else max_path
    outcome = detect_beads(cwd)
    resolved_len = _resolved_dolt_path_length(cwd)

    if outcome.valid and outcome.mode == "embedded" and IS_WINDOWS and resolved_len > threshold:
        return SyncGuardDecision(
            ok=False,
            cwd=cwd,
            backend=outcome.backend,
            resolved_path_length=resolved_len,
            max_path_threshold=threshold,
            reason=(
                f"path length {resolved_len} > validated Windows MAX_PATH "
                f"{threshold}; embedded Dolt storage at {cwd}/.beads/dolt/ "
                f"may exceed Win32 path limits during push"
            ),
            alternative=(
                "either re-init with `bd init --server` against an external "
                "Dolt server, or configure a non-git remote (HTTP/S3) per "
                "docs/beads/capabilities.md Conditional row. Both require "
                "explicit user authority per docs/mba/charter.md §11."
            ),
        )

    return SyncGuardDecision(
        ok=True,
        cwd=cwd,
        backend=outcome.backend,
        resolved_path_length=resolved_len,
        max_path_threshold=threshold,
        reason="",
        alternative="",
    )


def refuse_push(cwd: Path) -> SyncGuardDecision:
    """Convenience: return a ``SyncGuardDecision`` for ``cwd``.

    Equivalent to ``check_push_safety(cwd)``; the symmetric helper name
    mirrors the AC's "refuses `bd dolt push`" framing so callers can read
    the intent at the call site.
    """

    return check_push_safety(cwd)
