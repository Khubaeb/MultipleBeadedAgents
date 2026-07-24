"""`safe_write_field` unit + argv-mock tests (AC #1)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from mba_primitives import bead_write
from mba_primitives.bead_write import SafeWriteError


class _FakeProc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_safe_write_field_refuses_unknown_field() -> None:
    with pytest.raises(SafeWriteError):
        bead_write.safe_write_field("bead-x", "bogus", "value", cwd=Path("."))


def test_safe_write_field_text_writes_temp_file(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_run(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        captured["cwd"] = kwargs.get("cwd")
        return _FakeProc(returncode=0, stdout="ok")

    monkeypatch.setattr(bead_write.subprocess, "run", fake_run)

    proc = bead_write.safe_write_field(
        "bead-x",
        "description",
        "line 1\nline 2\nline 3\n",
        cwd=tmp_path,
    )
    assert proc.returncode == 0

    argv = captured["argv"]
    assert argv[0] == "bd"
    assert argv[1:3] == ["update", "bead-x"]
    body_arg = argv[3]
    assert body_arg.startswith("--body-file=")
    body_path = Path(body_arg.split("=", 1)[1])
    assert body_path.exists() or not body_path.exists()  # unlinked by helper
    # The helper must NOT inline the multi-line content into argv.
    for piece in argv:
        assert "line 1" not in piece
        assert "line 2" not in piece
    # The temp file is cleaned up after the helper returns.
    assert not body_path.exists()


def test_safe_write_field_design_uses_design_file(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_run(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        return _FakeProc()

    monkeypatch.setattr(bead_write.subprocess, "run", fake_run)

    bead_write.safe_write_field(
        "bead-x",
        "design",
        "design body\nwith newline\n",
        cwd=tmp_path,
    )
    argv = captured["argv"]
    assert argv[3].startswith("--design-file=")
    assert "@" not in argv[3]


def test_safe_write_field_notes_uses_argv_transport(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_run(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        captured["cwd"] = kwargs.get("cwd")
        captured["shell"] = kwargs.get("shell", False)
        return _FakeProc()

    monkeypatch.setattr(bead_write.subprocess, "run", fake_run)

    bead_write.safe_write_field(
        "bead-x", "notes", "alpha\nbeta\ngamma", cwd=tmp_path
    )
    argv = captured["argv"]
    assert argv[0:3] == ["bd", "update", "bead-x"]
    assert argv[3] == "--notes"
    # Multi-line content is passed as a single argv element; no shell.
    assert argv[4] == "alpha\nbeta\ngamma"
    assert captured["shell"] is False
    assert captured["cwd"] == str(tmp_path)


def test_safe_write_field_acceptance_uses_argv_transport(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_run(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        return _FakeProc()

    monkeypatch.setattr(bead_write.subprocess, "run", fake_run)

    bead_write.safe_write_field(
        "bead-x",
        "acceptance",
        "- AC-1\n- AC-2\n",
        cwd=tmp_path,
    )
    argv = captured["argv"]
    assert argv[3] == "--acceptance"
    assert argv[4] == "- AC-1\n- AC-2\n"


def test_safe_write_field_labels_repeat_flag(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_run(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        return _FakeProc()

    monkeypatch.setattr(bead_write.subprocess, "run", fake_run)

    bead_write.safe_write_field(
        "bead-x",
        "labels",
        ["alpha", "beta", "gamma"],
        cwd=tmp_path,
    )
    argv = captured["argv"]
    assert argv[0:3] == ["bd", "update", "bead-x"]
    assert "--set-labels" in argv
    # Each label appears as a separate argv element.
    assert "alpha" in argv
    assert "beta" in argv
    assert "gamma" in argv
    # No @file transport for list fields.
    assert not any(arg.startswith("--body-file=@") for arg in argv)


def test_safe_write_field_labels_accept_string(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_run(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        return _FakeProc()

    monkeypatch.setattr(bead_write.subprocess, "run", fake_run)

    bead_write.safe_write_field(
        "bead-x",
        "labels",
        "alpha, beta\ngamma",
        cwd=tmp_path,
    )
    argv = captured["argv"]
    assert argv.count("--set-labels") == 3
    assert argv.count("alpha") == 1
    assert argv.count("beta") == 1
    assert argv.count("gamma") == 1


def test_safe_write_field_labels_refuses_empty(monkeypatch, tmp_path: Path) -> None:
    called = {"n": 0}

    def fake_run(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
        called["n"] += 1
        return _FakeProc()

    monkeypatch.setattr(bead_write.subprocess, "run", fake_run)
    with pytest.raises(SafeWriteError):
        bead_write.safe_write_field(
            "bead-x", "labels", "", cwd=tmp_path
        )
    assert called["n"] == 0


def test_safe_write_field_text_refuses_non_str(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        bead_write.subprocess, "run", lambda *a, **k: _FakeProc()
    )
    with pytest.raises(SafeWriteError):
        bead_write.safe_write_field("bead-x", "description", 12345, cwd=tmp_path)


def test_safe_write_field_uses_subprocess_run_not_shell(monkeypatch, tmp_path: Path) -> None:
    """Confirm `shell=True` is never passed (no fragile inline quoting)."""

    captured: dict = {}

    def fake_run(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["shell"] = kwargs.get("shell", False)
        return _FakeProc()

    monkeypatch.setattr(bead_write.subprocess, "run", fake_run)

    bead_write.safe_write_field(
        "bead-x",
        "description",
        "line with 'single' and \"double\" quotes\nand a $dollar\n",
        cwd=tmp_path,
    )
    assert captured["shell"] is False
