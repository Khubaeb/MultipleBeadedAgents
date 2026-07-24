"""MBA Foundation.

The smallest complete Foundation for the Multiple Beaded Agents (MBA)
workflow. Every public module is wired to exactly one Bead acceptance
criterion (see ``docs/beads/capabilities.md`` and ``.mba-work/example-002.1/
engineering-manager/selection.md`` for the converged selection).

Charter: ``docs/mba/charter.md``. Capability record: ``docs/beads/capabilities.md``.
"""

__all__ = [
    "constants",
    "preflight",
    "detect",
    "sync_guard",
    "workspace",
    "orchestrator",
    "markers",
    "product_boundary",
    "manifest",
]
