"""MBA-owned worker stdout/stderr capture + read-only follower.

This module delivers the converged first-release stream/log design from
``example-stream``: a runtime-owned capture surface for MBA-launched workers
that is **durable, live-readable where practical, restart-safe, and
harness-neutral**. It does **not** attempt to claim cross-harness
transcript capture, server ownership, or token-level streaming.

Three layers cooperate:

1. ``StreamCapture`` describes what the dispatch adapter wrote (the
   paths, the capture protocol, and the captured-at timestamp).
2. ``parse_ndjson_lines`` and ``summarize_event`` validate, parse, and
   summarize the line-delimited JSON a hidden OpenCode worker emits.
   Other harnesses declare ``line_text`` or ``opaque_bytes`` and bypass
   parsing.
3. ``summarize_log`` / ``follow_stream`` expose a **read-only,
   process-decoupled** surface for the Orchestrator or a human to
   observe worker progress without sitting between the worker and
   its durable files. The follower buffers a partial final line so
   a crash cannot leave a fake truncated row.

Non-goals (deliberately deferred; see the research report in
``.mba-work/example-stream/doer/research.md``):

* Token-by-token text / reasoning. OpenCode ``--format json`` emits at
  step / tool / completed-part granularity.
* OpenCode ``serve`` / SSE / SDK ownership (auth, port, reconnect,
  filter, lifecycle).
* TUI / ConPTY capture.
* Cross-stream ordering between independently redirected
  stdout/stderr files.
* Power-loss-grade fsync guarantees.

The module is stdlib-only and has **no side effects** beyond reading
files. It never spawns, signals, or kills processes — cleanup stays
with :mod:`mba_runtime.external_dispatch`.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional


# ---------------------------------------------------------------------------
# Stream capture contract
# ---------------------------------------------------------------------------


# Capture protocol literals. The string values are stable; tests and
# downstream readers depend on them.
PROTOCOL_NDJSON: str = "ndjson"
PROTOCOL_LINE_TEXT: str = "line_text"
PROTOCOL_OPAQUE_BYTES: str = "opaque_bytes"

PROTOCOLS: frozenset[str] = frozenset(
    {PROTOCOL_NDJSON, PROTOCOL_LINE_TEXT, PROTOCOL_OPAQUE_BYTES}
)


@dataclass(frozen=True)
class StreamCapture:
    """The runtime's record of one MBA-owned worker capture.

    Attributes
    ----------
    stdout_path
        The path the dispatch adapter redirected the worker's stdout
        to. Always set for the canonical launch.
    stderr_path
        The path the dispatch adapter redirected the worker's stderr
        to. Always set for the canonical launch.
    protocol
        One of :data:`PROTOCOL_NDJSON`, :data:`PROTOCOL_LINE_TEXT`, or
        :data:`PROTOCOL_OPAQUE_BYTES`. ``ndjson`` is what
        ``opencode run --format json`` emits (see ``example-stream``).
    captured_at
        ISO-8601 UTC timestamp the capture started. Set by the
        adapter; the follower does not depend on it.
    label
        Short label such as ``"run.log"`` / ``"run.err"``. The
        adapter sets it; the follower uses it in headers.
    """

    stdout_path: Path
    stderr_path: Path
    protocol: str = PROTOCOL_NDJSON
    captured_at: str = ""
    label: str = "run.log"


# ---------------------------------------------------------------------------
# Stream events (NDJSON parser)
# ---------------------------------------------------------------------------


# Known OpenCode NDJSON ``type`` values. The parser treats every other
# type as an opaque JSON object and surfaces the verbatim JSON.
KNOWN_EVENT_TYPES: frozenset[str] = frozenset(
    {"step_start", "step_finish", "tool_use", "text", "reasoning", "error"}
)


@dataclass(frozen=True)
class StreamEvent:
    """One parsed event from a worker's ``run.log``.

    ``payload`` is the verbatim decoded JSON object so callers that
    need tool inputs / outputs / token counts can read them through
    the same handle. ``summary`` is the bounded, redacted single-line
    summary ``summarize_event`` produces; it is precomputed so the
    follower can render cheaply.
    """

    line_no: int
    raw: str
    payload: dict[str, object]
    event_type: str
    timestamp_ms: int
    session_id: str
    summary: str
    truncated: bool = False


@dataclass(frozen=True)
class ParseStats:
    """Counters the parser accumulates while reading a file."""

    total_lines: int = 0
    parsed_lines: int = 0
    malformed_lines: int = 0
    unknown_types: int = 0
    truncated_tail: bool = False
    final_partial_line: str = ""


class StreamCaptureError(ValueError):
    """Raised on an unusable capture input (never a silent fallthrough)."""


# ---------------------------------------------------------------------------
# Bead-scoped path guard (downstream test repo ``path-guard-regression``-style)
# ---------------------------------------------------------------------------


class BeadScopedPathError(ValueError):
    """The supplied path is not Bead-scoped to ``.mba-work/<bead-id>/``."""


def validate_bead_scoped_path(
    cwd: Path, target: Path, *, bead_id: str
) -> Path:
    """Refuse paths that are not inside ``<cwd>/.mba-work/<bead_id>/``.

    The companion to the downstream test repo ``path-guard-regression`` retry fix: the follower must
    not silently read from a friendly install name or a ref hash. The
    path **must** resolve under ``.mba-work/<bead_id>/``; symlink
    targets outside that root are rejected.
    """

    if not bead_id or not bead_id.strip():
        raise BeadScopedPathError(
            "bead_id must be a non-empty string"
        )
    cwd_resolved = cwd.resolve()
    target_resolved = target.resolve()
    expected_root = (cwd_resolved / ".mba-work" / bead_id).resolve()
    try:
        target_resolved.relative_to(expected_root)
    except ValueError as exc:
        raise BeadScopedPathError(
            f"path {target_resolved} is not under "
            f"{expected_root}; the follower only reads "
            f"Bead-scoped paths under .mba-work/{bead_id}/"
        ) from exc
    return target_resolved


# ---------------------------------------------------------------------------
# Summarisation
# ---------------------------------------------------------------------------


# Default maximum payload sizes for the bounded summary. Tool outputs
# in OpenCode can run to multiple KB; reasoning text is normally short
# but provider variants differ. Callers may override.
DEFAULT_MAX_FIELD_CHARS: int = 240
DEFAULT_MAX_TOOL_PAYLOAD_CHARS: int = 480


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    """Return ``(text, truncated_flag)`` clipped to ``limit`` chars."""

    if limit <= 0 or len(text) <= limit:
        return text, False
    return text[:limit] + f"…[{len(text) - limit} more chars]", True


def _string_field(value: object) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _tool_summary(
    payload: dict[str, object], *, max_payload_chars: int
) -> str:
    part = payload.get("part")
    if not isinstance(part, dict):
        return "tool_use: <malformed part>"
    tool = _string_field(part.get("tool")) or "<unnamed>"
    call_id = _string_field(part.get("callID"))
    state = part.get("state")
    status = "<unknown>"
    duration_ms: Optional[int] = None
    input_blob = ""
    output_blob = ""
    error_blob = ""
    if isinstance(state, dict):
        status = _string_field(state.get("status")) or status
        time_obj = state.get("time")
        if isinstance(time_obj, dict):
            start = time_obj.get("start")
            end = time_obj.get("end")
            if isinstance(start, (int, float)) and isinstance(end, (int, float)):
                duration_ms = int(end - start)
        inp = state.get("input")
        if inp is not None:
            try:
                input_blob = json.dumps(inp, ensure_ascii=False)
            except (TypeError, ValueError):
                input_blob = repr(inp)
        out = state.get("output")
        if out is not None:
            output_blob = _string_field(out)
        err = state.get("error")
        if err is not None:
            error_blob = _string_field(err)
    bits = [f"tool={tool}", f"status={status}"]
    if duration_ms is not None:
        bits.append(f"duration_ms={duration_ms}")
    if call_id:
        bits.append(f"call={call_id}")
    summary = "tool_use: " + " ".join(bits)
    if input_blob:
        clip, truncated = _truncate(input_blob, max_payload_chars)
        summary += f" input={clip!r}"
        if truncated:
            summary += " [truncated]"
    if output_blob:
        clip, truncated = _truncate(output_blob, max_payload_chars)
        summary += f" output={clip!r}"
        if truncated:
            summary += " [truncated]"
    if error_blob:
        clip, truncated = _truncate(error_blob, max_payload_chars)
        summary += f" error={clip!r}"
        if truncated:
            summary += " [truncated]"
    return summary


def _step_summary(
    payload: dict[str, object], *, kind: str
) -> str:
    part = payload.get("part")
    reason = ""
    tokens_blob = ""
    cost: Optional[float] = None
    if isinstance(part, dict):
        reason = _string_field(part.get("reason"))
        tokens = part.get("tokens")
        if isinstance(tokens, dict):
            total = tokens.get("total")
            inp = tokens.get("input")
            out = tokens.get("output")
            tokens_blob = (
                f"tokens(total={total}, input={inp}, output={out})"
            )
        cost_value = part.get("cost")
        if isinstance(cost_value, (int, float)):
            cost = float(cost_value)
    bits = [f"{kind}"]
    if reason:
        bits.append(f"reason={reason}")
    if tokens_blob:
        bits.append(tokens_blob)
    if cost is not None:
        bits.append(f"cost={cost:.6f}")
    return " ".join(bits)


def _part_summary(
    payload: dict[str, object], *, kind: str, max_field_chars: int
) -> str:
    part = payload.get("part")
    text = ""
    if isinstance(part, dict):
        text = _string_field(part.get("text"))
    if not text:
        return f"{kind}: <empty>"
    clip, truncated = _truncate(text, max_field_chars)
    summary = f"{kind}: {clip!r}"
    if truncated:
        summary += " [truncated]"
    return summary


def _error_summary(payload: dict[str, object]) -> str:
    err = payload.get("error")
    if isinstance(err, dict):
        name = _string_field(err.get("name")) or "Error"
        msg = _string_field(err.get("message")) or ""
        return f"error: {name}: {msg}"
    return f"error: {_string_field(err)!r}"


def summarize_event(
    payload: dict[str, object],
    *,
    max_field_chars: int = DEFAULT_MAX_FIELD_CHARS,
    max_tool_payload_chars: int = DEFAULT_MAX_TOOL_PAYLOAD_CHARS,
    redact_payloads: bool = True,
) -> tuple[str, bool]:
    """Return ``(summary_line, was_redacted)`` for one NDJSON event.

    ``was_redacted`` is ``True`` when any payload field was clipped or
    omitted for the bounded view. ``redact_payloads=True`` (default)
    hides tool inputs / outputs and reasoning text by truncation
    rather than full removal so an Operator can still see whether a
    tool was called and how much it produced. The full raw line is
    always available via the ``--raw`` flag.
    """

    if not isinstance(payload, dict):
        return "<malformed>", True
    event_type = _string_field(payload.get("type")) or "<unknown>"
    if redact_payloads:
        # Cap what is shown for tool payloads and reasoning.
        if event_type == "tool_use":
            return _tool_summary(
                payload, max_payload_chars=max_tool_payload_chars
            ), True
        if event_type == "reasoning":
            return _part_summary(
                payload,
                kind="reasoning",
                max_field_chars=max_field_chars,
            ), True
    if event_type == "step_start":
        return _step_summary(payload, kind="step_start"), False
    if event_type == "step_finish":
        return _step_summary(payload, kind="step_finish"), False
    if event_type == "text":
        return _part_summary(
            payload, kind="text", max_field_chars=max_field_chars
        ), False
    if event_type == "error":
        return _error_summary(payload), False
    # Unknown type: render the JSON itself, truncated if huge.
    blob = json.dumps(payload, ensure_ascii=False)
    clip, truncated = _truncate(blob, max_field_chars)
    return f"unknown({event_type}): {clip!r}", truncated


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_ndjson_lines(
    lines: Iterable[str],
) -> Iterator[tuple[int, str, Optional[StreamEvent]]]:
    """Yield ``(line_no, raw, event_or_none)`` for each input line.

    A line is yielded **even when it does not parse**, with
    ``event=None`` and a parser-side ``malformed_lines`` tally kept
    by the caller. A trailing partial line (no newline) is yielded as
    the last entry with ``StreamEvent.summary`` flagged and
    ``truncated=True`` so the caller can decide whether to surface or
    drop it.
    """

    for line_no, raw_line in enumerate(lines, start=1):
        # Tolerate trailing CR-only artefacts.
        stripped = raw_line.rstrip("\r\n")
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            yield line_no, raw_line, None
            continue
        if not isinstance(payload, dict):
            yield line_no, raw_line, None
            continue
        event_type = _string_field(payload.get("type")) or "unknown"
        timestamp_ms_raw = payload.get("timestamp")
        timestamp_ms = (
            int(timestamp_ms_raw)
            if isinstance(timestamp_ms_raw, (int, float))
            else 0
        )
        session_id = _string_field(payload.get("sessionID"))
        summary, redacted = summarize_event(payload)
        truncated = redacted
        yield (
            line_no,
            raw_line,
            StreamEvent(
                line_no=line_no,
                raw=raw_line,
                payload=payload,
                event_type=event_type,
                timestamp_ms=timestamp_ms,
                session_id=session_id,
                summary=summary,
                truncated=truncated,
            ),
        )


def read_events(
    path: Path,
    *,
    max_bytes: Optional[int] = None,
) -> tuple[list[StreamEvent], ParseStats]:
    """Parse a complete NDJSON file into ``(events, stats)``.

    ``max_bytes`` bounds how much of the file is read; when the file
    exceeds the budget, the parser reads the tail and flags
    :attr:`ParseStats.truncated_tail`. The function is the read-only
    follower in its simplest form — it never writes and never blocks
    on a still-growing file.
    """

    stats = ParseStats()
    events: list[StreamEvent] = []
    if not path.exists():
        return events, stats

    text, was_truncated = _read_text_bounded(
        path, max_bytes=max_bytes
    )
    if text is None:
        stats = ParseStats(truncated_tail=True)
        return events, stats

    lines = text.splitlines(keepends=True)
    initial_stats = ParseStats(
        truncated_tail=was_truncated,
        final_partial_line=(
            lines[-1].rstrip("\r\n")
            if was_truncated and lines and not lines[-1].endswith("\n")
            else ""
        ),
    )

    for line_no, raw, event in parse_ndjson_lines(lines):
        initial_stats = ParseStats(
            total_lines=initial_stats.total_lines + 1,
            parsed_lines=initial_stats.parsed_lines
            + (1 if event is not None else 0),
            malformed_lines=initial_stats.malformed_lines
            + (1 if event is None else 0),
            unknown_types=initial_stats.unknown_types
            + (
                1
                if event is not None
                and event.event_type not in KNOWN_EVENT_TYPES
                else 0
            ),
            truncated_tail=initial_stats.truncated_tail,
            final_partial_line=initial_stats.final_partial_line,
        )
        if event is not None:
            events.append(event)
    return events, initial_stats


def _read_text_bounded(
    path: Path, *, max_bytes: Optional[int]
) -> tuple[Optional[str], bool]:
    """Read the file; return ``(text, was_truncated)``.

    ``max_bytes`` is a *soft* bound. When the file is larger than the
    budget the reader jumps to ``size - max_bytes`` and reports the
    tail; the parser then flags :attr:`ParseStats.truncated_tail`.
    The function is intended for bounded UI rendering, not for
    streaming full logs. The boolean second tuple element is
    ``True`` whenever the reader had to drop the file head to
    satisfy the budget — even if the remaining tail happens to end
    in a newline, the records are still partial.
    """

    if max_bytes is None or max_bytes <= 0:
        return path.read_text(encoding="utf-8", errors="replace"), False
    size = path.stat().st_size
    if size <= max_bytes:
        return path.read_text(encoding="utf-8", errors="replace"), False
    # Skip the head; report the tail so the reader sees the most
    # recent events. The truncation flag is sticky: any reader
    # that had to drop the head to satisfy the budget returns
    # ``was_truncated=True`` so a downstream UI can show the user
    # that older records are missing.
    with path.open("rb") as handle:
        handle.seek(size - max_bytes)
        tail = handle.read(max_bytes)
    try:
        return tail.decode("utf-8", errors="replace"), True
    except Exception:
        return None, True


# ---------------------------------------------------------------------------
# Summariser for a whole log
# ---------------------------------------------------------------------------


def summarize_log(
    path: Path,
    *,
    max_bytes: Optional[int] = None,
    max_field_chars: int = DEFAULT_MAX_FIELD_CHARS,
    max_tool_payload_chars: int = DEFAULT_MAX_TOOL_PAYLOAD_CHARS,
) -> str:
    """Render a compact text summary of one capture file.

    Output shape:

    .. code-block:: text

        # <label> — path=<path> bytes=<N>
        # protocol=<protocol> lines=<L> parsed=<P> malformed=<M>
        <line-no> <event-type> <summary>
        ...

    When the file is missing, the function returns a single
    ``"<label>: missing"`` line so the caller can distinguish
    "not started yet" from "started but no events yet" without
    touching the filesystem again.
    """

    if not path.exists():
        return f"# {path.name}: missing"
    events, stats = read_events(path, max_bytes=max_bytes)
    header = (
        f"# {path.name} — path={path} "
        f"bytes={path.stat().st_size}"
    )
    counters = (
        f"# protocol=ndjson lines={stats.total_lines} "
        f"parsed={stats.parsed_lines} "
        f"malformed={stats.malformed_lines} "
        f"unknown={stats.unknown_types}"
    )
    if stats.truncated_tail:
        counters += " truncated_tail=true"
    body = "\n".join(
        f"{event.line_no:>5} {event.event_type:<12} {event.summary}"
        for event in events
    )
    parts = [header, counters]
    if body:
        parts.append(body)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Follower
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FollowConfig:
    """Tuning knobs for :func:`follow_stream`."""

    follow: bool = False
    poll_interval_seconds: float = 0.25
    max_events: Optional[int] = None
    max_bytes: Optional[int] = None
    max_field_chars: int = DEFAULT_MAX_FIELD_CHARS
    max_tool_payload_chars: int = DEFAULT_MAX_TOOL_PAYLOAD_CHARS
    raw: bool = False
    on_partial_line: Callable[[str], None] | None = None


def follow_stream(
    path: Path,
    config: FollowConfig | None = None,
) -> Iterator[str]:
    """Replay then optionally follow ``path`` line by line.

    Each yielded string is one **rendered** line:

    * NDJSON events (when the capture protocol is ``ndjson``) become a
      single bounded summary line per complete record; raw JSON is
      yielded only when ``config.raw`` is ``True``.
    * ``line_text`` and ``opaque_bytes`` captures yield each completed
      line verbatim (UTF-8 with replacement) or each chunk when no
      newline is observed yet.

    The follower **buffers a trailing partial line**; if the file ends
    without a newline, the partial line is yielded with a ``[partial]``
    suffix so a downstream consumer can decide whether to treat it as
    evidence. A crash that truncates a row cannot produce a fake
    completed event. When ``max_bytes`` is positive and the file is
    larger, replay starts at the bounded tail and emits a
    ``# truncated_tail=true`` marker before the tail content.

    The function is the only safe way to read a still-growing capture
    file. It never sits between the worker and its durable files —
    the file remains the canonical sink; the follower is a read-only
    observer.
    """

    cfg = config or FollowConfig()
    if not path.exists():
        # The worker may not have started writing yet. In follow mode
        # we wait briefly; in replay mode we report and exit.
        if not cfg.follow:
            yield f"# {path.name}: missing"
            return
        deadline = time.monotonic() + max(cfg.poll_interval_seconds * 4, 1.0)
        while not path.exists():
            if time.monotonic() >= deadline:
                yield f"# {path.name}: missing"
                return
            time.sleep(cfg.poll_interval_seconds)

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        if cfg.max_bytes is not None and cfg.max_bytes > 0:
            size = path.stat().st_size
            if size > cfg.max_bytes:
                handle.seek(size - cfg.max_bytes)
                yield "# truncated_tail=true"
        buffer = ""
        last_size = handle.tell()
        emitted = 0
        while True:
            chunk = handle.read()
            if chunk:
                buffer += chunk
                while True:
                    newline_index = buffer.find("\n")
                    if newline_index == -1:
                        break
                    raw_line = buffer[: newline_index + 1]
                    buffer = buffer[newline_index + 1 :]
                    for rendered in _render_raw_line(
                        raw_line.rstrip("\r\n"),
                        raw=cfg.raw,
                        max_field_chars=cfg.max_field_chars,
                        max_tool_payload_chars=cfg.max_tool_payload_chars,
                    ):
                        yield rendered
                        emitted += 1
                        if cfg.max_events is not None and emitted >= cfg.max_events:
                            return
            current_size = path.stat().st_size
            grew = current_size > last_size
            last_size = current_size
            if not cfg.follow:
                if buffer:
                    # Trailing partial line without a newline.
                    for rendered in _render_partial_line(
                        buffer, raw=cfg.raw
                    ):
                        yield rendered
                    buffer = ""
                return
            if not grew and handle.tell() == current_size:
                # Nothing new to read and we are at EOF. If the
                # follower is watching a still-running worker, the
                # next iteration will pick up the next chunk.
                if buffer:
                    # Hold the partial line in the buffer; do not
                    # yield it yet so we never claim a partial event
                    # is complete.
                    pass
                time.sleep(cfg.poll_interval_seconds)
                # Re-check existence: file may have been removed.
                if not path.exists():
                    return


def _render_raw_line(
    stripped: str,
    *,
    raw: bool,
    max_field_chars: int,
    max_tool_payload_chars: int,
) -> Iterator[str]:
    """Render one complete captured line.

    When ``raw`` is ``True`` the raw NDJSON text is yielded. Otherwise
    the bounded summary is rendered.
    """

    if not stripped:
        return iter(())
    if raw:
        return iter([stripped])
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        # Non-NDJSON line; surface it as a raw note.
        return iter([f"# non-json: {stripped!r}"])
    if not isinstance(payload, dict):
        return iter([f"# non-object: {stripped!r}"])
    summary, _redacted = summarize_event(
        payload,
        max_field_chars=max_field_chars,
        max_tool_payload_chars=max_tool_payload_chars,
    )
    event_type = _string_field(payload.get("type")) or "unknown"
    return iter([f"{event_type:<12} {summary}"])


def _render_partial_line(buffer: str, *, raw: bool) -> Iterator[str]:
    if not buffer:
        return iter(())
    if raw:
        return iter([f"{buffer} [partial]"])
    return iter([f"# partial: {buffer!r}"])


# ---------------------------------------------------------------------------
# Convenience: capture-aware follow wrapper
# ---------------------------------------------------------------------------


def follow_capture(
    capture: StreamCapture,
    *,
    config: FollowConfig | None = None,
    on_complete: Callable[[ParseStats], None] | None = None,
) -> Iterator[str]:
    """High-level wrapper that follows a capture's stdout file.

    When ``capture.protocol`` is not :data:`PROTOCOL_NDJSON`, the
    follower renders the file verbatim (with a header noting the
    protocol) so an operator can still see the worker's progress
    even when the harness emits non-NDJSON text.
    """

    cfg = config or FollowConfig()
    header = (
        f"# capture: path={capture.stdout_path} "
        f"protocol={capture.protocol} label={capture.label}"
    )
    yield header
    if capture.protocol == PROTOCOL_NDJSON:
        yield from follow_stream(capture.stdout_path, config=cfg)
        return
    # Non-NDJSON: yield verbatim lines with a leading label.
    with capture.stdout_path.open(
        "r", encoding="utf-8", errors="replace"
    ) as handle:
        for line in handle:
            stripped = line.rstrip("\r\n")
            if not stripped:
                continue
            yield f"{capture.label}: {stripped}"
            if cfg.max_events is not None:
                cfg = FollowConfig(
                    follow=cfg.follow,
                    poll_interval_seconds=cfg.poll_interval_seconds,
                    max_events=cfg.max_events - 1
                    if cfg.max_events is not None
                    else None,
                    max_bytes=cfg.max_bytes,
                    max_field_chars=cfg.max_field_chars,
                    max_tool_payload_chars=cfg.max_tool_payload_chars,
                    raw=cfg.raw,
                    on_partial_line=cfg.on_partial_line,
                )
    if on_complete is not None:
        on_complete(ParseStats())


__all__ = [
    "PROTOCOL_NDJSON",
    "PROTOCOL_LINE_TEXT",
    "PROTOCOL_OPAQUE_BYTES",
    "PROTOCOLS",
    "StreamCapture",
    "StreamEvent",
    "ParseStats",
    "FollowConfig",
    "StreamCaptureError",
    "BeadScopedPathError",
    "validate_bead_scoped_path",
    "summarize_event",
    "read_events",
    "summarize_log",
    "follow_stream",
    "follow_capture",
    "DEFAULT_MAX_FIELD_CHARS",
    "DEFAULT_MAX_TOOL_PAYLOAD_CHARS",
    "KNOWN_EVENT_TYPES",
]
