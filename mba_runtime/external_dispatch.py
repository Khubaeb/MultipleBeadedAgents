"""External-process session adapter (audit F2 delivered; G1 + G2 closed;
example-capture capture hardening).

The Runtime exposes a real dispatch surface so Doer / Auditor
sessions are not just in-process callable stubs. The adapter
spawns an external worker subprocess, points it at the §10
``prompt.md``, and captures the artefacts the worker writes.

Capture model (example-capture implementation of the example-stream converged
design)::

    ExternalProcessSessionRunner.redirect_stdout_to_path /
    ExternalProcessSessionRunner.redirect_stderr_to_path
        point the subprocess at session-local ``run.log`` and
        ``run.err`` files. Direct file redirection is durable: the
        worker can write its NDJSON progress, OpenCode's stdout is
        decoupled from any Python ``PIPE`` buffer, and a viewer
        following the file (via ``mba_runtime.stream_capture``) does
        not sit between the worker and its durable sink.

The legacy ``PIPE``+``communicate`` path is preserved as
:attr:`ExternalProcessSessionRunner.capture_mode == "pipe"`` for
tests and for callers that explicitly opt out of direct capture.
The default is :data:`CAPTURE_MODE_DIRECT` so every production
launch is durable and live-readable without code changes.

Comment discipline (G2 fix): the adapter is the **single** writer
of the worker role-attributed Bead comment; ``drive_bead`` skips
its own ``_post_comments_for_sessions`` call when any session
is dispatched through this adapter (``posts_own_comment=True``).

Confinement model (G1 fix, corrected after the first revision):

* The worker's **allowed write root** is the **entire project
  tree** — the assignment contract declares ``<project_cwd>``.
  Workers *can* modify project source / content; they are not
  restricted to their records directory. The records directory
  (``<project_cwd>/.mba-work/<bead>/<session>/``) is just where
  the worker is told to write its working files, not the only
  write scope.
* **Arbitrary external processes cannot portably prove** that
  they did not write outside the project tree once spawned.
  The adapter **therefore does not attempt a pre/post
  filesystem diff**: that approach is racy and not portable
  across shells, OSes, and worker libraries. The runtime
  enforces confinement at the **pre-launch gate** instead.
* **Default-deny.** The adapter refuses to launch the worker
  unless one of the following holds (per the Workspace-boundary
  recovery note's #3 ``"Out-of-root writes must be rejected or
  require explicit user authority"``):
  1. ``authority.trusted_confinement`` is set to ``True`` —
     the configured host dispatcher proves it enforces the
     repository boundary (e.g. macOS sandbox / Linux user ns /
     Windows job object; out of scope for this turn).
  2. A persistent, recorded **human-origin** §11 decision for
     ``external_dispatch_unconfinement`` is present in the
     project's decisions log. Non-human-origin actors ("cli",
     "decision_fn_default") do not qualify; self-approval is
     refused with a loud error.

  A project-scoped approval is reusable across rounds so the
  runtime does not prompt the user mid-flow.

Comment discipline (G2 fix): the adapter is the **single** writer
of the worker role-attributed Bead comment; ``drive_bead`` skips
its own ``_post_comments_for_sessions`` call when any session
is dispatched through this adapter (``posts_own_comment=True``).

Dispatch contract (canonical):

1. The runtime invokes the worker with the following argv:

   ``<dispatch_argv[0]> <dispatch_argv[1..]> --bead-id <id> \
       --session-name <name> --session-dir <abs-path> --prompt <abs-path> \
       --hat <hat> --round <round_index>``

   The worker writes ``working.md``, ``result.md`` and ``comment.md``
   under ``session_dir`` for the subprocess capture protocol, and may
   also touch other project files within ``allowed_write_root``.
   ``comment.md`` must itself be sufficient for normal human review.
   ``--hat=="Workflow Auditor"`` should also write ``_verdict.txt``
   (``ACCEPT`` / ``FIND`` / ``BLOCKED``).
2. The adapter validates and posts the worker-written
   ``comment.md`` to ``bd comments add -f comment.md --actor=<hat>``.

MBA-owned worker cleanup (example-cleanup):

The adapter is the **single** capture-and-cleanup surface for
MBA-owned worker PIDs / session ids. Every dispatch produces a
:class:`MBAOwnedWorker` record; the lifecycle calls
:func:`cleanup_mba_owned_worker` after every terminal state
(successful completion, convergence, blocked-handoff, timeout,
failure, retry, or relaunch) so that no MBA-owned worker
remains alive across rounds. The cleanup helper is fail-closed:

* The helper only operates on a worker that the runtime can
  **prove** is MBA-owned (the launch receipt is the portable
  proof; the helper accepts it as the ownership anchor).
* The helper never kills a session whose ownership cannot be
  tied back to the launch — ambiguous ownership becomes a
  blocked/Human handoff, not a blind kill.
* The helper never touches a non-MBA / user session. A user
  session that happens to share a PID-equivalent is recorded
  as :data:`CleanupOutcome.action == "PROTECTED"` and the
  worker is left untouched.

The helper is injectable (``is_alive``, ``kill``,
``record_blocked``) so tests can verify the gate logic without
actually killing a real process.
"""

from __future__ import annotations

import datetime as _dt
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import bd_client
from . import user_authority
from .comments import CommentFormatError, _validate_comment_text
from .human_handoff import raise_human_needed, render_human_handoff_comment
from .stream_capture import (
    PROTOCOL_NDJSON,
    PROTOCOL_LINE_TEXT,
    PROTOCOL_OPAQUE_BYTES,
    StreamCapture,
)
from .user_authority import is_human_origin
from .workspace_safety import (
    WorkspaceSafetyError,
    assert_no_destructive_bd_init,
    assert_path_inside,
)


# ---------------------------------------------------------------------------
# Capture mode (example-capture)
# ---------------------------------------------------------------------------


# Capture mode literals. The string values are stable; tests and
# downstream readers depend on them.
CAPTURE_MODE_DIRECT: str = "direct_files"
CAPTURE_MODE_PIPE: str = "pipe"


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Centralised so tests can monkeypatch the call instead of
    ``datetime.datetime.utcnow`` (which is deprecated).
    """

    return (
        _dt.datetime.now(tz=_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _tail_text(path: Path, *, max_chars: int = 4000) -> str:
    """Return the trailing text of ``path`` (UTF-8 with replacement).

    Used to surface a bounded stderr snippet on a non-zero exit or
    timeout. The file is read in binary mode so a non-UTF-8 chunk
    cannot raise; a partial trailing record is acceptable because
    the surface is a diagnostic only.
    """

    if not path.exists():
        return ""
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if len(data) > max_chars:
        data = data[-max_chars:]
    return data.decode("utf-8", errors="replace")


class ExternalDispatchError(RuntimeError):
    """Raised when the external worker exits non-zero, the dispatch
    is refused without a recorded human-origin §11 decision, or
    the worker did not honour the artefact contract."""


@dataclass(frozen=True)
class LaunchProvenance:
    """Observable identity captured at launch and checked at collection."""

    worker_session_id: str | None = None
    worker_transcript_id: str | None = None
    observed_session_id: str | None = None
    observed_transcript_id: str | None = None
    orchestrator_session_id: str | None = None
    orchestrator_transcript_id: str | None = None
    pid: int | None = None
    host_exposes_identity: bool = False

    def validate(self) -> None:
        """Reject output that is not tied to the launched worker."""

        expected = tuple(
            value.strip()
            for value in (self.worker_session_id, self.worker_transcript_id)
            if value and value.strip()
        )
        observed = tuple(
            value.strip()
            for value in (self.observed_session_id, self.observed_transcript_id)
            if value and value.strip()
        )
        orchestrator = {
            value.strip()
            for value in (
                self.orchestrator_session_id,
                self.orchestrator_transcript_id,
            )
            if value and value.strip()
        }
        if any(value in orchestrator for value in (*expected, *observed)):
            raise ExternalDispatchError(
                "worker provenance matches the Orchestrator session or transcript"
            )
        if self.host_exposes_identity:
            if not expected:
                raise ExternalDispatchError(
                    "host exposes worker identity but launch provenance recorded no "
                    "worker session or transcript id"
                )
            if not observed:
                raise ExternalDispatchError(
                    "host exposes worker identity but output has no worker session "
                    "or transcript id"
                )
            if not any(value in observed for value in expected):
                raise ExternalDispatchError(
                    "worker output cannot be tied to the launched worker identity"
                )
        elif self.pid is None:
            raise ExternalDispatchError(
                "worker launch has no PID or session identity to prove a separate "
                "worker session"
            )


def validate_launch_provenance(provenance: LaunchProvenance) -> None:
    """Validate launch provenance before accepting any worker artefact."""

    provenance.validate()


# ---------------------------------------------------------------------------
# MBA-owned worker cleanup (example-cleanup)
# ---------------------------------------------------------------------------


# Cleanup outcome actions. The string values are stable; tests and
# downstream readers depend on them.
CLEANUP_NOTHING: str = "NOTHING_TO_CLEAN"
CLEANUP_CLEANED: str = "CLEANED"
CLEANUP_PROTECTED: str = "PROTECTED"
CLEANUP_REFUSED: str = "REFUSED"
CLEANUP_NOT_MBA_OWNED: str = "NOT_MBA_OWNED"


# Owner values. The default is ``mba`` — the helper refuses to
# operate on every other owner, which is how a user / foreign
# session is left untouched.
OWNER_MBA: str = "mba"
OWNER_USER: str = "user"
OWNER_UNKNOWN: str = ""


@dataclass(frozen=True)
class MBAOwnedWorker:
    """The runtime's record of one MBA-owned worker session.

    The adapter captures this at launch and the lifecycle threads it
    through every terminal state (success, convergence, blocked,
    timeout, failure, retry, relaunch). Cleanup is only permitted
    against a record that ties the kill back to the original launch
    — the :attr:`LaunchProvenance.worker_session_id` plus
    :attr:`LaunchProvenance.worker_transcript_id` (when the host
    exposes them) plus the recorded :attr:`pid` are the ownership
    anchor. Without a non-empty identity binding, the cleanup helper
    refuses.

    ``owner`` is the explicit ownership marker. The default
    :data:`OWNER_MBA` is the only value that allows cleanup;
    :data:`OWNER_USER` (or any other value) means the record is
    foreign and the helper leaves the session untouched. The
    runtime populates this from the launch receipt context.

    ``ownership_proof`` is the verbatim worker-identity string the
    Orchestrator recorded in the launch receipt
    (``.mba-work/<bead>/<session>/launch.md``). When the host does
    not expose a separate transcript or session id, the proof is the
    bare PID plus the session launch context (bead, session
    name, hat); cleanup then refuses unless the *same* record is
    presented again at cleanup time.
    """

    bead_id: str
    session_name: str
    hat: str = ""
    pid: int | None = None
    session_id: str | None = None
    transcript_id: str | None = None
    worker_identity: str | None = None
    orchestrator_identity: str | None = None
    launched_at_utc: str | None = None
    model: str | None = None
    owner: str = OWNER_MBA
    ownership_proof: str = ""

    def has_identity_binding(self) -> bool:
        """True iff the record carries an identity to bind the kill to.

        A bare PID with no session / transcript / worker identity is
        *not* sufficient: cleanup would have no way to prove the
        targeted process is the worker the runtime launched. The
        caller must either supply a stronger identity (worker
        session id, transcript id, or worker identity) or refuse
        the cleanup.
        """

        if self.session_id and self.session_id.strip():
            return True
        if self.transcript_id and self.transcript_id.strip():
            return True
        if self.worker_identity and self.worker_identity.strip():
            return True
        if self.ownership_proof and self.ownership_proof.strip():
            return True
        return False

    @property
    def is_mba_owned(self) -> bool:
        """True iff the record is explicitly MBA-owned."""

        return self.owner == OWNER_MBA


@dataclass(frozen=True)
class CleanupOutcome:
    """The result of a cleanup attempt.

    ``action`` is one of the :data:`CLEANUP_*` constants. ``reason``
    is a human-readable summary suitable for logs and for the
    Orchestrator's decision record. ``pid`` / ``session_id`` are
    the values the operation actually targeted (or ``None`` when
    nothing was targeted).
    """

    action: str
    reason: str
    pid: int | None = None
    session_id: str | None = None
    blocked_for_human: bool = False


class WorkerCleanupError(RuntimeError):
    """Raised when cleanup must be refused (ambiguous ownership).

    The caller is expected to convert the refusal into a
    blocked/Human handoff via :func:`mba_runtime.human_handoff` —
    cleanup never raises to silently abort the round.
    """


def _default_process_is_alive(pid: int) -> bool:
    """Best-effort OS check whether ``pid`` is still alive.

    The default lives here for the few runners that wrap a real
    subprocess. Production dispatchers (Windows ``Start-Process
    -PassThru`` / Linux ``nohup`` / macOS ``launchctl``) write the
    PID into the launch receipt; the cleanup helper is what
    consults them at terminal-state time. The helper never tries
    to be cleverer than the host — it asks the host via this
    callable and trusts the answer.
    """

    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The PID exists but is owned by another user; the
        # MBACleanup cannot touch it. The caller treats this as
        # "alive but not ours" so the cleanup can refuse.
        return True
    except OSError:
        return False
    return True


def _default_process_kill(pid: int) -> None:
    """Best-effort kill; raises :class:`ProcessLookupError` if dead.

    The default targets a graceful ``SIGTERM`` on POSIX hosts and
    walks the process tree on Windows. Concrete dispatchers may
    override the callable through :func:`cleanup_mba_owned_worker`.
    """

    if pid is None or pid <= 0:
        return
    if os.name == "nt":
        # Best-effort: walk the process tree under Windows so a
        # worker that spawned children (conhost, etc.) is also
        # torn down. Skips silently if the helper is unavailable.
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        return
    try:
        os.kill(pid, 15)  # SIGTERM
    except ProcessLookupError:
        return
    except PermissionError:
        raise


def _default_record_blocked(bead_id: str, comment_text: str) -> None:
    """Default refusal sink: the Bead handoff path.

    The default delegates to ``raise_human_needed`` so the refusal
    produces the structured comment + status / assignee / label
    write the rest of the runtime expects. The Orchestrator can
    inject a no-op replacement in tests.
    """

    raise_human_needed(
        cwd=Path.cwd(),
        bead_id=bead_id,
        actor="Orchestrator",
        comment_text=comment_text,
    )


def cleanup_mba_owned_worker(
    worker: MBAOwnedWorker,
    *,
    is_alive: Callable[[int], bool] | None = None,
    kill: Callable[[int], None] | None = None,
    record_blocked: Callable[[str, str], None] | None = None,
    orchestrator_session_id: str | None = None,
    orchestrator_transcript_id: str | None = None,
) -> CleanupOutcome:
    """Clean up an MBA-owned worker if it is still alive.

    The cleanup helper is the **single** gate that decides whether
    a process may be killed on behalf of the Orchestrator. It is
    fail-closed in three directions:

    1. **No identity binding** — when the worker record carries
       neither a session id, transcript id, worker identity, nor
       an explicit ``ownership_proof`` string, the cleanup refuses
       and demands a Human handoff. PID-only cleanup is not
       acceptable when stronger identity was available at launch.
    2. **Non-MBA / user session** — when the recorded identity
       matches a non-MBA / user session, the cleanup leaves the
       session untouched and reports ``PROTECTED``.
    3. **Orchestrator identity** — when the recorded identity
       matches the Orchestrator's own session or transcript id,
       the cleanup refuses with a loud error. The assistant must
       never kill its own session.

    Successful cleanup returns :data:`CLEANUP_CLEANED`; a no-op
    (worker already gone) returns :data:`CLEANUP_NOTHING`; a
    refusal returns :data:`CLEANUP_REFUSED` with
    ``blocked_for_human=True`` so the caller can route the round
    to the blocked/Human handoff path.

    ``is_alive`` / ``kill`` / ``record_blocked`` are injectable so
    tests can verify the gate logic without spawning or killing
    a real process. The defaults are the OS-level helpers above.
    """

    is_alive_fn = is_alive or _default_process_is_alive
    kill_fn = kill or _default_process_kill
    blocked_fn = record_blocked or _default_record_blocked

    # Non-MBA gate: a foreign / user session is never touched.
    # The runtime owns the launch record's ``owner`` field; a
    # session that is not explicitly MBA-owned is left alone.
    if not worker.is_mba_owned:
        blocked_fn(
            worker.bead_id,
            (
                f"Refused to clean up session "
                f"{worker.session_id or worker.worker_identity!r}: "
                f"the record is not MBA-owned "
                f"(owner={worker.owner!r}). The runtime never kills "
                f"the user's own Claude / OpenCode / Codex sessions. "
                f"Stop the worker manually if cleanup is required."
            ),
        )
        return CleanupOutcome(
            action=CLEANUP_NOT_MBA_OWNED,
            reason=(
                f"worker record is not MBA-owned "
                f"(owner={worker.owner!r}); refusing to kill"
            ),
            pid=worker.pid,
            session_id=worker.session_id,
            blocked_for_human=True,
        )

    if worker.pid is None and not worker.session_id:
        return CleanupOutcome(
            action=CLEANUP_NOTHING,
            reason=(
                "no PID or session id recorded; nothing to clean up"
            ),
        )

    # The Orchestrator identity gate: refuse to kill a session that
    # is recorded as the Orchestrator's own.
    if orchestrator_session_id and worker.session_id:
        if orchestrator_session_id == worker.session_id:
            blocked_fn(
                worker.bead_id,
                (
                    f"Refused to clean up worker session "
                    f"{worker.session_id!r}: "
                    f"the recorded identity matches the Orchestrator "
                    f"session. The runtime never kills its own session. "
                    f"Stop the round and review the launch receipt at "
                    f".mba-work/{worker.bead_id}/{worker.session_name}/launch.md."
                ),
            )
            return CleanupOutcome(
                action=CLEANUP_REFUSED,
                reason="worker identity matches the Orchestrator session",
                blocked_for_human=True,
                session_id=worker.session_id,
            )
    if orchestrator_transcript_id and worker.transcript_id:
        if orchestrator_transcript_id == worker.transcript_id:
            blocked_fn(
                worker.bead_id,
                (
                    f"Refused to clean up worker transcript "
                    f"{worker.transcript_id!r}: the recorded identity "
                    f"matches the Orchestrator transcript. The runtime "
                    f"never kills its own session."
                ),
            )
            return CleanupOutcome(
                action=CLEANUP_REFUSED,
                reason="worker identity matches the Orchestrator transcript",
                blocked_for_human=True,
                session_id=worker.session_id,
            )

    # Identity binding gate: refuse to kill unless the record
    # carries an identity that can be tied back to the launch.
    if not worker.has_identity_binding():
        blocked_fn(
            worker.bead_id,
            (
                f"Refused to clean up worker for "
                f"Bead {worker.bead_id!r} session {worker.session_name!r}: "
                f"PID={worker.pid!r} but no session id, transcript id, "
                f"worker identity, or ownership proof was recorded. "
                f"Cleanup is only permitted against a record that ties "
                f"the kill back to the launch receipt. Without identity "
                f"binding, the kill could target the user's own session. "
                f"Review the launch receipt at "
                f".mba-work/{worker.bead_id}/{worker.session_name}/launch.md "
                f"and either supply the missing identity or stop the round."
            ),
        )
        return CleanupOutcome(
            action=CLEANUP_REFUSED,
            reason=(
                "no identity binding on the worker record; refusing "
                "to kill without a session id, transcript id, worker "
                "identity, or ownership proof"
            ),
            pid=worker.pid,
            blocked_for_human=True,
        )

    # Survives the gate: the worker is MBA-owned and we have an
    # identity binding. Decide whether to kill.
    if worker.pid is None or worker.pid <= 0:
        return CleanupOutcome(
            action=CLEANUP_NOTHING,
            reason=(
                "worker identity recorded but no PID to kill; "
                "the host must clean up via the host's session manager"
            ),
            session_id=worker.session_id,
        )

    if not is_alive_fn(worker.pid):
        return CleanupOutcome(
            action=CLEANUP_NOTHING,
            reason="worker PID already exited; no cleanup needed",
            pid=worker.pid,
            session_id=worker.session_id,
        )

    try:
        kill_fn(worker.pid)
    except ProcessLookupError:
        return CleanupOutcome(
            action=CLEANUP_NOTHING,
            reason="worker PID exited between alive-check and kill",
            pid=worker.pid,
            session_id=worker.session_id,
        )
    except PermissionError as exc:
        # A live PID we cannot kill is a different ownership signal:
        # maybe the user took over the PID. Treat as protected.
        blocked_fn(
            worker.bead_id,
            (
                f"Cleanup could not kill worker PID {worker.pid} for "
                f"session {worker.session_name!r}: "
                f"{exc!r}. The runtime never escalates a permission "
                f"refusal into a force-kill; treat the worker as "
                f"protected and ask the user to stop it manually."
            ),
        )
        return CleanupOutcome(
            action=CLEANUP_PROTECTED,
            reason=(
                f"permission denied killing PID {worker.pid}; "
                f"worker left untouched"
            ),
            pid=worker.pid,
            session_id=worker.session_id,
            blocked_for_human=True,
        )

    return CleanupOutcome(
        action=CLEANUP_CLEANED,
        reason=(
            f"cleaned up MBA-owned worker PID {worker.pid} for "
            f"session {worker.session_name!r}"
        ),
        pid=worker.pid,
        session_id=worker.session_id,
    )


def cleanup_worker_or_record_block(
    worker: MBAOwnedWorker,
    *,
    is_alive: Callable[[int], bool] | None = None,
    kill: Callable[[int], None] | None = None,
    record_blocked: Callable[[str, str], None] | None = None,
    orchestrator_session_id: str | None = None,
    orchestrator_transcript_id: str | None = None,
) -> CleanupOutcome:
    """Convenience wrapper that turns a refusal into a Bead handoff.

    The wrapped helper is the public surface the Orchestrator uses
    at terminal-state time. On a refusal it posts the structured
    Bead handoff comment and marks the Bead ``blocked``; a
    successful cleanup returns the outcome without a Bead write.
    """

    return cleanup_mba_owned_worker(
        worker,
        is_alive=is_alive,
        kill=kill,
        record_blocked=record_blocked,
        orchestrator_session_id=orchestrator_session_id,
        orchestrator_transcript_id=orchestrator_transcript_id,
    )


@dataclass(frozen=True)
class AuthorityContext:
    """Inputs the gate consults before launching the worker.

    Attributes
    ----------
    decision_fn
        :func:`user_authority.gate` ``decision_fn``. The default
        behaviour (see :func:`authority_decision_fn_from_decisions_file`)
        reads a JSONL decisions log, refuses self / ``cli`` /
        ``decision_fn_default`` actors, and raises
        :class:`UserAuthorityRequired` when no human-origin approval
        exists. Tests plumb in-memory stubs.
    project_cwd
        The project root. Spawn cwd for the worker; default write
        root.
    trusted_confinement
        ``True`` when the configured host dispatcher proves
        repository-boundary enforcement (job object, sandbox-exec,
        bwrap, etc.). Out of scope here — when ``True``, the gate
        is skipped. ``False`` (the default) requires a recorded
        human-origin decision.
    decisions_path
        Optional path to a JSONL decisions file the gate can read
        when ``decision_fn`` is left at its default. When both
        ``decision_fn`` and ``decisions_path`` are supplied,
        ``decision_fn`` wins.
    """

    decision_fn: Callable[[str], user_authority.AuthorityDecision]
    project_cwd: Path
    trusted_confinement: bool = False
    decisions_path: Optional[Path] = None


@dataclass(frozen=True)
class ExternalProcessSessionRunner:
    """Dispatch Doer / Auditor sessions through a real external process.

    Parameters
    ----------
    dispatch_argv
        Argv prefix the runtime prepends to the worker flags.
    authority
        :class:`AuthorityContext` describing how to consult the
        §11 user-authority gate. Pass an in-memory stub via
        :func:`authority_decision_fn_allow_when` for tests that
        want to opt in to dispatch without a real decisions file.
    allowed_write_root
        **Project root** (the worker's whole project is writable;
        the adapter does NOT gate individual file writes — only
        the pre-launch gate decides whether to spawn the worker).
        Defaults to ``authority.project_cwd``.
    records_dir
        The ``.mba-work/<bead>/<session>/`` scratch the worker
        uses. The worker is told to write ``working.md`` /
        ``result.md`` / ``comment.md`` here.
    working_filename, result_filename, comment_filename
        Filenames the worker is required to write under
        ``records_dir``. The adapter raises if any is missing.
    timeout_seconds
        Per-session subprocess timeout.
    """

    dispatch_argv: tuple[str, ...]
    authority: AuthorityContext
    allowed_write_root: Path | None = None
    records_dir: Path | None = None
    working_filename: str = "working.md"
    result_filename: str = "result.md"
    comment_filename: str = "comment.md"
    report_filename: str = "report.md"
    timeout_seconds: float | None = 60.0
    approved_disposable_workspace: bool = False
    launch_provenance: LaunchProvenance | None = None
    provenance_resolver: Callable[[subprocess.CompletedProcess[str], object], LaunchProvenance] | None = None
    handoff_on_failure: bool = True
    # example-capture: durable capture surface. The default mode redirects
    # the worker's stdout/stderr to session-local ``run.log`` /
    # ``run.err`` files. Direct redirection keeps capture durable
    # across viewer restart and decouples the worker from any
    # Python ``PIPE`` buffer. Override to ``CAPTURE_MODE_PIPE`` only
    # for tests that need to observe in-memory stdout/stderr.
    capture_mode: str = CAPTURE_MODE_DIRECT
    capture_protocol: str = PROTOCOL_LINE_TEXT
    capture_stdout_filename: str = "run.log"
    capture_stderr_filename: str = "run.err"

    # MBA-owned worker cleanup injection (example-cleanup). The runner
    # captures the launched PID / session id at spawn time and
    # threads an :class:`MBAOwnedWorker` record through every
    # terminal state. The cleanup callables are injectable so
    # tests can verify the gate logic without spawning or killing
    # a real process; the defaults are the OS-level helpers.
    cleanup_is_alive: Callable[[int], bool] | None = None
    cleanup_kill: Callable[[int], None] | None = None
    cleanup_record_blocked: Callable[[str, str], None] | None = None
    cleanup_orchestrator_session_id: str | None = None
    cleanup_orchestrator_transcript_id: str | None = None
    # When ``True`` (the default), the runner calls
    # :func:`cleanup_mba_owned_worker` after the subprocess exits
    # non-zero or times out, and again before it returns its
    # :class:`SessionOutcome`. The default matches the spec:
    # every terminal state cleans up any matching MBA-owned
    # worker that is still alive.
    cleanup_on_terminal_state: bool = True

    posts_own_comment: bool = True

    def _build_mba_owned_worker(
        self,
        brief,
        *,
        proc: "subprocess.Popen[str] | None",
    ) -> MBAOwnedWorker:
        """Build the launch-time :class:`MBAOwnedWorker` record.

        The record is the cleanup gate's only portable proof the
        process / session belongs to MBA. The runner fills it
        from the captured PID plus the configured
        :class:`LaunchProvenance` (worker session id, transcript
        id, host-exposed identity).
        """

        pid: int | None = None
        if proc is not None:
            pid = proc.pid
        provenance = self.launch_provenance
        worker_session_id = None
        worker_transcript_id = None
        worker_identity = None
        orchestrator_identity = None
        host_exposes_identity = False
        if provenance is not None:
            worker_session_id = provenance.worker_session_id
            worker_transcript_id = provenance.worker_transcript_id
            worker_identity = provenance.worker_transcript_id or provenance.worker_session_id
            orchestrator_identity = (
                provenance.orchestrator_session_id
                or provenance.orchestrator_transcript_id
            )
            host_exposes_identity = provenance.host_exposes_identity
        proof = (
            f"bead={brief.bead_id};session={brief.session_name};"
            f"hat={brief.hat};round={brief.round_index};"
            f"pid={pid!r}"
        )
        return MBAOwnedWorker(
            bead_id=brief.bead_id,
            session_name=brief.session_name,
            hat=brief.hat,
            pid=pid,
            session_id=worker_session_id,
            transcript_id=worker_transcript_id,
            worker_identity=worker_identity,
            orchestrator_identity=orchestrator_identity,
            ownership_proof=proof,
        )

    def _cleanup_terminal_state(
        self,
        worker: MBAOwnedWorker,
    ) -> CleanupOutcome:
        """Run the cleanup gate at a terminal state.

        No-ops when ``cleanup_on_terminal_state`` is disabled.
        Defaults to :func:`cleanup_mba_owned_worker` with the
        runner's injected callables.
        """

        if not self.cleanup_on_terminal_state:
            return CleanupOutcome(
                action=CLEANUP_NOTHING,
                reason="cleanup_on_terminal_state disabled",
                pid=worker.pid,
                session_id=worker.session_id,
            )
        return cleanup_mba_owned_worker(
            worker,
            is_alive=self.cleanup_is_alive,
            kill=self.cleanup_kill,
            record_blocked=self.cleanup_record_blocked,
            orchestrator_session_id=self.cleanup_orchestrator_session_id,
            orchestrator_transcript_id=self.cleanup_orchestrator_transcript_id,
        )

    def _effective_capture_mode(self) -> str:
        """Return the capture mode the runner will actually use.

        Defaults to :data:`CAPTURE_MODE_DIRECT`. Tests can pin the
        runner to :data:`CAPTURE_MODE_PIPE` to keep the legacy
        in-memory behaviour.
        """

        mode = (self.capture_mode or CAPTURE_MODE_DIRECT).strip()
        if mode not in {CAPTURE_MODE_DIRECT, CAPTURE_MODE_PIPE}:
            # Loud refusal — silent fallback would mask a typo.
            raise ExternalDispatchError(
                f"unknown capture_mode={mode!r}; expected one of "
                f"{CAPTURE_MODE_DIRECT!r} or {CAPTURE_MODE_PIPE!r}"
            )
        return mode

    def _prepare_capture(
        self, records_dir: Path, brief
    ) -> StreamCapture:
        """Build the :class:`StreamCapture` record for this dispatch.

        Creates the ``run.log`` / ``run.err`` files (empty) so the
        subprocess can ``Popen(..., stdout=file, stderr=file)`` with
        the file already open. The capture is durable whether the
        worker crashes, times out, or completes normally — the
        files remain readable on disk.
        """

        records_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = records_dir / self.capture_stdout_filename
        stderr_path = records_dir / self.capture_stderr_filename
        # Pre-create empty files so the launch receipt and the
        # follower can read them even before the worker flushes
        # anything. ``Popen`` re-opens them in write mode, which
        # truncates; that is the desired behaviour.
        stdout_path.touch(exist_ok=True)
        stderr_path.touch(exist_ok=True)
        return StreamCapture(
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            protocol=self.capture_protocol,
            captured_at=_utc_now_iso(),
            label=self.capture_stdout_filename,
        )

    def _surface_failure(
        self,
        *,
        bead_id: str,
        records_dir: Path,
        reason: str,
    ) -> None:
        if not self.handoff_on_failure:
            return
        comment_path = records_dir / "handoff-comment.md"
        comment_text = render_human_handoff_comment(
            decision_needed="Resolve the external worker dispatch failure before this Bead can continue.",
            options=("resume the worker", "relaunch with a fresh receipt", "stop and revise the assignment"),
            recommendation="keep the Bead blocked until a valid worker report and comment are available",
            detail=reason,
        )
        raise_human_needed(
            cwd=self.authority.project_cwd.resolve(),
            bead_id=bead_id,
            actor="Orchestrator",
            comment_text=comment_text,
            comment_path=comment_path,
        )

    def __call__(self, brief) -> object:  # type: ignore[no-untyped-def]
        """Spawn the external worker; capture artefacts; post comment."""

        from .lifecycle import SessionOutcome

        project_cwd = self.authority.project_cwd.resolve()
        records_dir = (
            self.records_dir
            or (project_cwd / ".mba-work" / brief.bead_id / brief.session_name)
        ).resolve()
        allowed_write_root = (self.allowed_write_root or project_cwd).resolve()

        try:
            assert_no_destructive_bd_init(
                self.dispatch_argv,
                approved_disposable=self.approved_disposable_workspace,
            )
            assert_path_inside(project_cwd, records_dir, label="records_dir")
            assert_path_inside(project_cwd, Path(brief.prompt_path), label="prompt_path")
            assert_path_inside(project_cwd, allowed_write_root, label="allowed_write_root")
        except WorkspaceSafetyError as exc:
            raise ExternalDispatchError(str(exc)) from exc

        # G1 corrected: the runtime does **not** attempt a pre/post
        # file-diff. The confinement decision is pre-launch: the
        # adapter launches only when either trusted_confinement is
        # true OR a recorded human-origin decision accepts the
        # dispatcher risk.
        #
        # H1 fix: ``user_authority.gate`` returns the decision (it
        # only raises when ``decision_fn`` is ``None`` or returns the
        # wrong type); an explicit refusal comes back as
        # ``AuthorityDecision(approved=False)``. The adapter must
        # enforce that verdict — capturing the return value and
        # refusing to launch when ``not decision.approved``. Relying
        # solely on the decision_fn to raise would let any return-style
        # refusal (e.g. ``authority_decision_fn_refuse_when``) bypass
        # the gate.
        if not self.authority.trusted_confinement:
            decision_fn = self.authority.decision_fn
            if decision_fn is None and self.authority.decisions_path is not None:
                decision_fn = authority_decision_fn_from_decisions_file(
                    self.authority.decisions_path
                )
            decision = user_authority.gate(
                "external_dispatch_unconfinement",
                decision_fn=decision_fn,
            )
            if not decision.approved:
                raise user_authority.UserAuthorityRequired(
                    action="external_dispatch_unconfinement",
                    reason=(
                        f"the recorded decision for "
                        f"'external_dispatch_unconfinement' is refused "
                        f"(actor={decision.actor!r}, "
                        f"rationale={decision.rationale!r}); the "
                        f"dispatcher refuses to launch an external "
                        f"worker against an explicit refusal"
                    ),
                )

        argv = [
            *self.dispatch_argv,
            "--bead-id",
            brief.bead_id,
            "--session-name",
            brief.session_name,
            "--session-dir",
            str(records_dir),
            "--prompt",
            str(Path(brief.prompt_path).resolve()),
            "--allowed-write-root",
            str(allowed_write_root),
            "--hat",
            brief.hat,
            "--round",
            str(brief.round_index),
        ]
        # example-capture: prepare the direct-capture files first so the
        # Popen kwargs can reference them. The files exist empty
        # before the worker spawns; the worker inherits them as
        # stdout/stderr sinks and the runtime can read them at any
        # time, including after a crash, without sitting between
        # the worker and its durable sink.
        capture = self._prepare_capture(records_dir, brief)
        capture_mode = self._effective_capture_mode()
        proc_handle: "subprocess.Popen[str] | None" = None
        if capture_mode == CAPTURE_MODE_DIRECT:
            popen_kwargs: dict[str, object] = {
                "stdout": capture.stdout_path.open(
                    "w", encoding="utf-8"
                ),
                "stderr": capture.stderr_path.open(
                    "w", encoding="utf-8"
                ),
                "cwd": str(project_cwd),
            }
        else:
            popen_kwargs = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "text": True,
                "cwd": str(project_cwd),
            }
        # Spawn via Popen so the launcher captures the worker PID
        # for the cleanup gate (example-cleanup). The dispatcher waits
        # synchronously so callers see the same blocking behaviour
        # as ``subprocess.run``; the cleanup helper is invoked at
        # every terminal state.
        worker_record = self._build_mba_owned_worker(brief, proc=None)
        try:
            proc_handle = subprocess.Popen(argv, **popen_kwargs)
        except (OSError, ValueError) as exc:
            # Close the capture sinks the helper opened above.
            for handle in (
                popen_kwargs.get("stdout"),
                popen_kwargs.get("stderr"),
            ):
                close = getattr(handle, "close", None)
                if close is not None:
                    try:
                        close()
                    except Exception:
                        pass
            reason = (
                f"external worker failed to spawn; argv={argv!r}; "
                f"error={exc!r}"
            )
            self._surface_failure(
                bead_id=brief.bead_id,
                records_dir=records_dir,
                reason=reason,
            )
            raise ExternalDispatchError(reason) from exc
        # Rebuild the record with the captured PID so the cleanup
        # gate has the actual launch-time identity.
        worker_record = self._build_mba_owned_worker(brief, proc=proc_handle)
        timed_out = False
        proc_error: BaseException | None = None
        stdout: str = ""
        stderr: str = ""
        try:
            if capture_mode == CAPTURE_MODE_DIRECT:
                try:
                    returncode_local = proc_handle.wait(
                        timeout=self.timeout_seconds
                    )
                except subprocess.TimeoutExpired as exc:
                    timed_out = True
                    proc_error = exc
                    returncode_local = None
                # Always close the sinks so the OS flushes the
                # file buffers; a crash leaves the file readable
                # without further coordination.
                for sink in (
                    getattr(proc_handle, "stdout", None),
                    getattr(proc_handle, "stderr", None),
                ):
                    close = getattr(sink, "close", None)
                    if close is not None:
                        try:
                            close()
                        except Exception:
                            pass
            else:
                try:
                    stdout, stderr = proc_handle.communicate(
                        timeout=self.timeout_seconds
                    )
                except subprocess.TimeoutExpired as exc:
                    timed_out = True
                    stdout = exc.stdout or ""
                    stderr = exc.stderr or ""
                    proc_error = exc
                except (OSError, subprocess.SubprocessError) as exc:
                    proc_error = exc
                    stdout = ""
                    stderr = str(exc)
        except (OSError, subprocess.SubprocessError) as exc:
            proc_error = exc

        if timed_out:
            # The direct-capture path keeps the partial files
            # readable; pipe mode falls back to the partial bytes
            # communicate could collect.
            stderr_tail = (
                _tail_text(capture.stderr_path)
                if capture_mode == CAPTURE_MODE_DIRECT
                else stderr
            )
            reason = (
                f"external worker exceeded timeout "
                f"{self.timeout_seconds}s; argv={argv!r}; "
                f"stderr_tail={stderr_tail.strip()!r}; "
                f"capture_path={capture.stderr_path}"
            )
            self._cleanup_terminal_state(worker_record)
            self._surface_failure(
                bead_id=brief.bead_id,
                records_dir=records_dir,
                reason=reason,
            )
            raise ExternalDispatchError(reason) from proc_error

        returncode = (
            returncode_local
            if capture_mode == CAPTURE_MODE_DIRECT
            else (proc_handle.returncode if proc_handle is not None else 1)
        )
        if returncode != 0:
            stderr_tail = (
                _tail_text(capture.stderr_path)
                if capture_mode == CAPTURE_MODE_DIRECT
                else stderr
            )
            reason = (
                f"external worker exited {returncode}; argv={argv!r}; "
                f"stderr_tail={stderr_tail.strip()!r}; "
                f"capture_path={capture.stderr_path}"
            )
            self._cleanup_terminal_state(worker_record)
            self._surface_failure(
                bead_id=brief.bead_id,
                records_dir=records_dir,
                reason=reason,
            )
            raise ExternalDispatchError(reason)

        proc = subprocess.CompletedProcess(
            args=argv,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

        provenance = self.launch_provenance
        if self.provenance_resolver is not None:
            provenance = self.provenance_resolver(proc, brief)
        if provenance is not None:
            try:
                validate_launch_provenance(provenance)
            except ExternalDispatchError as exc:
                self._surface_failure(
                    bead_id=brief.bead_id,
                    records_dir=records_dir,
                    reason=str(exc),
                )
                raise

        report_path = records_dir / self.report_filename
        result_path = records_dir / self.result_filename
        missing = [
            label
            for path, label in (
                (records_dir / self.working_filename, "working"),
                (report_path if report_path.exists() else result_path, "report"),
                (records_dir / self.comment_filename, "comment"),
            )
            if not path.exists()
        ]
        if missing:
            reason = (
                "external worker did not produce the required worker artefacts: "
                + ", ".join(missing)
                + "; the Bead cannot remain in progress without a report and comment"
            )
            self._surface_failure(
                bead_id=brief.bead_id,
                records_dir=records_dir,
                reason=reason,
            )
            raise ExternalDispatchError(reason)

        working_text = (records_dir / self.working_filename).read_text(
            encoding="utf-8"
        )
        result_text = (report_path if report_path.exists() else result_path).read_text(
            encoding="utf-8"
        )
        comment_path = records_dir / self.comment_filename

        comment_text = comment_path.read_text(encoding="utf-8")
        try:
            _validate_comment_text(comment_text)
        except CommentFormatError as exc:
            raise ExternalDispatchError(
                f"external worker wrote invalid Bead comment: {exc}"
            ) from exc

        # Auditor side-channel — workers may write ``_verdict.txt``
        # to feed the §8 convergence loop.
        verdict_file = records_dir / "_verdict.txt"
        if verdict_file.exists():
            verdict_text = verdict_file.read_text(encoding="utf-8").strip()
            if verdict_text not in ("ACCEPT", "FIND", "BLOCKED"):
                raise ExternalDispatchError(
                    f"external worker wrote an unrecognised verdict "
                    f"{verdict_text!r}; expected one of ACCEPT/FIND/BLO"
                    f"CKED"
                )
            next_state = verdict_text
            verification = (
                f"Auditor verdict {verdict_text!r} (round "
                f"{brief.round_index}) — read from {verdict_file}"
            )
        else:
            next_state = f"external worker round {brief.round_index} completed"
            verification = "external worker exit 0 and artefacts present"

        # The adapter is the single writer of the worker's brief
        # role-attributed Bead comment (G2 fix; ``posts_own_comment``
        # tells ``drive_bead`` to skip its own posting for this
        # session).
        bd_client.call(
            "bd",
            args=[
                "comments",
                "add",
                brief.bead_id,
                "-f",
                str(comment_path),
                "--actor",
                brief.hat,
            ],
            cwd=project_cwd,
        )

        # example-cleanup: clean up the MBA-owned worker at the success
        # terminal state. The subprocess is already reaped via
        # ``communicate``, so this is normally a no-op (the OS
        # already has the PID); the gate still runs so the
        # contract is honoured end-to-end. Production dispatchers
        # that wrap a long-lived OpenCode / Claude session
        # (Start-Process -PassThru) supply a non-default
        # ``is_alive`` that surfaces the live worker PID and the
        # cleanup helper kills it at this terminal state.
        self._cleanup_terminal_state(worker_record)

        return SessionOutcome(
            working_text=working_text,
            result_text=result_text,
            next_state=next_state,
            verification=verification,
            changes=(
                f"working.md: {len(working_text.splitlines())} lines",
                f"result.md: {len(result_text.splitlines())} lines",
                f"comment.md: {len(comment_text.splitlines())} lines",
            ),
            capture=capture,
        )


# ---------------------------------------------------------------------------
# Decision-function builders
# ---------------------------------------------------------------------------


def authority_decision_fn_from_decisions_file(
    decisions_path: Path,
) -> Callable[[str], user_authority.AuthorityDecision]:
    """Build a :func:`user_authority.gate` ``decision_fn`` from a JSONL log.

    Read-only: the file is **never** written. The function reads every
    previously-recorded decision from the file; for the requested
    action, it returns the **first** decision whose actor is a
    recognisable human-origin actor (`human:...` prefix or a manually
    populated "human" origin) **and** whose ``approved`` field is
    ``True``. Refusals, non-human / AI / role / default actors,
    missing files, empty files, and records missing required fields
    each raise :class:`UserAuthorityRequired` (default-deny).

    Audit invariant: the function reads the file but never creates,
    appends, or modifies a row. The CLI / dispatcher surface that
    uses this helper cannot manufacture consent.
    """

    def _fn(action: str) -> user_authority.AuthorityDecision:
        if not decisions_path.exists():
            raise user_authority.UserAuthorityRequired(
                action=action,
                reason=(
                    f"decision file {decisions_path} is missing; the "
                    f"dispatcher refuses without a pre-existing "
                    f"recorded approval for {action!r}"
                ),
            )
        rows = user_authority.load_decision_log(decisions_path)
        if not rows:
            raise user_authority.UserAuthorityRequired(
                action=action,
                reason=(
                    f"decision file {decisions_path} is empty; the "
                    f"dispatcher refuses without at least one recorded "
                    f"row"
                ),
            )
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_action = str(row.get("action", ""))
            if row_action != action:
                continue
            if not bool(row.get("approved", False)):
                continue
            actor = str(row.get("actor", "")).strip()
            if not actor:
                continue
            if not is_human_origin(actor):
                continue
            rationale = str(row.get("rationale", ""))
            recorded_at = str(row.get("recorded_at", ""))
            # Round 2 (example-017.1): the read-only decisions log
            # loader is a verified channel. The attestation lets
            # the gate accept the decision; without it a Doer
            # could forge ``AuthorityDecision.approved(...)``
            # from its own memory and bypass §11.
            return user_authority.AuthorityDecision(
                action=row_action,
                actor=actor,
                rationale=rationale,
                approved=True,
                recorded_at=recorded_at,
                attestation="verified:decisions_log_read_only",
            )
        raise user_authority.UserAuthorityRequired(
            action=action,
            reason=(
                f"no human-origin approved decision recorded for "
                f"{action!r} in {decisions_path}; the dispatcher "
                f"refuses to launch an external worker without an "
                f"explicit recorded approval (refusals and "
                f"non-human-origin rows are skipped)"
            ),
        )

    return _fn


# Public alias that makes the read-only contract explicit.
read_only_decision_fn_from_decisions_file = (
    authority_decision_fn_from_decisions_file
)


def _is_human_origin(actor: str) -> bool:
    """Deprecated alias — kept for backwards compatibility.

    New code should import :func:`mba_runtime.user_authority.is_human_origin`
    directly; the validation rule moved there in example-017.1 so the
    :func:`~mba_runtime.user_authority.gate` check can share the
    single source of truth.
    """

    return is_human_origin(actor)


def authority_decision_fn_allow_when(
    test_session_marker: str,
) -> Callable[[str], user_authority.AuthorityDecision]:
    """In-memory allow-everything decision function for tests.

    Tests that want the adapter to launch without writing a real
    decisions file use this shim. The ``test_session_marker`` flows
    into the recorded reason so an audit can trace which test
    authorised the dispatch. The actor uses the ``human:`` prefix
    and the resulting decision carries a verified attestation
    (``verified:test_allow_when``) so the gate's round-2 invariant
    accepts it; without the attestation the gate would refuse the
    approval as a forge.
    """

    def _fn(action: str) -> user_authority.AuthorityDecision:
        return user_authority.make_verified_decision(
            action=action,
            actor=f"human:test-session:{test_session_marker}",
            rationale=f"in-memory approval for test {test_session_marker}",
            channel="test_allow_when",
        )

    return _fn


def authority_decision_fn_refuse_when(
    test_session_marker: str,
) -> Callable[[str], user_authority.AuthorityDecision]:
    """In-memory refuse-everything decision function for tests.

    Mirrors a user who has reviewed the dispatcher and explicitly
    refused to authorise it. Used by the fail-closed tests. The
    actor uses the ``human:`` prefix so the §11 gate validates
    the format consistently with approvals (example-017.1).
    Refusals carry no attestation (no verification needed).
    """

    def _fn(action: str) -> user_authority.AuthorityDecision:
        return user_authority.AuthorityDecision.refused(
            action=action,
            actor=f"human:test-session:{test_session_marker}",
            rationale=f"in-memory refusal for test {test_session_marker}",
        )

    return _fn
