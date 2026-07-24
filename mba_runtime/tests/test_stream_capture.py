"""Tests for ``mba_runtime.stream_capture``.

The module is the runtime-owned capture surface for MBA-launched
workers. The tests pin the contract for the converged first-release
design (example-capture) without touching real OpenCode output: the NDJSON
events the parser handles come from a representative sample modelled
on the empirical ``run.log`` from ``.mba-work/example-stream/doer/``.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from mba_runtime import stream_capture
from mba_runtime.stream_capture import (
    BeadScopedPathError,
    FollowConfig,
    PROTOCOL_LINE_TEXT,
    PROTOCOL_NDJSON,
    PROTOCOL_OPAQUE_BYTES,
    StreamCapture,
    StreamCaptureError,
    StreamEvent,
    ParseStats,
    follow_capture,
    follow_stream,
    parse_ndjson_lines,
    read_events,
    summarize_event,
    summarize_log,
    validate_bead_scoped_path,
)


# A representative slice of the empirical OpenCode NDJSON stream
# (example-stream research). Each line is one JSON object with the
# fields the v1.18.3 CLI emits; the slice covers the load-bearing
# event types the parser must recognise.
NDJSON_SAMPLE = (
    '{"type":"step_start","timestamp":1784846698103,"sessionID":"ses_abc","part":{"id":"prt_a","messageID":"msg_a","sessionID":"ses_abc","snapshot":"snap1","type":"step-start"}}',
    '{"type":"reasoning","timestamp":1784846698124,"sessionID":"ses_abc","part":{"id":"prt_b","messageID":"msg_a","sessionID":"ses_abc","type":"reasoning","text":"hello world","time":{"start":1784846698049,"end":1784846698064}}}',
    '{"type":"tool_use","timestamp":1784846705022,"sessionID":"ses_abc","part":{"type":"tool","tool":"read","callID":"call_1","state":{"status":"completed","time":{"start":1784846705000,"end":1784846705020},"input":{"filePath":"/etc/hosts","limit":2000},"output":"<file>127.0.0.1 localhost</file>"}}}',
    '{"type":"step_finish","timestamp":1784846706071,"sessionID":"ses_abc","part":{"id":"prt_c","reason":"tool-calls","snapshot":"snap1","messageID":"msg_a","sessionID":"ses_abc","type":"step-finish","tokens":{"total":42,"input":40,"output":2,"reasoning":0,"cache":{"write":0,"read":0}},"cost":0.000123}}',
    '{"type":"text","timestamp":1784846710000,"sessionID":"ses_abc","part":{"id":"prt_d","messageID":"msg_b","sessionID":"ses_abc","type":"text","text":"summary line","time":{"start":1784846709999,"end":1784846710000}}}',
    '{"type":"error","timestamp":1784846712000,"sessionID":"ses_abc","error":{"name":"ProviderError","message":"rate limited"}}',
)


def _write_lines(path: Path, lines: list[str], *, trailing: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line)
            if not line.endswith("\n"):
                handle.write("\n")
        if trailing:
            handle.write(trailing)


# ---------------------------------------------------------------------------
# Bead-scoped path guard
# ---------------------------------------------------------------------------


def test_validate_bead_scoped_path_accepts_canonical(tmp_path: Path) -> None:
    target = tmp_path / ".mba-work" / "example-capture" / "doer-round2" / "run.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()
    resolved = validate_bead_scoped_path(tmp_path, target, bead_id="example-capture")
    assert resolved == target.resolve()


def test_validate_bead_scoped_path_rejects_friendly_name(tmp_path: Path) -> None:
    # downstream test repo path-guard-regression guard: refuse .mba-work/<friendly-name> mistakes
    # where the parent is not a real Bead ID.
    wrong = tmp_path / ".mba-work" / "example-capture-pretty" / "run.log"
    wrong.parent.mkdir(parents=True, exist_ok=True)
    wrong.touch()
    with pytest.raises(BeadScopedPathError):
        validate_bead_scoped_path(tmp_path, wrong, bead_id="example-capture")


def test_validate_bead_scoped_path_rejects_empty_bead_id(tmp_path: Path) -> None:
    target = tmp_path / ".mba-work" / "example-capture" / "run.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()
    with pytest.raises(BeadScopedPathError):
        validate_bead_scoped_path(tmp_path, target, bead_id="")


def test_validate_bead_scoped_path_rejects_outside_root(tmp_path: Path) -> None:
    # Symlink evasion: a path inside .mba-work/<bead> that resolves
    # to outside the project is rejected.
    outside = tmp_path.parent / "external" / "run.log"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.touch()
    symlink = tmp_path / ".mba-work" / "example-capture" / "run.log"
    symlink.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        # Best-effort symlink; skip on hosts that disallow it.
        try:
            os.symlink(outside, symlink)
        except (OSError, NotImplementedError):
            pytest.skip("symlink unsupported on this host")
    else:
        os.symlink(outside, symlink)
    with pytest.raises(BeadScopedPathError):
        validate_bead_scoped_path(tmp_path, symlink, bead_id="example-capture")


# ---------------------------------------------------------------------------
# NDJSON parsing
# ---------------------------------------------------------------------------


def test_parse_ndjson_lines_yields_typed_events() -> None:
    events = list(parse_ndjson_lines(NDJSON_SAMPLE))
    assert len(events) == len(NDJSON_SAMPLE)
    # Every yielded triple is (line_no, raw, event) with event
    # being a StreamEvent for well-formed JSON.
    for index, (line_no, raw, event) in enumerate(events, start=1):
        assert line_no == index
        assert raw == NDJSON_SAMPLE[index - 1]
        assert event is not None
        assert isinstance(event, StreamEvent)


def test_parse_ndjson_lines_handles_malformed_records() -> None:
    lines = list(NDJSON_SAMPLE) + ["{not json"]
    events = list(parse_ndjson_lines(lines))
    # Malformed line still yields a triple, with event=None.
    malformed = [(ln, raw, ev) for ln, raw, ev in events if ev is None]
    assert len(malformed) == 1
    assert malformed[0][1] == "{not json"


def test_parse_ndjson_lines_skips_blank_lines() -> None:
    lines = ["", NDJSON_SAMPLE[0], "", "  \n"]
    events = list(parse_ndjson_lines(lines))
    # Only one real line should yield a (line_no, raw, event) triple
    # because blank lines are skipped silently.
    parsed = [ev for ev in events if ev[2] is not None]
    assert len(parsed) == 1


# ---------------------------------------------------------------------------
# Bounded summarisation
# ---------------------------------------------------------------------------


def test_summarize_event_step_start_includes_reason() -> None:
    payload = json.loads(NDJSON_SAMPLE[3])
    summary, redacted = summarize_event(payload)
    assert "step_finish" in summary
    assert "reason=tool-calls" in summary
    assert "tokens(total=42" in summary
    assert redacted is False


def test_summarize_event_tool_use_truncates_payload() -> None:
    payload = json.loads(NDJSON_SAMPLE[2])
    summary, redacted = summarize_event(
        payload, max_tool_payload_chars=24
    )
    assert "tool=read" in summary
    assert "status=completed" in summary
    assert "duration_ms=20" in summary
    assert redacted is True
    # Truncation marker present.
    assert "[truncated]" in summary


def test_summarize_event_reasoning_is_truncated() -> None:
    payload = json.loads(NDJSON_SAMPLE[1])
    summary, redacted = summarize_event(
        payload, max_field_chars=4
    )
    assert summary.startswith("reasoning: ")
    assert redacted is True
    assert "[truncated]" in summary


def test_summarize_event_text_passthrough_under_limit() -> None:
    payload = json.loads(NDJSON_SAMPLE[4])
    summary, redacted = summarize_event(payload)
    assert "summary line" in summary
    assert redacted is False


def test_summarize_event_error_renders_name_and_message() -> None:
    payload = json.loads(NDJSON_SAMPLE[5])
    summary, redacted = summarize_event(payload)
    assert "error: ProviderError: rate limited" in summary
    assert redacted is False


def test_summarize_event_unknown_type_renders_truncated_json() -> None:
    payload = {"type": "mystery", "data": "x" * 500}
    summary, redacted = summarize_event(payload, max_field_chars=64)
    assert "unknown(mystery)" in summary
    assert redacted is True
    # Truncation marker: explicit "more chars" tail.
    assert "more chars" in summary


# ---------------------------------------------------------------------------
# read_events / summarize_log
# ---------------------------------------------------------------------------


def test_read_events_counts_known_and_unknown(tmp_path: Path) -> None:
    path = tmp_path / "run.log"
    payload = json.loads(NDJSON_SAMPLE[0])
    extra = {"type": "mystery", "timestamp": 1, "sessionID": "s"}
    _write_lines(
        path,
        [NDJSON_SAMPLE[0], NDJSON_SAMPLE[1], json.dumps(extra)],
    )
    events, stats = read_events(path)
    assert len(events) == 3
    assert stats.parsed_lines == 3
    assert stats.unknown_types == 1
    assert stats.malformed_lines == 0
    assert stats.truncated_tail is False


def test_summarize_log_renders_header_and_body(tmp_path: Path) -> None:
    path = tmp_path / "run.log"
    _write_lines(path, list(NDJSON_SAMPLE))
    rendered = summarize_log(path)
    assert rendered.startswith("# run.log — ")
    assert "protocol=ndjson" in rendered
    assert "parsed=" in rendered
    assert "step_start" in rendered
    assert "tool_use" in rendered
    assert "step_finish" in rendered
    assert "reasoning" in rendered
    assert "text" in rendered
    assert "error" in rendered


def test_summarize_log_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "nope.log"
    assert summarize_log(path) == "# nope.log: missing"


# ---------------------------------------------------------------------------
# follow_stream
# ---------------------------------------------------------------------------


def test_follow_stream_replays_complete_records(tmp_path: Path) -> None:
    path = tmp_path / "run.log"
    _write_lines(path, list(NDJSON_SAMPLE))
    out = list(follow_stream(path, config=FollowConfig()))
    # One bounded summary line per complete record.
    assert len(out) == len(NDJSON_SAMPLE)
    assert all(
        line.split(" ", 1)[0] in {
            "step_start",
            "step_finish",
            "tool_use",
            "text",
            "reasoning",
            "error",
        }
        for line in out
    )


def test_follow_stream_raw_mode_keeps_json_verbatim(tmp_path: Path) -> None:
    path = tmp_path / "run.log"
    _write_lines(path, list(NDJSON_SAMPLE))
    out = list(follow_stream(path, config=FollowConfig(raw=True)))
    assert out == list(NDJSON_SAMPLE)


def test_follow_stream_partial_final_line_flagged_not_completed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "run.log"
    # One complete line, one partial.
    _write_lines(
        path,
        [NDJSON_SAMPLE[0]],
        trailing='{"type":"tool_use","timestamp":1,"sessionID":',
    )
    out = list(follow_stream(path, config=FollowConfig()))
    # Two lines: the completed record and a "partial" marker for
    # the trailing bytes. A crash that truncates a row never
    # produces a fake completed event.
    assert len(out) == 2
    assert "step_start" in out[0]
    assert "partial" in out[1].lower()


def test_follow_stream_max_events_caps_output(tmp_path: Path) -> None:
    path = tmp_path / "run.log"
    _write_lines(path, list(NDJSON_SAMPLE))
    out = list(
        follow_stream(
            path, config=FollowConfig(max_events=2)
        )
    )
    assert len(out) == 2


def test_follow_stream_follows_growing_file(tmp_path: Path) -> None:
    path = tmp_path / "run.log"
    path.touch()

    captured: list[str] = []
    stop_event = threading.Event()

    def _writer() -> None:
        # Write a complete record, then a partial line, then a
        # second complete record, then exit.
        time.sleep(0.1)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(NDJSON_SAMPLE[0] + "\n")
            handle.flush()
            time.sleep(0.1)
            handle.write(NDJSON_SAMPLE[1] + "\n")
            handle.flush()
            time.sleep(0.1)
            handle.write(NDJSON_SAMPLE[2] + "\n")
            handle.flush()
        stop_event.set()

    writer_thread = threading.Thread(target=_writer, daemon=True)
    writer_thread.start()

    config = FollowConfig(
        follow=True,
        poll_interval_seconds=0.05,
        max_events=2,
    )
    for line in follow_stream(path, config=config):
        captured.append(line)
    writer_thread.join(timeout=2.0)
    assert len(captured) == 2
    assert "step_start" in captured[0]
    assert "reasoning" in captured[1]


def test_follow_stream_missing_file_replay(tmp_path: Path) -> None:
    path = tmp_path / "nope.log"
    out = list(follow_stream(path, config=FollowConfig()))
    assert out == ["# nope.log: missing"]


# ---------------------------------------------------------------------------
# follow_capture
# ---------------------------------------------------------------------------


def test_follow_capture_ndjson_delegates_to_follow_stream(
    tmp_path: Path,
) -> None:
    log = tmp_path / "run.log"
    _write_lines(log, list(NDJSON_SAMPLE[:2]))
    capture = StreamCapture(
        stdout_path=log,
        stderr_path=tmp_path / "run.err",
        protocol=PROTOCOL_NDJSON,
    )
    out = list(follow_capture(capture, config=FollowConfig(max_events=1)))
    assert out[0].startswith("# capture:")
    assert "protocol=ndjson" in out[0]
    assert "step_start" in out[1]


def test_follow_capture_line_text_yields_verbatim(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("hello\nworld\n", encoding="utf-8")
    capture = StreamCapture(
        stdout_path=log,
        stderr_path=tmp_path / "run.err",
        protocol=PROTOCOL_LINE_TEXT,
        label="agent.txt",
    )
    out = list(follow_capture(capture, config=FollowConfig()))
    assert out[0].startswith("# capture:")
    assert "agent.txt: hello" in out
    assert "agent.txt: world" in out


# ---------------------------------------------------------------------------
# StreamCapture contract
# ---------------------------------------------------------------------------


def test_stream_capture_defaults_are_pinned() -> None:
    capture = StreamCapture(
        stdout_path=Path("run.log"), stderr_path=Path("run.err")
    )
    assert capture.protocol == PROTOCOL_NDJSON
    assert capture.label == "run.log"
    assert capture.captured_at == ""


def test_stream_capture_explicit_protocol_overrides_default() -> None:
    capture = StreamCapture(
        stdout_path=Path("a"),
        stderr_path=Path("b"),
        protocol=PROTOCOL_OPAQUE_BYTES,
    )
    assert capture.protocol == PROTOCOL_OPAQUE_BYTES


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def test_read_events_returns_empty_stats_for_missing_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "absent.log"
    events, stats = read_events(path)
    assert events == []
    assert stats.total_lines == 0
    assert stats.parsed_lines == 0


def test_read_events_truncated_tail_flag(tmp_path: Path) -> None:
    path = tmp_path / "big.log"
    payload = json.loads(NDJSON_SAMPLE[0])
    _write_lines(path, [json.dumps(payload)])
    # Tiny max_bytes forces the reader to take the tail path.
    events, stats = read_events(path, max_bytes=16)
    assert stats.truncated_tail is True
