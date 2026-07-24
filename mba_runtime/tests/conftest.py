"""Shared pytest fixtures for the Runtime test suite.

Each test runs in its own ``tmp_path``. Tests that exercise the live
``bd`` binary skip cleanly when ``bd`` is not on PATH; the
disposable-repo tests run against a real ``bd init`` workspace when
the binary is available, fall back to a synthesised ``.beads/`` folder
otherwise — both paths validate the same AC rows.

The fixture patches ``mba_runtime.bd_client.call`` so the runtime
dispatches every subprocess call through a stub. The stub is a small
Python script that respects the following environment variables:

* ``BD_RECORD``            — path to a JSON file emitted for ``bd show
                              <id> --json``.
* ``BD_ISSUE_LOG``         — path to a JSON issue list used for
                              ``bd list``, ``bd create``, and ``bd
                              update`` calls. Each write modifies the
                              file; tests assert against the post-state.
* ``BD_COMMENT_LOG``       — file to append `(bead, actor, file)` rows
                              for ``bd comments add``.
* ``BD_CLOSED_BEADS``      — file to append closed bead ids for
                              ``bd close``.
* ``BD_DEP_LISTING``       — content for ``bd dep list`` output.
* ``BD_DEP_CYCLES``        — content for ``bd dep cycles`` output.
* ``BD_FAIL_COMMANDS``     — colon-separated list of command prefixes
                              that should exit non-zero (e.g.
                              ``"bd_dolt_push"`` to simulate a refusal).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from mba_runtime import bd_client

PACKAGE_PARENT = Path(__file__).resolve().parents[2]
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))


def is_bd_available() -> bool:
    return shutil.which("bd") is not None


_STUB_SOURCE = r"""#!/usr/bin/env python3
import os, sys, json, pathlib

BD_RECORD = os.environ.get("BD_RECORD", "")
BD_ISSUE_LOG = os.environ.get("BD_ISSUE_LOG", "")
BD_COMMENT_LOG = os.environ.get("BD_COMMENT_LOG", "")
BD_CLOSED_BEADS = os.environ.get("BD_CLOSED_BEADS", "")
BD_DEP_LISTING = os.environ.get("BD_DEP_LISTING", "")
BD_DEP_CYCLES = os.environ.get("BD_DEP_CYCLES", "")
BD_FAIL_COMMANDS = os.environ.get("BD_FAIL_COMMANDS", "")


def fail(argv):
    tokens = BD_FAIL_COMMANDS.split(";")
    for prefix in tokens:
        if not prefix:
            continue
        if prefix in "_".join(argv):
            print("refused " + prefix, file=sys.stderr)
            return 2
    return 0


def read_issues():
    if BD_ISSUE_LOG and pathlib.Path(BD_ISSUE_LOG).exists():
        try:
            return json.loads(
                pathlib.Path(BD_ISSUE_LOG).read_text(encoding="utf-8") or "[]"
            )
        except json.JSONDecodeError:
            return []
    return []


def write_issues(issues):
    if BD_ISSUE_LOG:
        pathlib.Path(BD_ISSUE_LOG).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(BD_ISSUE_LOG).write_text(
            json.dumps(issues), encoding="utf-8"
        )


def opt_value(argv, name, default=""):
    for index, token in enumerate(argv):
        if token == name and index + 1 < len(argv):
            return argv[index + 1]
    return default


def has_flag(argv, name):
    return name in argv


def main():
    argv = sys.argv[1:]
    rc = fail(argv)
    if rc:
        return rc
    actor = ""
    for index, token in enumerate(argv):
        if token == "--actor" and index + 1 < len(argv):
            actor = argv[index + 1]
            argv = argv[:index] + argv[index + 2 :]
            break
    if argv and argv[0] == "version":
        print("bd version 1.0.4 (ce242a879: HEAD@ce242a879678) stub")
        return 0
    if argv and argv[0] == "show" and len(argv) >= 3 and argv[2] == "--json":
        if BD_RECORD and pathlib.Path(BD_RECORD).exists():
            sys.stdout.write(pathlib.Path(BD_RECORD).read_text(encoding="utf-8"))
        else:
            sys.stdout.write("[]")
        return 0
    if argv and argv[0] == "list":
        issues = read_issues()
        title_filter = opt_value(argv, "--title")
        want_json = has_flag(argv, "--json")
        want_all = has_flag(argv, "--all")
        filtered = []
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            if title_filter and issue.get("title") != title_filter:
                continue
            if (
                not want_all
                and str(issue.get("status", "")).lower() == "closed"
            ):
                continue
            filtered.append(issue)
        if want_json:
            sys.stdout.write(json.dumps(filtered))
        else:
            for issue in filtered:
                print(issue.get("id", ""))
        return 0
    if argv and argv[0] == "create":
        issues = read_issues()
        labels_arg = opt_value(argv, "--labels")
        labels = [item for item in labels_arg.split(",") if item]
        new_id = f"stub-{len(issues) + 1}"
        issue = {
            "id": new_id,
            "title": opt_value(argv, "--title"),
            "description": opt_value(argv, "--description"),
            "status": "open",
            "assignee": opt_value(argv, "--assignee"),
            "labels": labels,
            "issue_type": opt_value(argv, "--type", "task"),
            "priority": opt_value(argv, "--priority", "2"),
            "created_by": actor,
        }
        issues.append(issue)
        write_issues(issues)
        if has_flag(argv, "--json"):
            sys.stdout.write(json.dumps(issue))
        else:
            print(issue["id"])
        return 0
    if argv and argv[0] == "update" and len(argv) >= 2:
        issue_id = argv[1]
        issues = read_issues()
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            if issue.get("id") != issue_id:
                continue
            if has_flag(argv, "--status"):
                issue["status"] = opt_value(argv, "--status")
            if has_flag(argv, "--assignee"):
                issue["assignee"] = opt_value(argv, "--assignee")
            for index, token in enumerate(argv):
                if token == "--set-labels":
                    raw = argv[index + 1]
                    issue["labels"] = [
                        item for item in raw.split(",") if item
                    ]
            issue["updated_by"] = actor
        write_issues(issues)
        return 0
    if argv and argv[0] == "comments" and len(argv) >= 2 and argv[1] == "add":
        bead = argv[2] if len(argv) > 2 else ""
        file = ""
        i = 3
        while i < len(argv):
            if argv[i] == "-f" and i + 1 < len(argv):
                file = argv[i + 1]
                i += 2
                continue
            i += 1
        if BD_COMMENT_LOG:
            with open(BD_COMMENT_LOG, "a", encoding="utf-8") as h:
                h.write(bead + "\t" + actor + "\t" + file + "\n")
        return 0
    if argv and argv[0] == "close":
        rid = argv[1] if len(argv) > 1 else ""
        if BD_CLOSED_BEADS:
            with open(BD_CLOSED_BEADS, "a", encoding="utf-8") as h:
                h.write(rid + "\n")
        return 0
    if argv and argv[0] == "dep" and len(argv) >= 2 and argv[1] == "list":
        sys.stdout.write(BD_DEP_LISTING)
        return 0
    if argv and argv[0] == "dep" and len(argv) >= 2 and argv[1] == "cycles":
        sys.stdout.write(BD_DEP_CYCLES)
        return 2 if BD_DEP_CYCLES.strip() else 0
    print("stub bd: unsupported command", argv, file=sys.stderr)
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
"""


@pytest.fixture()
def bd_stub_path(tmp_path: Path) -> Path:
    """Path to the stub ``bd`` Python script (created per-test)."""

    path = tmp_path / "_bd_bin" / "bd-stub.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_STUB_SOURCE, encoding="utf-8")
    return path


@pytest.fixture()
def fake_bd_dir(tmp_path: Path, monkeypatch, bd_stub_path: Path):
    """Install the stub ``bd`` binary plus a fake ``.beads/``,

    and explicitly install the subprocess invoker override so
    :func:`mba_runtime.bd_client.call` (the in-process code
    path) routes every ``bd`` invocation through the stub.

    Round-2 (example-017.1): the override is purely in-process.
    The fixture also writes a per-test ``bd`` wrapper under
    ``tmp_path/_bd_bin_path`` and prepends it to ``PATH`` so
    forked subprocesses (``python -m mba_runtime``) resolve
    ``bd`` to the same stub indirection. The wrapper is an
    explicit per-test artefact, not an ambient env-var; PATH
    is the standard cross-platform command-resolution
    mechanism.
    """

    workspace_dir = tmp_path
    beads = workspace_dir / ".beads"
    beads.mkdir()
    (beads / "metadata.json").write_text(
        '{"database":"dolt","backend":"dolt","dolt_mode":"embedded",'
        '"dolt_database":"test","project_id":"unit-test"}',
        encoding="utf-8",
    )
    (beads / "config.yaml").write_text(
        'actor: "test"\nsync.remote: ""\nexport.auto: false\n',
        encoding="utf-8",
    )
    (beads / "issues.jsonl").write_text("", encoding="utf-8")

    bd_client.set_subprocess_invoker_override(bd_stub_path)

    # Cross-process seam: a per-test PATH entry with a `bd`
    # wrapper. Tests that fork ``python -m mba_runtime`` (the
    # CLI smoke tests in particular) invoke ``bd_client.call``
    # in the fork; the fork's module state is independent of
    # this process, so the in-process override does not
    # transfer. PATH lookup of ``bd`` does.
    bin_dir = tmp_path / "_bd_bin_path"
    bin_dir.mkdir(exist_ok=True)
    if sys.platform == "win32":
        wrapper = bin_dir / "bd.cmd"
        wrapper.write_text(
            "@echo off\r\n"
            f'"{sys.executable}" "{bd_stub_path}" %*\r\n',
            encoding="utf-8",
        )
    else:
        wrapper = bin_dir / "bd"
        wrapper.write_text(
            f"#!/bin/sh\nexec {sys.executable!r} {str(bd_stub_path)!r} \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)

    path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + path)

    yield workspace_dir
    bd_client.set_subprocess_invoker_override(None)


@pytest.fixture()
def bd_path_wrapper(tmp_path, monkeypatch, bd_stub_path: Path):
    """Prepend a per-test ``bin/`` directory to PATH with a ``bd`` wrapper.

    Tests that fork ``python -m mba_runtime`` (the CLI smoke
    tests in particular) need the fork's :func:`bd_client.call`
    to route through the stub. The runtime no longer consults
    any environment variable to pick the invoker (round-2
    hardening), so the cross-process seam is now explicit:
    prepend a directory to ``PATH`` that contains a ``bd``
    executable the forked subprocess resolves to.

    The wrapper itself is a per-test artefact — it lives
    under ``tmp_path/bin`` and is deleted at teardown. It
    simply invokes the stub script (the same one the
    in-process seam uses) with the same argv.
    """

    bin_dir = tmp_path / "_bd_bin_path"
    bin_dir.mkdir(exist_ok=True)
    if sys.platform == "win32":
        wrapper = bin_dir / "bd.cmd"
        wrapper.write_text(
            "@echo off\r\n"
            f'"{sys.executable}" "{bd_stub_path}" %*\r\n',
            encoding="utf-8",
        )
    else:
        wrapper = bin_dir / "bd"
        wrapper.write_text(
            f"#!/bin/sh\nexec {sys.executable!r} {str(bd_stub_path)!r} \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)

    path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + path)
    return bin_dir


@pytest.fixture()
def fake_bead_record(tmp_path: Path) -> Path:
    """Path to a fixture JSON file matching the live `bd show --json` shape."""

    path = tmp_path / "_bead_record.json"
    payload = [
        {
            "id": "sample-1",
            "title": "Runtime",
            "description": (
                "Sample bead record exercising the runtime's read-back path."
            ),
            "notes": "",
            "design": "",
            "acceptance_criteria": (
                "every acceptance row passes with evidence"
            ),
            "labels": ["implementation", "mba", "workflow"],
            "status": "in_progress",
            "assignee": "Engineer",
            "issue_type": "task",
            "priority": 1,
        }
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture()
def skip_unless_bd_available():
    if not is_bd_available():
        pytest.skip("`bd` binary not on PATH")


@pytest.fixture()
def wire_record_to_stub(monkeypatch):
    """Wire ``BD_RECORD`` to a per-test fixture JSON so the stub returns it."""

    def _fn(cwd: Path, bead_id: str) -> Path:
        payload = [
            {
                "id": bead_id,
                "title": "Sample",
                "description": "End-to-end test for the Runtime.",
                "notes": "",
                "design": "",
                "acceptance_criteria": "every AC row passes with evidence",
                "labels": ["implementation", "mba", "workflow"],
                "status": "in_progress",
                "assignee": "Engineer",
                "issue_type": "task",
                "priority": 1,
            }
        ]
        record = cwd / "_bead_record.json"
        record.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setenv("BD_RECORD", str(record))
        return record

    return _fn
