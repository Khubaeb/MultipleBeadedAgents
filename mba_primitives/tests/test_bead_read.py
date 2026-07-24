"""`read_back` + `assert_field_matches` tests (AC #2, AC #3)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from mba_primitives import bead_read
from mba_primitives.bead_read import FieldMismatchError, ReadBackError


def _make_proc(payload: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[], returncode=0, stdout=json.dumps(payload), stderr=""
    )


def test_read_back_parses_first_record(monkeypatch) -> None:
    payload = [
        {
            "id": "bead-x",
            "title": "X",
            "description": "multi\nline\n",
            "notes": "n",
            "design": "d",
            "acceptance_criteria": "a",
            "labels": ["p", "q"],
        },
        {"id": "bead-y", "title": "Y"},
    ]
    monkeypatch.setattr(
        bead_read.subprocess,
        "run",
        lambda *a, **k: _make_proc(payload),
    )
    record = bead_read.read_back("bead-x")
    assert record["id"] == "bead-x"
    assert record["description"] == "multi\nline\n"
    assert record["labels"] == ["p", "q"]


def test_read_back_returns_id_matched_record(monkeypatch) -> None:
    """Cosmetic rename (turn-3 D4): the prior test name falsely implied
    a ``payload[0]`` fallback. ``read_back`` never falls back; it
    matches the requested id and raises otherwise. This test exercises
    the id-match happy path."""

    payload = [{"id": "bead-x", "description": "d"}]
    monkeypatch.setattr(
        bead_read.subprocess,
        "run",
        lambda *a, **k: _make_proc(payload),
    )
    record = bead_read.read_back("bead-x")
    assert record["description"] == "d"


def test_read_back_raises_on_empty_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        bead_read.subprocess,
        "run",
        lambda *a, **k: _make_proc([]),
    )
    with pytest.raises(ReadBackError):
        bead_read.read_back("bead-x")


def test_read_back_raises_on_non_zero_exit(monkeypatch) -> None:
    monkeypatch.setattr(
        bead_read.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="boom"
        ),
    )
    with pytest.raises(ReadBackError):
        bead_read.read_back("bead-x")


def test_read_back_raises_on_invalid_json(monkeypatch) -> None:
    monkeypatch.setattr(
        bead_read.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not json", stderr=""
        ),
    )
    with pytest.raises(ReadBackError):
        bead_read.read_back("bead-x")


def test_assert_field_matches_text_passes_on_byte_match(monkeypatch) -> None:
    payload = [
        {
            "id": "bead-x",
            "description": "line 1\nline 2\nline 3\n",
        }
    ]
    monkeypatch.setattr(
        bead_read.subprocess,
        "run",
        lambda *a, **k: _make_proc(payload),
    )
    record = bead_read.assert_field_matches(
        "bead-x",
        "description",
        "line 1\nline 2\nline 3\n",
    )
    assert record["id"] == "bead-x"


def test_assert_field_matches_text_raises_on_trailing_newline_mismatch(monkeypatch) -> None:
    """F4 (turn-3): a ±1 trailing-newline delta must RAISE.

    The previous implementation silently accepted a stored value that
    differed from the expected value by exactly one trailing newline,
    on the rationale that ``bd update --body-file`` strips one. The
    Quality Reviewer's live probe on ``bd 1.0.4`` proved the premise
    false — bd stores content byte-exactly — so the tolerance silently
    weakened the byte-for-byte guarantee AC #3 depends on. This test
    enforces the corrected contract.
    """

    payload = [{"id": "bead-x", "description": "alpha\nbeta"}]
    monkeypatch.setattr(
        bead_read.subprocess,
        "run",
        lambda *a, **k: _make_proc(payload),
    )
    # Caller wrote with trailing newline; stored value does not have
    # one. Byte-exact comparison rejects the delta.
    with pytest.raises(FieldMismatchError):
        bead_read.assert_field_matches("bead-x", "description", "alpha\nbeta\n")


def test_assert_field_matches_text_raises_on_missing_trailing_newline(monkeypatch) -> None:
    """F4 (turn-3): the inverse delta — caller has no trailing newline,
    stored value has one — must also raise. The tolerance stripped ≤1
    trailing newline from BOTH sides, so this case was equally
    silently accepted."""

    payload = [{"id": "bead-x", "description": "alpha\nbeta\n"}]
    monkeypatch.setattr(
        bead_read.subprocess,
        "run",
        lambda *a, **k: _make_proc(payload),
    )
    with pytest.raises(FieldMismatchError):
        bead_read.assert_field_matches("bead-x", "description", "alpha\nbeta")


def test_assert_field_matches_text_raises_on_mismatch(monkeypatch) -> None:
    payload = [{"id": "bead-x", "description": "alpha"}]
    monkeypatch.setattr(
        bead_read.subprocess,
        "run",
        lambda *a, **k: _make_proc(payload),
    )
    with pytest.raises(FieldMismatchError):
        bead_read.assert_field_matches("bead-x", "description", "beta")


def test_assert_field_matches_text_preserves_internal_newlines(monkeypatch) -> None:
    """F4 (turn-3): byte-exact means internal newlines are part of the
    comparison. A swap of an internal newline for a different character
    must raise."""

    payload = [{"id": "bead-x", "description": "alpha\nbeta"}]
    monkeypatch.setattr(
        bead_read.subprocess,
        "run",
        lambda *a, **k: _make_proc(payload),
    )
    # Caller passed the same body; passes.
    bead_read.assert_field_matches("bead-x", "description", "alpha\nbeta")
    # Different internal byte — raises.
    with pytest.raises(FieldMismatchError):
        bead_read.assert_field_matches("bead-x", "description", "alpha beta")


def test_assert_field_matches_labels_order_independent(monkeypatch) -> None:
    payload = [{"id": "bead-x", "labels": ["a", "b", "c"]}]
    monkeypatch.setattr(
        bead_read.subprocess,
        "run",
        lambda *a, **k: _make_proc(payload),
    )
    # Order differs — still passes.
    bead_read.assert_field_matches("bead-x", "labels", ["c", "b", "a"])


def test_assert_field_matches_labels_raises_on_set_mismatch(monkeypatch) -> None:
    payload = [{"id": "bead-x", "labels": ["a", "b"]}]
    monkeypatch.setattr(
        bead_read.subprocess,
        "run",
        lambda *a, **k: _make_proc(payload),
    )
    with pytest.raises(FieldMismatchError):
        bead_read.assert_field_matches("bead-x", "labels", ["a", "c"])


def test_assert_field_matches_text_preserves_crlf_byte_exact(monkeypatch) -> None:
    """F4 (turn-3): CRLF is preserved byte-exactly when caller and
    store agree on the exact byte sequence. (No tolerance; CRLF is not
    a tolerance case, it is a byte sequence.)
    """

    payload = [{"id": "bead-x", "description": "alpha\r\nbeta"}]
    monkeypatch.setattr(
        bead_read.subprocess,
        "run",
        lambda *a, **k: _make_proc(payload),
    )
    # Exact match passes.
    bead_read.assert_field_matches("bead-x", "description", "alpha\r\nbeta")
    # LF-only caller against CRLF-only store — byte mismatch raises.
    with pytest.raises(FieldMismatchError):
        bead_read.assert_field_matches("bead-x", "description", "alpha\nbeta")


def test_read_back_raises_when_id_absent_in_payload(monkeypatch) -> None:
    """F3 (turn-2): do not fall back to ``payload[0]`` when the requested
    Bead id is absent from the response. A future ``bd`` that returns
    multiple records must not allow ``read_back`` to silently
    mis-attribute a record to the wrong Bead id."""

    payload = [
        {"id": "bead-other", "description": "x"},
        {"id": "bead-yet-another", "description": "y"},
    ]
    monkeypatch.setattr(
        bead_read.subprocess,
        "run",
        lambda *a, **k: _make_proc(payload),
    )
    with pytest.raises(ReadBackError):
        bead_read.read_back("bead-x")


def test_read_back_error_message_includes_payload_size(monkeypatch) -> None:
    """F3 (turn-2): the refusal cites the actual payload size so a
    Quality Reviewer can tell id-mismatch from an empty payload."""

    payload = [
        {"id": "bead-other", "description": "x"},
    ]
    monkeypatch.setattr(
        bead_read.subprocess,
        "run",
        lambda *a, **k: _make_proc(payload),
    )
    with pytest.raises(ReadBackError) as exc_info:
        bead_read.read_back("bead-x")
    assert "1 record(s)" in str(exc_info.value)
    assert "bead-x" in str(exc_info.value)


def test_read_back_id_match_takes_precedence(monkeypatch) -> None:
    """F3 (turn-2): when the requested id IS in a multi-record payload,
    the matching record is returned (not the first)."""

    payload = [
        {"id": "bead-other", "description": "x"},
        {"id": "bead-x", "description": "wanted"},
        {"id": "bead-y", "description": "y"},
    ]
    monkeypatch.setattr(
        bead_read.subprocess,
        "run",
        lambda *a, **k: _make_proc(payload),
    )
    record = bead_read.read_back("bead-x")
    assert record["description"] == "wanted"
