"""Tests for the ``stream`` CLI subcommand in ``mba_runtime.cli``.

The follower surfaces the example-capture capture surface end-to-end. The
tests pin the contract for the Orchestrator and for any
``mba stream <bead-id> <session-name>`` invocation: the path guard,
the bounded summary, the raw mode, the follow mode, and the
explicit errors when the path is out of scope.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from mba_runtime import cli, stream_capture


# Representative slice of OpenCode NDJSON output.
NDJSON_SAMPLE = (
    '{"type":"step_start","timestamp":1784846698103,"sessionID":"ses_abc","part":{"id":"prt_a","messageID":"msg_a","sessionID":"ses_abc","snapshot":"snap1","type":"step-start"}}',
    '{"type":"tool_use","timestamp":1784846705022,"sessionID":"ses_abc","part":{"type":"tool","tool":"read","callID":"call_1","state":{"status":"completed","time":{"start":1784846705000,"end":1784846705020},"input":{"filePath":"/etc/hosts","limit":2000},"output":"127.0.0.1"}}}',
    '{"type":"text","timestamp":1784846710000,"sessionID":"ses_abc","part":{"id":"prt_d","messageID":"msg_b","sessionID":"ses_abc","type":"text","text":"all good","time":{"start":1784846709999,"end":1784846710000}}}',
)


def _write_ndjson(path: Path, lines: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out):
        try:
            rc = cli.main(args)
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


def test_stream_cli_replays_bounded_summary(tmp_path: Path) -> None:
    session = tmp_path / ".mba-work" / "example-capture" / "doer-round2"
    _write_ndjson(session / "run.log", NDJSON_SAMPLE)
    rc, out, _err = _run_cli(
        [
            "stream",
            "--bead-id",
            "example-capture",
            "--session-name",
            "doer-round2",
            "--cwd",
            str(tmp_path),
            "--max-tool-payload-chars",
            "16",
        ]
    )
    assert rc == 0
    lines = out.strip().splitlines()
    assert len(lines) == len(NDJSON_SAMPLE)
    assert "step_start" in lines[0]
    assert "tool_use" in lines[1]
    assert "text" in lines[2]
    # Tool payload is truncated by default.
    assert "more chars" in lines[1]


def test_stream_cli_raw_mode_keeps_json(tmp_path: Path) -> None:
    session = tmp_path / ".mba-work" / "example-capture" / "doer-round2"
    _write_ndjson(session / "run.log", NDJSON_SAMPLE)
    rc, out, _err = _run_cli(
        [
            "stream",
            "--bead-id",
            "example-capture",
            "--session-name",
            "doer-round2",
            "--cwd",
            str(tmp_path),
            "--raw",
        ]
    )
    assert rc == 0
    assert json.loads(out.strip().splitlines()[0])["type"] == "step_start"
    assert "tool_use" in out


def test_stream_cli_rejects_friendly_name_path(tmp_path: Path) -> None:
    # Friendly-name mistake: a directory under .mba-work/ that is
    # not the real Bead ID. The CLI must refuse when the operator
    # tries to point ``--stdout-file`` at the wrong scope. The
    # default target is computed from ``--bead-id`` and is always
    # in scope, so we exercise the override path.
    wrong = tmp_path / ".mba-work" / "example-capture-pretty" / "doer-round2"
    _write_ndjson(wrong / "run.log", NDJSON_SAMPLE)
    rc, _out, _err = _run_cli(
        [
            "stream",
            "--bead-id",
            "example-capture",
            "--session-name",
            "doer-round2",
            "--cwd",
            str(tmp_path),
            "--stdout-file",
            str(wrong / "run.log"),
        ]
    )
    assert rc == 99


def test_stream_cli_rejects_empty_bead_id(tmp_path: Path) -> None:
    rc, _out, _err = _run_cli(
        [
            "stream",
            "--bead-id",
            "",
            "--session-name",
            "doer-round2",
            "--cwd",
            str(tmp_path),
        ]
    )
    assert rc == 99


def test_stream_cli_rejects_invalid_max_events(tmp_path: Path) -> None:
    session = tmp_path / ".mba-work" / "example-capture" / "doer-round2"
    _write_ndjson(session / "run.log", NDJSON_SAMPLE)
    rc, _out, _err = _run_cli(
        [
            "stream",
            "--bead-id",
            "example-capture",
            "--session-name",
            "doer-round2",
            "--cwd",
            str(tmp_path),
            "--max-field-chars",
            "-1",
        ]
    )
    assert rc == 99


def test_stream_cli_max_events_caps_output(tmp_path: Path) -> None:
    session = tmp_path / ".mba-work" / "example-capture" / "doer-round2"
    _write_ndjson(session / "run.log", NDJSON_SAMPLE)
    rc, out, _err = _run_cli(
        [
            "stream",
            "--bead-id",
            "example-capture",
            "--session-name",
            "doer-round2",
            "--cwd",
            str(tmp_path),
            "--max-events",
            "1",
        ]
    )
    assert rc == 0
    assert len(out.strip().splitlines()) == 1


def test_stream_cli_max_bytes_reads_tail_and_flags_truncation(
    tmp_path: Path,
) -> None:
    session = tmp_path / ".mba-work" / "example-capture" / "doer-round3"
    records = tuple(
        json.dumps({"type": "text", "part": {"text": f"record-{index}"}})
        for index in range(1, 11)
    )
    _write_ndjson(session / "run.log", records)
    rc, out, _err = _run_cli(
        [
            "stream",
            "--bead-id",
            "example-capture",
            "--session-name",
            "doer-round3",
            "--cwd",
            str(tmp_path),
            "--max-bytes",
            str(len(records[-1].encode("utf-8")) + 1),
        ]
    )
    assert rc == 0
    assert out.splitlines()[0] == "# truncated_tail=true"
    assert "record-10" in out
    assert "record-1'" not in out


def test_stream_cli_missing_file_reports_missing(tmp_path: Path) -> None:
    rc, out, _err = _run_cli(
        [
            "stream",
            "--bead-id",
            "example-capture",
            "--session-name",
            "doer-round2",
            "--cwd",
            str(tmp_path),
        ]
    )
    assert rc == 0
    assert "missing" in out


def test_stream_cli_streams_stderr_file(tmp_path: Path) -> None:
    session = tmp_path / ".mba-work" / "example-capture" / "doer-round2"
    _write_ndjson(session / "run.err", NDJSON_SAMPLE)
    rc, out, _err = _run_cli(
        [
            "stream",
            "--bead-id",
            "example-capture",
            "--session-name",
            "doer-round2",
            "--cwd",
            str(tmp_path),
            "--stream",
            "stderr",
        ]
    )
    assert rc == 0
    lines = out.strip().splitlines()
    assert len(lines) == len(NDJSON_SAMPLE)
    assert "step_start" in lines[0]
