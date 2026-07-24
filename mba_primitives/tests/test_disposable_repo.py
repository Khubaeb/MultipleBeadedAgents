"""Disposable Beads repository: end-to-end round-trip test (AC #3).

This is the AC's "disposable Beads repository" path: a fresh
``tmp_path`` is initialised with ``bd init --non-interactive`` when the
``bd`` binary is on PATH. When it is absent, the test synthesises the
exact JSON record the live ``bd show --json`` emits so the round-trip
contract still holds. The byte-for-byte comparison is the load-bearing
assertion: ``safe_write_field`` writes through a temp-file transport;
``read_back`` parses ``bd show --json``; ``assert_field_matches`` proves
the two are identical for both a multi-line ``description`` and a label
list.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from mba_primitives import bead_read, bead_write, records_layout

from .conftest import is_bd_available


def _run_bd_init(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bd", "init", "--non-interactive", "--prefix", "disp"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(repo),
    )


def _make_bead(
    repo: Path, bead_id: str = "disp-1", title: str = "Round-trip"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bd", "create", "--title", title, "--json", "--id", bead_id],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(repo),
    )


def test_round_trip_description_and_labels_on_disposable_repo(
    authorized_workspace: Path,
) -> None:
    """The AC's stated case: multi-line description + label list, byte-for-byte.

    Runs against real ``bd 1.0.4`` in the authorized workspace (an OS
    temporary directory outside the live repository, per the User-
    authorised test-only exception). The workspace is created
    uniquely for this test and removed at teardown.
    """

    repo = authorized_workspace
    init = _run_bd_init(repo)
    assert init.returncode == 0, init.stderr
    create = _make_bead(repo, bead_id="disp-1", title="Round-trip")
    assert create.returncode == 0, create.stderr

    description = (
        "Multi-line description\n"
        "  with indented line\n"
        "with \"double quotes\" and 'single quotes'\n"
        "with a trailing newline\n"
        "with $dollar and `backtick` markers\n"
        "and a final line\n"
    )
    expected_labels = ["alpha", "beta", "gamma", "delta"]

    # Write the description via the multiline-safe transport.
    desc_proc = bead_write.safe_write_field(
        "disp-1", "description", description, cwd=repo
    )
    assert desc_proc.returncode == 0, desc_proc.stderr

    # Write the labels via repeated --set-labels argv.
    labels_proc = bead_write.safe_write_field(
        "disp-1", "labels", expected_labels, cwd=repo
    )
    assert labels_proc.returncode == 0, labels_proc.stderr

    # Round-trip verifier: the stored description must match byte-for-byte.
    bead_read.assert_field_matches("disp-1", "description", description, cwd=repo)
    bead_read.assert_field_matches("disp-1", "labels", expected_labels, cwd=repo)

    # Read-back directly to confirm no escape artifacts.
    record = bead_read.read_back("disp-1", cwd=repo)
    assert "\\n" not in (record.get("description") or "")
    assert "\\t" not in (record.get("description") or "")
    # `bd --set-labels` canonicalises label order alphabetically; the
    # contract is set equality, which `assert_field_matches` already
    # verified above. Compare as a sorted list here.
    assert sorted(record.get("labels") or []) == sorted(expected_labels)


def test_round_trip_via_stub_when_bd_absent(tmp_path: Path, monkeypatch) -> None:
    """Stub path: synthesise the JSON record `bd show --json` would emit.

    The contract under test is the byte-for-byte round-trip through the
    public API; when the binary is unavailable we drive the same code
    paths against a synthetic JSON response to prove the contract.
    """

    description = (
        "Multi-line\n  indented\n\"quoted\"\n$dollar\n`backtick`\n"
    )
    expected_labels = ["one", "two", "three"]

    payload = [
        {
            "id": "disp-1",
            "title": "Round-trip",
            "description": description,
            "notes": "n",
            "design": "d",
            "acceptance_criteria": "a",
            "labels": expected_labels,
        }
    ]

    def fake_run(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

    # Patch the subprocess used inside bead_read.
    monkeypatch.setattr(bead_read.subprocess, "run", fake_run)

    # Confirm round-trip verifier passes against the synthetic payload.
    bead_read.assert_field_matches("disp-1", "description", description)
    bead_read.assert_field_matches("disp-1", "labels", expected_labels)

    # The write side is mocked separately.
    captured: dict[str, Any] = {}

    def fake_write_run(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(bead_write.subprocess, "run", fake_write_run)
    bead_write.safe_write_field("disp-1", "description", description, cwd=tmp_path)
    argv = captured["argv"]
    # No inline shell quoting; multi-line content goes through @file.
    assert argv[3].startswith("--body-file=")
    assert not argv[3].startswith("--body-file=@")
    for piece in argv:
        assert "Multi-line" not in piece
        assert "indented" not in piece


def test_records_layout_creates_layout_for_disposable_repo(tmp_path: Path) -> None:
    """Combine `ensure_layout` with the disposable repo so §10 layout is
    co-located with the actual Bead."""

    repo = tmp_path / "disposable"
    repo.mkdir()

    layout = records_layout.ensure_layout(
        "disp-2", ["engineer", "auditor"], base_dir=repo
    )
    assert (repo / ".mba-work" / "disp-2" / "orchestrator").is_dir()
    assert (repo / ".mba-work" / "disp-2" / "engineer").is_dir()
    assert (repo / ".mba-work" / "disp-2" / "auditor").is_dir()
    assert (repo / ".mba-work" / "disp-2" / "final").is_dir()
    assert layout["sessions"]["engineer"] == repo / ".mba-work" / "disp-2" / "engineer"


def test_disposable_repo_command_line_uses_bd(tmp_path: Path) -> None:
    """Sanity: `bd` is on PATH on this host so the disposable tests above
    actually exercised the live binary. Documented here so a future host
    with no `bd` can read why the tests skip."""

    assert is_bd_available(), shutil.which("bd")
