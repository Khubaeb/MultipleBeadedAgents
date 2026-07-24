"""Dependency-graph helpers (``bd dep list`` + ``bd dep cycles``).

Per the capability record (`docs/beads/capabilities.md` Dependency
direction verification): "After wiring any dependency edge, verify the
resulting graph with `bd dep list` and `bd dep cycles` before
considering the wire complete."
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import bd_client


class GraphVerificationError(RuntimeError):
    """Raised when the dependency graph fails verification."""


@dataclass(frozen=True)
class GraphState:
    raw_listing: str
    raw_cycles_output: str
    list_returncode: int
    cycles_returncode: int
    cycles_present: bool


def verify_graph(
    *,
    cwd: Path,
    bd_binary: str = "bd",
    for_issue_id: str | None = None,
) -> GraphState:
    """Run `bd dep list` + `bd dep cycles` and capture the outputs.

    ``for_issue_id`` is forwarded to ``bd dep list`` when supplied. On
    bd 1.0.4 the subcommand requires at least one issue id; passing
    one keeps the call verifiable on the real binary. When the helper
    is invoked without a known issue id (e.g. a graph-wide audit), the
    caller is responsible for tolerating the resulting non-zero
    returncode from the bare ``bd dep list`` command.

    ``bd dep cycles`` reports a clean graph as ``✓ No dependency
    cycles detected`` on bd 1.0.4 — that is a positive indicator, not
    a cycle. The helper interprets the textual output semantically.
    """

    list_args = ["dep", "list"]
    if for_issue_id:
        list_args.append(for_issue_id)
    list_proc = bd_client.call(bd_binary, args=list_args, cwd=cwd)
    cycles_proc = bd_client.call(bd_binary, args=["dep", "cycles"], cwd=cwd)
    cycles_present = _has_cycle(cycles_proc.stdout, cycles_proc.stderr)
    return GraphState(
        raw_listing=list_proc.stdout,
        raw_cycles_output=cycles_proc.stdout,
        list_returncode=list_proc.returncode,
        cycles_returncode=cycles_proc.returncode,
        cycles_present=cycles_present,
    )


def _has_cycle(stdout: str, stderr: str) -> bool:
    """Return True only when ``bd dep cycles`` actually reports a cycle.

    On bd 1.0.4 a clean graph is reported as the literal ``✓ No
    dependency cycles detected``. Any other non-empty output counts as
    a cycle evidence. The exit code is a separate signal: it is 0
    on a clean graph and 2 on a cycle. The helper inspects text so a
    custom ``bd`` variant with different text remains correct as long
    as the clean-graph indicator is the literal phrase.
    """

    text = (stdout or "") + (stderr or "")
    if not text.strip():
        return False
    return "✓ No dependency cycles detected" not in text


def assert_graph_clean(state: GraphState) -> None:
    """Refuse the wire when ``bd dep list`` or ``bd dep cycles`` failed."""

    if state.list_returncode != 0:
        raise GraphVerificationError(
            f"`bd dep list` exited {state.list_returncode}; stderr head="
            f"{state.raw_listing[:200]!r}"
        )
    if state.cycles_returncode != 0:
        raise GraphVerificationError(
            f"`bd dep cycles` exited {state.cycles_returncode}; stdout="
            f"{state.raw_cycles_output!r}"
        )
    if state.cycles_present:
        raise GraphVerificationError(
            "dependency cycle detected: " + state.raw_cycles_output.strip()
        )


def assert_wire_clean(
    *,
    cwd: Path,
    bd_binary: str = "bd",
    for_issue_id: str | None = None,
    require_listing: bool = True,
) -> GraphState:
    """Verify the graph is sane; on failure raise.

    When ``require_listing=False`` the helper tolerates a non-zero
    `bd dep list` returncode (the bare subcommand exits 1 on bd 1.0.4
    without an issue id; in that case ``for_issue_id`` should be
    supplied so the listing has valid output). The
    :class:`GraphState` returned reflects the bare exit codes so the
    audit can diagnose.

    Convenience wrapper matching the Capability record rule verbatim:
    "verify the resulting graph with `bd dep list` and `bd dep cycles`
    before considering the wire complete."
    """

    state = verify_graph(cwd=cwd, bd_binary=bd_binary, for_issue_id=for_issue_id)
    if state.list_returncode != 0 and require_listing:
        raise GraphVerificationError(
            f"`bd dep list` exited {state.list_returncode}; stderr head="
            f"{state.raw_listing[:200]!r}"
        )
    if state.cycles_returncode != 0:
        raise GraphVerificationError(
            f"`bd dep cycles` exited {state.cycles_returncode}; stdout="
            f"{state.raw_cycles_output!r}"
        )
    if state.cycles_present:
        raise GraphVerificationError(
            "dependency cycle detected: " + state.raw_cycles_output.strip()
        )
    return state
