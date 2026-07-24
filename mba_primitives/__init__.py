"""MBA Primitives.

The smallest complete primitives that the Runtime depends on
(example-005.1). Implements:

* Multiline-safe Bead-field writer (``safe_write_field``) that uses a
  newline-safe file/stdin transport — never fragile inline shell quoting.
* Read-back verifier (``read_back`` + ``assert_field_matches``) that
  fetches a Bead and compares a field byte-for-byte against the intended
  content.
* Assignment-contract template generator
  (``assignment_contract``) that fills the ``docs/mba/charter.md`` §10
  template (Orchestrator's worker-prompt contract).
* Records-layout helpers (``ensure_layout``) that create the §10
  directory layout ``.mba-work/<bead>/{orchestrator,<session>,final}/``,
  preserving existing directories.

Every Bead the implementation creates or updates is read back before any
subsequent write proceeds (Charter Constraint 22 / Understanding §2
outcome #9 / selection §example-004.1).

Charter: ``docs/mba/charter.md``. Capability record: ``docs/beads/capabilities.md``.
"""

__all__ = [
    "bead_write",
    "bead_read",
    "assignment_contract",
    "records_layout",
]
