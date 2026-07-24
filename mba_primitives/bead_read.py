"""Read-back verifier (``read_back`` + ``assert_field_matches``).

Acceptance row coverage (Primitives AC #2):

* ``read_back(bead_id)`` returns the Bead's current fields, parsed as a
  dict (subset of the ``bd show <id> --json`` output relevant to MBA
  Primitives: ``description``, ``notes``, ``design``,
  ``acceptance_criteria``, ``labels``).
* ``assert_field_matches(bead_id, field, content)`` raises
  :class:`FieldMismatchError` on byte-for-byte mismatch (or list-shape
  mismatch for label fields).

``read_back`` is the verifier every later stage depends on; it must
round-trip text without inserting escape artefacts or trimming trailing
newlines.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .constants import LIST_FIELDS


class FieldMismatchError(AssertionError):
    """Raised when ``assert_field_matches`` finds a mismatch."""

    def __init__(
        self,
        *,
        bead_id: str,
        field: str,
        expected: Any,
        actual: Any,
    ) -> None:
        self.bead_id = bead_id
        self.field = field
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"field mismatch for {bead_id}.{field}: "
            f"expected {expected!r}, got {actual!r}"
        )


# Field-name aliases used by ``bd show --json``. The Primitives API uses
# the short name (``description``, ``notes``, ...) and translates to the
# canonical key the Beads JSON emits (``description``,
# ``acceptance_criteria``, ...).
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "description": ("description",),
    "notes": ("notes",),
    "design": ("design",),
    "acceptance": ("acceptance_criteria",),
    "labels": ("labels",),
}


def _extract_field(record: dict[str, Any], field: str) -> Any:
    """Return ``record[field]`` honouring the alias map.

    Missing keys return ``None`` so the caller can compare against the
    intended (possibly absent) value.
    """

    for key in _FIELD_ALIASES.get(field, (field,)):
        if key in record:
            return record[key]
    return None


def read_back(
    bead_id: str,
    *,
    cwd: Path | None = None,
    bd_binary: str = "bd",
) -> dict[str, Any]:
    """Fetch a Bead's fields as a dict via ``bd show <id> --json``.

    Returns the record whose ``id`` matches ``bead_id``. Other keys on
    the returned dict are preserved so callers can inspect them.

    On validated ``bd 1.0.4`` the response is a single-element array
    whose id matches the requested id (verified live). A future ``bd``
    that returns multiple records is supported — the matching record
    is returned, others ignored.

    Raises
    ------
    ReadBackError
        When ``bd`` is missing, exits non-zero, emits non-JSON output,
        returns an empty/non-list payload, or the payload contains no
        record whose id matches ``bead_id``.
    """

    proc = subprocess.run(
        [bd_binary, "show", bead_id, "--json"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd) if cwd is not None else None,
    )
    if proc.returncode != 0:
        raise ReadBackError(
            f"`{bd_binary} show {bead_id} --json` exited "
            f"{proc.returncode}: stderr={proc.stderr.strip()!r}"
        )
    try:
        payload = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise ReadBackError(
            f"`{bd_binary} show {bead_id} --json` returned non-JSON: "
            f"{exc}; stdout head={proc.stdout[:120]!r}"
        ) from exc
    if not isinstance(payload, list) or not payload:
        raise ReadBackError(
            f"`{bd_binary} show {bead_id} --json` returned an empty/non-list "
            f"payload; got {type(payload).__name__}"
        )

    for record in payload:
        if isinstance(record, dict) and record.get("id") == bead_id:
            return dict(record)
    # F3 (turn-2): when the requested Bead id is absent from the
    # payload, raise rather than silently falling back to ``payload[0]``.
    # Today ``bd 1.0.4`` ``bd show <id> --json`` returns a single
    # record whose id matches the requested id (verified live by the
    # Quality Reviewer's independent exerciser). A future ``bd`` that
    # emits multiple records must not allow ``read_back`` to silently
    # mis-attribute a record to the wrong Bead id.
    raise ReadBackError(
        f"`{bd_binary} show {bead_id} --json` returned "
        f"{len(payload)} record(s); none has id={bead_id!r}"
    )


def assert_field_matches(
    bead_id: str,
    field: str,
    content: Any,
    *,
    cwd: Path | None = None,
    bd_binary: str = "bd",
) -> dict[str, Any]:
    """Compare ``bead_id.<field>`` against ``content`` and return the record.

    Text fields are compared **byte-exactly** (``expected == actual``).
    On validated ``bd 1.0.4`` the store preserves content byte-exactly
    across both file and argv transports and across 0/1/2 trailing
    newlines (proven live by the Quality Reviewer's F4 probe); any
    tolerance would mask a real corruption in a primitive every later
    stage depends on. List fields (``labels``) are compared set-wise
    because ``bd --set-labels`` canonicalises label order
    alphabetically.

    Raises
    ------
    FieldMismatchError
        When the stored value differs from ``content`` in any byte
        (text fields) or any element (list fields).
    """

    record = read_back(bead_id, cwd=cwd, bd_binary=bd_binary)
    actual = _extract_field(record, field)

    if field in LIST_FIELDS:
        expected_list = _normalise_expected_labels(content)
        actual_list = list(actual or [])
        if sorted(expected_list) != sorted(actual_list):
            raise FieldMismatchError(
                bead_id=bead_id,
                field=field,
                expected=expected_list,
                actual=actual_list,
            )
        return record

    # Text field — byte-exact comparison. The previous ±1 trailing-
    # newline tolerance was removed (turn-3 F4) on auditor evidence:
    # ``bd 1.0.4`` preserves content byte-exactly across both transports
    # and across 0/1/2 trailing newlines; any tolerance silently weakens
    # the byte-for-byte guarantee AC #3 depends on.
    expected_text = "" if content is None else str(content)
    actual_text = "" if actual is None else str(actual)
    if expected_text != actual_text:
        raise FieldMismatchError(
            bead_id=bead_id,
            field=field,
            expected=expected_text,
            actual=actual_text,
        )
    return record


def _normalise_expected_labels(content: Any) -> list[str]:
    if isinstance(content, str):
        raw = content.replace(",", "\n").splitlines()
        return [piece.strip() for piece in raw if piece.strip()]
    if isinstance(content, (list, tuple)):
        return [str(item).strip() for item in content if str(item).strip()]
    raise FieldMismatchError(  # type: ignore[call-arg]
        bead_id="?",
        field="labels",
        expected=content,
        actual=None,
    )


class ReadBackError(RuntimeError):
    """Raised when ``read_back`` cannot produce a usable record."""
