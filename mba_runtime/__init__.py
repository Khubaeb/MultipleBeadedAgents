"""MBA Runtime.

The orchestrator-side shell that drives a single executable Bead from
``bd ready`` through ``bd close``. See ``mba_runtime/lifecycle.py`` for
the entry point (:func:`drive_bead`) and ``mba_runtime/cli.py`` for
the shell-driven audit surface.

Charter: ``docs/mba/charter.md``. Capability record: ``docs/beads/capabilities.md``.
"""

from __future__ import annotations

__all__ = [
    "constants",
    "ai_resources",
    "pattern_router",
    "convergence",
    "user_authority",
    "comments",
    "graph",
    "lifecycle",
]
