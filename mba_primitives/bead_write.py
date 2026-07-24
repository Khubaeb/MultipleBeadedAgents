"""Multiline-safe Bead-field writer (``safe_write_field``).

Acceptance row coverage (Primitives AC #1):

* Accepts a Bead id, a field name, and multi-line content; writes
  through a newline-safe transport (``bd update --body-file=<path>``
  for ``description``, ``bd update --design-file=<path>`` for
  ``design``; ``--notes`` / ``--acceptance`` passed as a single
  ``subprocess.run`` argv element with ``shell=False``); labels are
  passed via repeated ``--set-labels``. **Never** through fragile
  inline shell quoting.

Two transport modes are used:

* ``"file"`` — content is written to a temporary file (UTF-8, no shell)
  and the path is passed to ``bd update`` via ``--<flag>=<path>``. This
  is the official path for ``description`` and ``design`` in bd 1.0.4.
* ``"argv"`` — content is passed as a single element of the argv list
  to ``subprocess.run(..., shell=False)``. ``shell=False`` means no
  shell ever sees the content, so quoting cannot corrupt it. This is
  the multiline-safe path for ``notes`` and ``acceptance`` which do not
  have a ``--<flag>-file`` variant in bd 1.0.4.

The transport mode is selected by ``FIELD_TRANSPORT`` in
:mod:`mba_primitives.constants`; an unknown field name is refused
without invoking ``bd``.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .constants import (
    FIELD_TO_FLAG,
    LABELS_FLAG,
    LIST_FIELDS,
    _FIELD_TRANSPORTS,
)


class SafeWriteError(RuntimeError):
    """Raised when ``safe_write_field`` cannot complete the write."""


def field_transport(field: str) -> str | None:
    """Return the transport mode (``"file"`` or ``"argv"``) for a field."""

    if field in LIST_FIELDS:
        return "argv"
    entry = _FIELD_TRANSPORTS.get(field)
    return entry[1] if entry else None


def _normalise_labels(content: Any) -> list[str]:
    """Coerce ``content`` into a list of non-empty label strings."""

    if content is None:
        raise SafeWriteError("labels content is None; expected a list/str")
    if isinstance(content, str):
        # Accept comma- or newline-separated label strings.
        raw = content.replace(",", "\n").splitlines()
        labels = [piece.strip() for piece in raw if piece.strip()]
        if not labels:
            raise SafeWriteError(
                "labels content is empty after stripping; provide at least one label"
            )
        return labels
    if isinstance(content, (list, tuple)):
        labels = [str(item).strip() for item in content]
        labels = [label for label in labels if label]
        if not labels:
            raise SafeWriteError("labels list is empty after stripping")
        return labels
    raise SafeWriteError(
        f"labels content has unsupported type {type(content).__name__}; "
        "expected str, list[str], or tuple[str, ...]"
    )


def _run(argv: list[str], *, cwd: Path | None) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` via ``subprocess.run`` with ``shell=False``.

    ``shell=False`` is the load-bearing guarantee: it means no shell
    ever sees argv, so multi-line content cannot be corrupted by
    PowerShell / cmd / bash quoting.
    """

    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd) if cwd is not None else None,
    )


def _write_to_temp_file(content: str) -> Path:
    """Write ``content`` (UTF-8) to a unique temp file and return the path.

    Caller is responsible for unlinking the file. The helper uses
    ``newline=""`` so Python does not translate line endings; bd reads
    the file verbatim.
    """

    fd, raw_path = tempfile.mkstemp(prefix="mba-prim-", suffix=".txt")
    path = Path(raw_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
    return path


def safe_write_field(
    bead_id: str,
    field: str,
    content: Any,
    *,
    cwd: Path | None = None,
    bd_binary: str = "bd",
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Write ``content`` to ``bead_id.<field>`` via a newline-safe transport.

    * ``field`` must be one of ``description``, ``notes``, ``design``,
      ``acceptance``, or ``labels``. Unknown fields raise
      :class:`SafeWriteError`.
    * Text fields use the transport declared in
      :data:`FIELD_TRANSPORT` (``file`` or ``argv``). Both modes
      preserve multi-line content byte-for-byte (no shell quoting).
    * Label fields (``labels``) are passed as repeated
      ``--set-labels <label>`` arguments; ``content`` may be a list/tuple
      of strings or a single newline/comma-separated string. Empty
      label lists are refused.
    * The subprocess is invoked with ``check=False``; the caller decides
      whether to raise on non-zero exit. The default behaviour is to
      return the completed process so callers can inspect stdout/stderr
      and route the result through ``read_back`` /
      ``assert_field_matches`` (Constraint 22).
    """

    if field in LIST_FIELDS:
        labels = _normalise_labels(content)
        argv = [bd_binary, "update", bead_id]
        for label in labels:
            argv.extend([LABELS_FLAG, label])
        return _run(argv, cwd=cwd)

    if field not in FIELD_TO_FLAG:
        raise SafeWriteError(
            f"unknown field {field!r}; allowed text fields: "
            f"{sorted(FIELD_TO_FLAG)}; allowed list fields: "
            f"{sorted(LIST_FIELDS)}"
        )

    if not isinstance(content, str):
        raise SafeWriteError(
            f"text field {field!r} requires a str; got {type(content).__name__}"
        )

    flag = FIELD_TO_FLAG[field]
    transport = field_transport(field)

    if transport == "file":
        body_file = _write_to_temp_file(content)
        try:
            argv = [bd_binary, "update", bead_id, f"{flag}={body_file}"]
            return _run(argv, cwd=cwd)
        finally:
            try:
                body_file.unlink()
            except FileNotFoundError:
                pass

    if transport == "argv":
        argv = [bd_binary, "update", bead_id, flag, content]
        return _run(argv, cwd=cwd)

    # Defensive: a future field added to FIELD_TO_FLAG without a transport
    # entry must not silently bypass the multiline-safety rule.
    raise SafeWriteError(
        f"field {field!r} has no transport declared in _FIELD_TRANSPORTS"
    )
