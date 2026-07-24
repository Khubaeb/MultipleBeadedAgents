"""Primitives-wide constants.

Derived from ``docs/mba/charter.md`` §10 (records layout, assignment contract)
and ``docs/beads/capabilities.md`` Core (direct ``bd`` CLI, validated
versions).

The ``FIELD_TO_FLAG`` map is the explicit allow-list that binds each
field name to the corresponding ``bd update`` flag. A field not in this
map is refused by ``safe_write_field`` so the helper cannot write
arbitrary ``bd update`` flags without audit.
"""

from __future__ import annotations

# Field names supported by ``safe_write_field``. Each entry maps to the
# ``bd update`` flag the helper invokes AND declares the transport the
# helper uses to keep multi-line content safe from shell quoting:
#
# * ``"file"`` — write content to a temporary file and pass the file
#   path to ``bd update`` via ``--<flag>=<path>`` (no ``@`` prefix; bd
#   1.0.4 reads the file path verbatim).
# * ``"argv"`` — pass the content as a single ``subprocess.run`` argv
#   element. ``shell=False`` means no shell ever sees the content, so
#   quoting cannot corrupt it. This is the multiline-safe path for
#   ``bd update`` flags that do not have a ``--<flag>-file`` variant.
#
# Labels are handled separately via repeated ``--set-labels``.
_FIELD_TRANSPORTS: dict[str, tuple[str, str]] = {
    "description": ("--body-file", "file"),
    "notes": ("--notes", "argv"),
    "design": ("--design-file", "file"),
    "acceptance": ("--acceptance", "argv"),
}

# Convenience: field name → ``bd update`` flag string.
FIELD_TO_FLAG: dict[str, str] = {
    field: flag for field, (flag, _transport) in _FIELD_TRANSPORTS.items()
}

# The set of field names whose value is a list of strings (rather than
# a multi-line block). Labels are the canonical example.
LIST_FIELDS: frozenset[str] = frozenset({"labels"})

# The ``bd update`` flag used for label-list fields.
LABELS_FLAG: str = "--set-labels"

# §10 records-layout directory names.
ORCHESTRATOR_DIR: str = "orchestrator"
FINAL_DIR: str = "final"

# §10 assignment-contract template title/heading fragment.
ASSIGNMENT_CONTRACT_HEADING: str = "Assignment"

# None-marker used by ``assignment_contract`` when a field is omitted.
# Per AC #4 the marker must be explicit, never a silently dropped default.
NONE_MARKER: str = "(None)"
