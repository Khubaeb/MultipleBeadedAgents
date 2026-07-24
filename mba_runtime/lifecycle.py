"""End-to-end runtime: ``drive_bead``.

Implements the Orchestrator-side shell that drives a single executable
Bead from ``bd ready`` through ``bd close``:

1. Read the Bead (``bd show --json``) and load the AI-resource record.
2. Pick a team configuration and route per the canonical pattern
   (a)/(b)/(c)/(d) using :mod:`mba_runtime.pattern_router`.
3. Create the §10 directory layout for every Doer and Auditor session
   via the Primitives' :func:`mba_primitives.records_layout.ensure_layout`.
4. Fill every worker's ``prompt.md`` from the Primitives'
   :func:`mba_primitives.assignment_contract.assignment_contract`.
5. Hand the Bead id and prompt path to the per-session
   :class:`SessionRunner`; the runner writes ``working.md`` +
   ``result.md`` and the runtime posts a brief role-attributed Bead
   comment.
6. Run the §8 adversarial loop via
   :func:`mba_runtime.convergence.iterate_until_converged`.
7. Verify the dependency graph (``bd dep list`` + ``bd dep cycles``)
   via :mod:`mba_runtime.graph`.
8. Pause for the §11 user-authority gate before any external action
   (e.g., ``bd dolt push``). The runtime refuses without a recorded
   decision.
9. Post a brief Orchestrator comment for the material coordination
   decision at closure, then close the Bead via ``bd close``.

The runtime is **session-runnable from any shell**; the actual AI
sessions it dispatches to live in *separate fresh sessions* with
opposing hats (per Charter §3). ``drive_bead`` is the audit-grade
orchestration shell that knows about the records the user can inspect.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .ai_resources import (
    AIResourceRecord,
    TeamConfig,
    team_config,
)  # noqa: F401
from . import bd_client
from .comments import (
    PostedComment,
    post_role_comment,
    render_orchestrator_comment_text,
)
from .auditor_reliability import (
    EvidenceConflict,
    downgrade_accept_on_conflicts,
)
from .workspace_safety import assert_no_destructive_bd_init, assert_path_inside
from .constants import (
    FINAL_DIR,
    MBA_WORK_DIR,
    ORCHESTRATOR_DIR,
    RESP_AUDITOR,
    RESP_DOER,
    VALIDATED_BD_VERSIONS,
)
from .convergence import (
    AuditorRunner,
    AuditorRoundContext,
    ConvergenceResult,
    DoerOutcome,
    DoerRunner,
    DoerRoundContext,
    ParsedAuditorProtocol,
    Verdict,
    VERDICT_ACCEPT,
    VERDICT_BLOCKED,
    VERDICT_FIND,
    VERDICTS,
    parse_auditor_protocol,
    iterate_until_converged,
)
from .graph import assert_wire_clean
from .pattern_router import (
    PatternError,
    SessionPlan,
    SessionSpec,
    route,
)


AuditConflictChecker = Callable[..., tuple[EvidenceConflict, ...]]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class LifecycleError(RuntimeError):
    """Raised when the runtime cannot complete a Bead."""


# ---------------------------------------------------------------------------
# Runner protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionOutcome:
    """What a runner returns per session.

    ``working_text`` is the body the runner wants to persist into
    ``working.md``; ``result_text`` is the body for ``result.md``;
    ``next_state`` is a short string the runtime writes into the
    role-attributed Bead comment so the audit trail summarises the
    worker's view.

    ``capture`` is the runtime-owned :class:`StreamCapture` record
    pointing at the worker's ``run.log`` / ``run.err`` files. The
    field is optional so stub runners in tests stay schema-stable.
    When set, the Orchestrator and the ``mba stream`` follower can
    use it to render bounded, redacted progress without sitting
    between the worker and its durable sink.
    """

    working_text: str
    result_text: str
    next_state: str
    verification: str = "n/a (stub runner)"
    changes: tuple[str, ...] = ()
    capture: object | None = None


# A session runner is invoked once per Bead session (or multiple times
# when the convergence loop cycles). The runtime produces the
# ``SessionBrief`` describing what the worker should do, then invokes
# the runner in a separate fresh session (per Charter §3 single-AI
# rule). For tests we inject a deterministic stub.
SessionRunner = Callable[["SessionBrief"], SessionOutcome]


@dataclass(frozen=True)
class SessionBrief:
    """Inputs given to a per-session runner."""

    bead_id: str
    session_dir: Path
    session_name: str
    responsibility: str
    hat: str
    ai_id: str
    ai_label: str
    pattern: str
    prompt_path: Path
    artefact_paths: dict[str, Path]
    round_index: int = 0
    prior_verdict: Verdict | None = None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DriveConfig:
    """Runtime configuration for a single ``drive_bead`` call."""

    cwd: Path
    bead_id: str
    pattern: str
    team: TeamConfig
    ai_resources: AIResourceRecord
    max_doer_auditor_rounds: int = 3
    doer_runner: SessionRunner | None = None
    auditor_runner: SessionRunner | None = None
    bd_binary: str = "bd"
    post_role_comments: bool = True
    # Default ``False``: the Orchestrator posts its own comment **only**
    # for material coordination decisions, results, exceptions,
    # recovery or handoff. Routine completion is **not** a justification
    # for an Orchestrator comment.
    post_orchestrator_comment: bool = False
    # Optional fail-closed guard for Auditor reliability. When supplied,
    # it receives (outcome, parsed verdict, brief) and returns known-fact
    # conflicts. Any conflict downgrades ACCEPT to FIND.
    auditor_conflict_checker: AuditConflictChecker | None = None


@dataclass(frozen=True)
class DriveResult:
    """Outcome of :func:`drive_bead`."""

    bead_id: str
    pattern: str
    plan: SessionPlan
    convergence: ConvergenceResult
    graph_state: GraphState
    doer_comments: tuple[PostedComment, ...]
    auditor_comments: tuple[PostedComment, ...]
    orchestrator_comment: PostedComment | None
    closed: bool
    final_state: str

    def as_dict(self) -> dict[str, object]:
        return {
            "bead_id": self.bead_id,
            "pattern": self.pattern,
            "convergence": {
                "converged": self.convergence.converged,
                "rounds": self.convergence.rounds,
                "final_verdict": self.convergence.final_verdict.verdict,
            },
            "closed": self.closed,
            "final_state": self.final_state,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_team_or_default(
    record: AIResourceRecord, *, team_name: str = "default"
) -> TeamConfig:
    """Convenience: pick the chosen team (default = ``"default"``)."""

    return team_config(record, team_name=team_name)


def load_orchestrator_dir(cwd: Path, bead_id: str) -> Path:
    return cwd / MBA_WORK_DIR / bead_id / ORCHESTRATOR_DIR


def load_final_dir(cwd: Path, bead_id: str) -> Path:
    return cwd / MBA_WORK_DIR / bead_id / FINAL_DIR


def record_bd_version(cwd: Path, bead_id: str, *, source: str) -> Path:
    """Append a ``bd version`` capture to the orchestrator's working log.

    The runtime never issues a ``bd`` write without this capture
    (Foundation AC #1 / #2). The capture is best-effort: a missing
    ``bd`` is reported with the failure reason.

    Two artefacts are updated:

    * ``.mba-work/<bead>/orchestrator/bd-version.log`` — append-only JSON
      capture of every call's ``stdout`` / ``stderr`` / ``returncode``
      (audit row).
    * ``.mba-work/<bead>/orchestrator/working.md`` — first line
      ``- **bd version:** ...`` so a reviewer can pair the run to its
      validated ``bd`` version at a glance (matches the Foundation
      rule: "record the value in ``.mba-work/<bead>/orchestrator/working.md``").
    """

    orch_dir = load_orchestrator_dir(cwd, bead_id)
    orch_dir.mkdir(parents=True, exist_ok=True)
    target = orch_dir / "bd-version.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    proc = bd_client.call("bd", args=["version"], cwd=cwd)
    payload = {
        "source": source,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    # Mirror into ``working.md`` so the Foundation rule (record the
    # captured ``bd`` version in the orchestrator's working log) holds
    # even when downstream tooling reads only the Markdown file.
    working_path = orch_dir / "working.md"
    record_line = f"- **bd version:** `{payload['stdout'] or '(no stdout)'}` (source={source})\n"
    if not working_path.exists():
        working_path.write_text(
            "# Orchestrator working\n\n" + record_line,
            encoding="utf-8",
        )
    else:
        body = working_path.read_text(encoding="utf-8")
        if "**bd version:**" in body:
            lines = [
                record_line if ln.startswith("- **bd version:**") else ln
                for ln in body.splitlines()
            ]
            working_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            with working_path.open("a", encoding="utf-8") as handle:
                handle.write("\n" + record_line)
    return target


def _bead_id_from_cwd(cwd: Path, bead_id: str) -> str:
    return bead_id.strip() or "bead"


def _create_layout(
    *, cwd: Path, bead_id: str, plan: SessionPlan
) -> dict[str, Path]:
    """Create the §10 layout for every session using the Primitives helper."""

    from mba_primitives.records_layout import ensure_layout

    session_names = [spec.session_name(bead_id) for spec in plan.all_sessions()]
    layout = ensure_layout(bead_id, session_names, base_dir=cwd)
    return layout["bead_dir"]  # type: ignore[return-value]


def _fill_prompts(
    *,
    cwd: Path,
    plan: SessionPlan,
    bead_id: str,
    bead_record: dict,
) -> dict[str, Path]:
    """Fill ``prompt.md`` for each session via the Primitives helper."""

    from mba_primitives.assignment_contract import assignment_contract

    prompt_paths: dict[str, Path] = {}
    for spec in plan.all_sessions():
        session_name = spec.session_name(bead_id)
        path = assignment_contract(
            role=spec.hat,
            bead=bead_id,
            # Use the general MBA lifecycle stage name (example-014.1).
            # The runtime is the §8 "Verify" stage (every executable
            # Bead drives the same convergence loop). Do not label it
            # with the project's internal build-order name.
            stage="Verify",
            session_purpose=(
                f"{spec.responsibility} session (#{spec.session_index + 1} of "
                f"{_count_sessions(plan, spec.responsibility)}) for pattern "
                f"{spec.pattern!r}"
            ),
            task=(
                "complete your assigned responsibility on this Bead; "
                "respect the Authority and limits in this prompt; end "
                "with one brief role-attributed Bead comment that is "
                "sufficient for normal human review."
            ),
            read="\n".join(
                [
                    f"`bd show {bead_id} --json`",
                    f"`bd show {bead_id}`",
                    (
                        bead_record.get("description") or bead_record.get("notes")
                        or "(see `.mba-work/<bead>/<session>/prompt.md`)"
                    ),
                ]
            ),
            produce=(
                "a concise Bead comment as the normal human record; "
                "write result.md / working.md / generated files only "
                "when needed for machine verdicts, bulky evidence, or "
                "deliverables; if blocked for a human decision, post the "
                "handoff comment, add the `human` label, set status "
                "`blocked`, and assign `Human`."
            ),
            acceptance=(
                "the Bead comment is a useful structured Markdown summary "
                "(normally 4-16 non-blank lines) that states the result, "
                "material change or finding, verification status, next "
                "state when useful, and details; a details path appears "
                "only when a separate bulky file exists."
            ),
            authority_and_limits=(
                "**Allowed write root (mandatory):** the **entire "
                "project tree** rooted at ``<project_root>``. Workers "
                "*can* modify project source and content; the records "
                "directory ``<project_root>/.mba-work/<bead>/<session>/`` "
                "is available for prompts, machine transcripts, bulky "
                "evidence, and generated artefacts, **not** a mandatory "
                "human status surface. **Outside the project "
                "tree is forbidden** — any write to a path outside the "
                "declared root is an unauthorised filesystem side effect; "
                "the runtime enforces confinement **before** launch "
                "(the dispatcher refuses to spawn unless a recorded "
                "**human-origin** §11 decision permits the "
                "``external_dispatch_unconfinement`` action, or the "
                "configured host dispatcher proves it enforces the "
                "repository boundary). The runtime does **not** "
                "monitor for out-of-root writes mid-run (that "
                "detection is racy and not portable). "
                "**Beads permissions:** may read Beads and post your own "
                "useful structured comment on the assigned Bead. You may also perform "
                "the narrow human-handoff write when blocked: add `human`, "
                "set status `blocked`, and assign `Human`. Do not change "
                "hierarchy, dependencies, closure, or Dolt unless the "
                "assignment explicitly delegates that write. **Worker "
                "safety:** never run `bd init --reinit-local` or other "
                "destructive Beads init flags unless the Orchestrator gave "
                "you an approved disposable root and you first checked it "
                "has no ancestor `.beads`. source Git, "
                "`bd dolt push/pull`, deployment, external messages, "
                "destructive actions, and reusable workflow changes each "
                "require the Orchestrator to record a §11 user-authority "
                "decision before invoking. The pre-launch gate is "
                "non-negotiable; if you cannot complete your work inside "
                "the allowed write root, raise and let the Orchestrator "
                "iterate."
            ),
            responsibility=spec.responsibility,
            session_name=session_name,
            base_dir=cwd,
        )
        prompt_paths[session_name] = path
    return prompt_paths


def _count_sessions(plan: SessionPlan, responsibility: str) -> int:
    return len(plan.sessions_for(responsibility))


def _ensure_session_runner(
    runner: SessionRunner | None, *, role: str
) -> SessionRunner:
    if runner is not None:
        return runner

    def _default(brief: SessionBrief) -> SessionOutcome:
        return SessionOutcome(
            working_text=(
                f"# {role} working - {brief.session_name}\n\n"
                f"Role: {brief.responsibility}\n"
                f"AI: {brief.ai_id} ({brief.ai_label})\n"
                f"Pattern: {brief.pattern}\n"
                f"Round: {brief.round_index}\n\n"
                "Detailed working not supplied because no runner was "
                "registered; the test or production hook is expected to "
                "provide one."
            ),
            result_text=(
                f"# {role} result - {brief.session_name}\n\n"
                f"Pattern: {brief.pattern}\n"
                f"Round: {brief.round_index}\n\n"
                f"No deliverable recorded (default runner)."
            ),
            next_state="await auditor review",
        )

    return _default


def _resolve_artefact_paths(session_dir: Path) -> dict[str, Path]:
    return {
        "prompt": session_dir / "prompt.md",
        "working": session_dir / "working.md",
        "result": session_dir / "result.md",
        "comment": session_dir / "comment.md",
    }


def _write_session_artifacts(
    session_dir: Path, outcome: SessionOutcome
) -> dict[str, Path]:
    paths = _resolve_artefact_paths(session_dir)
    paths["working"].parent.mkdir(parents=True, exist_ok=True)
    paths["working"].write_text(outcome.working_text, encoding="utf-8")
    paths["result"].write_text(outcome.result_text, encoding="utf-8")
    return paths


def _read_bead_record(bead_id: str, *, cwd: Path, bd_binary: str) -> dict:
    proc = bd_client.call(
        bd_binary, args=["show", bead_id, "--json"], cwd=cwd
    )
    if proc.returncode != 0:
        raise LifecycleError(
            f"`{bd_binary} show {bead_id} --json` exited "
            f"{proc.returncode}: stderr={proc.stderr.strip()!r}"
        )
    try:
        payload = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise LifecycleError(
            f"`{bd_binary} show {bead_id} --json` returned non-JSON: {exc}"
        ) from exc
    if not isinstance(payload, list) or not payload:
        raise LifecycleError(
            f"`{bd_binary} show {bead_id} --json` returned an empty payload"
        )
    for record in payload:
        if isinstance(record, dict) and record.get("id") == bead_id:
            return dict(record)
    raise LifecycleError(
        f"`{bd_binary} show {bead_id} --json` returned "
        f"{len(payload)} record(s); none matched id={bead_id!r}"
    )


def _bd_version_gate(cwd: Path, *, bead_id: str, bd_binary: str) -> str:
    """Return the recorded ``bd`` version or refuse to proceed.

    The runtime refuses to issue any ``bd`` write when the recorded
    version is not in the validated set. The capture happens **before**
    the first write (Foundation AC #1 / #2): every capture is appended
    to ``.mba-work/<bead>/orchestrator/bd-version.log`` and the latest
    line is mirrored into ``working.md`` so the audit row ties the
    run to the validated version.
    """

    proc = bd_client.call(bd_binary, args=["version"], cwd=cwd)
    raw = (proc.stdout or "").strip()
    if proc.returncode != 0:
        raise LifecycleError(
            f"`{bd_binary} version` exited {proc.returncode}; "
            f"the runtime refuses to issue writes without a working "
            f"`bd` binary"
        )
    import re

    match = re.search(r"\b(\d+\.\d+\.\d+)\b", raw)
    if not match:
        raise LifecycleError(
            f"`{bd_binary} version` output did not contain a semver "
            f"token; raw={raw!r}"
        )
    version = match.group(1)
    if version not in VALIDATED_BD_VERSIONS:
        raise LifecycleError(
            f"`bd` version {version!r} is not in the validated set "
            f"{sorted(VALIDATED_BD_VERSIONS)}; per capabilities.md "
            f"Version policy the runtime refuses to proceed."
        )

    # Record the capture **before** any ``bd`` write is issued. The
    # append is best-effort (a missing working.md is created); a write
    # failure here is fatal — without the record we cannot satisfy
    # Foundation AC #1 / #2.
    record_bd_version(
        cwd,
        bead_id,
        source="lifecycle._bd_version_gate",
    )
    return version


def _runner_for_responsibility(config: DriveConfig, responsibility: str) -> SessionRunner | None:
    """Return the runner instance configured for a responsibility.

    The runtime exposes a single ``doer_runner`` and ``auditor_runner``
    field on :class:`DriveConfig`. Per-role dynamic dispatch is out
    of scope for this turn; the helper exists so
    :func:`_post_comments_for_sessions` can introspect
    ``posts_own_comment`` without growing the call-site signature.
    """

    if responsibility == RESP_DOER:
        return config.doer_runner
    if responsibility == RESP_AUDITOR:
        return config.auditor_runner
    return None


def _post_comments_for_sessions(
    *,
    config: DriveConfig,
    plan: SessionPlan,
    cwd: Path,
    bead_id: str,
    session_outcomes: dict[str, tuple[SessionSpec, SessionOutcome, Path]],
) -> dict[str, PostedComment]:
    """Post a brief role-attributed Bead comment per session.

    When the runner has ``posts_own_comment=True`` (e.g.
    :class:`mba_runtime.external_dispatch.ExternalProcessSessionRunner`),
    the runtime skips its own posting for that session — the
    adapter already wrote the comment via ``bd comments add``. This
    preserves the G2 invariant: **one role comment per worker
    invocation / convergence round**.
    """

    posted: dict[str, PostedComment] = {}
    if not config.post_role_comments:
        return posted

    for session_name, (spec, outcome, session_dir) in session_outcomes.items():
        # G2: a runner that has ``posts_own_comment`` already wrote the
        # comment via ``bd comments add`` (typically
        # ``ExternalProcessSessionRunner``); the runtime must not
        # post a second comment for it. The check is opt-in via the
        # attribute; default in-process runners post through the
        # runtime.
        runner = _runner_for_responsibility(config, spec.responsibility)
        if getattr(runner, "posts_own_comment", False):
            continue
        posted[session_name] = post_role_comment(
            bead_id=bead_id,
            session_dir=session_dir,
            organisational_role=spec.hat,
            result_line=(
                f"{spec.responsibility} session {spec.session_index + 1}: "
                f"{outcome.next_state}"
            ),
            changes=[
                f"working lines: {len(outcome.working_text.splitlines())}",
                f"result lines: {len(outcome.result_text.splitlines())}",
            ],
            verification=outcome.verification,
            next_state=outcome.next_state,
            cwd=cwd,
            bd_binary=config.bd_binary,
        )
    return posted


def _close_bead(
    *, bead_id: str, cwd: Path, bd_binary: str, reason: str
) -> bool:
    """Close the Bead via ``bd close -r <reason>``.

    The Orchestrator owns lifecycle / graph / status / closure / Dolt
    coordination; this helper is the only closure surface the runtime
    exposes.
    """

    proc = bd_client.call(
        bd_binary, args=["close", bead_id, "--reason", reason], cwd=cwd
    )
    return proc.returncode == 0


# ---------------------------------------------------------------------------
# Drive a Bead
# ---------------------------------------------------------------------------


def drive_bead(config: DriveConfig) -> DriveResult:
    """Drive a single executable Bead end-to-end.

    Returns a :class:`DriveResult`. Raises :class:`LifecycleError` /
    :class:`UserAuthorityRequired` / :class:`PatternError` on
    non-recoverable problems.

    Dispatch discipline (audit F7 fix): **Doer and Auditor sessions
    are invoked only through the §8 convergence loop**. There is no
    pre-convergence pass; each round invokes every required session
    exactly once. Role-attributed Bead comments are posted **after
    convergence**, using the round's final outcomes — so a
    round-1 revision is reflected in the posted comment's ``- **next:**``
    line, not in a stale pre-convergence artefact.
    """

    bead_id = _bead_id_from_cwd(config.cwd, config.bead_id)
    bd_version = _bd_version_gate(
        config.cwd, bead_id=bead_id, bd_binary=config.bd_binary
    )
    bead_record = _read_bead_record(bead_id, cwd=config.cwd, bd_binary=config.bd_binary)

    # The team config carries the pattern; the runtime must agree.
    if config.team.pattern != config.pattern:
        raise LifecycleError(
            f"requested pattern {config.pattern!r} does not match the "
            f"team config pattern {config.team.pattern!r}; pick a team "
            f"whose pattern matches the requested one"
        )

    record = config.ai_resources
    plan = route(record, bead_id=bead_id, team=config.team)

    if not plan.doer_sessions or not plan.auditor_sessions:
        raise LifecycleError(
            f"plan must include at least one Doer and one Auditor session; "
            f"got doer={len(plan.doer_sessions)} auditor={len(plan.auditor_sessions)}"
        )

    bead_dir = _create_layout(cwd=config.cwd, bead_id=bead_id, plan=plan)
    prompt_paths = _fill_prompts(
        cwd=config.cwd, plan=plan, bead_id=bead_id, bead_record=bead_record
    )

    # Per-session outcome bookkeeping: last round wins for posting.
    last_doer_outcomes: dict[str, SessionOutcome] = {}
    last_auditor_outcomes: dict[str, SessionOutcome] = {}

    doer_runner = _ensure_session_runner(config.doer_runner, role=RESP_DOER)
    auditor_runner = _ensure_session_runner(config.auditor_runner, role=RESP_AUDITOR)

    def _doer_brief(
        spec: SessionSpec, round_index: int, prior_verdict: Verdict | None
    ) -> SessionBrief:
        session_name = spec.session_name(bead_id)
        session_dir = bead_dir / session_name
        return SessionBrief(
            bead_id=bead_id,
            session_dir=session_dir,
            session_name=session_name,
            responsibility=RESP_DOER,
            hat=spec.hat,
            ai_id=spec.ai_id,
            ai_label=spec.ai_label,
            pattern=spec.pattern,
            prompt_path=prompt_paths[session_name],
            artefact_paths=_resolve_artefact_paths(session_dir),
            round_index=round_index,
            prior_verdict=prior_verdict,
        )

    def _auditor_brief(spec: SessionSpec, round_index: int) -> SessionBrief:
        session_name = spec.session_name(bead_id)
        session_dir = bead_dir / session_name
        return SessionBrief(
            bead_id=bead_id,
            session_dir=session_dir,
            session_name=session_name,
            responsibility=RESP_AUDITOR,
            hat=spec.hat,
            ai_id=spec.ai_id,
            ai_label=spec.ai_label,
            pattern=spec.pattern,
            prompt_path=prompt_paths[session_name],
            artefact_paths=_resolve_artefact_paths(session_dir),
            round_index=round_index,
        )

    def doer_round_runner(ctx: DoerRoundContext) -> DoerOutcome:
        """Doer round: run **every** planned Doer session exactly once.

        For pattern (c), the post-step combines each session's
        findings into the primary Doer's ``combined-working.md`` so the
        Auditor reviews the merged artefact.
        """
        round_artefact_paths: dict[str, Path] = {}
        round_outcomes: dict[str, tuple[SessionSpec, SessionOutcome, Path]] = {}
        for spec in plan.doer_sessions:
            brief = _doer_brief(spec, ctx.round_index, ctx.prior_verdict)
            outcome = doer_runner(brief)
            session_name = spec.session_name(bead_id)
            session_dir = bead_dir / session_name
            artefact_paths = _write_session_artifacts(session_dir, outcome)
            last_doer_outcomes[session_name] = outcome
            round_artefact_paths.update(artefact_paths)
            round_outcomes[session_name] = (spec, outcome, session_dir)

        # Pattern (c): combine this round's Doer findings.
        if plan.pattern == "c" and len(plan.doer_sessions) > 1:
            combined = _combine_doer_findings(
                bead_dir=bead_dir,
                bead_id=bead_id,
                plan=plan,
                session_outcomes=round_outcomes,
            )
            round_artefact_paths["combined_working"] = combined

        return DoerOutcome(
            artefact_paths=round_artefact_paths,
            note=(
                f"round {ctx.round_index}: {len(plan.doer_sessions)} "
                f"Doer session(s) ran"
            ),
        )

    def auditor_round_runner(ctx: AuditorRoundContext) -> Verdict:
        """Auditor round: run every planned Auditor session exactly once.

        Every Auditor's verdict influences convergence (Charter §8 +
        example-014.1 audit). The combined round verdict is decided by
        priority:

        * any ``BLOCKED`` → round is ``BLOCKED`` (all such reasons are
          merged into the §8 transcript);
        * else any ``FIND`` → round is ``FIND`` (each finding's evidence
          is preserved);
        * ``ACCEPT`` only when **every** Auditor returned ``ACCEPT``.

        A pattern-(c) Auditor roster is the typical case where this
        matters; the single-Auditor case reduces to the existing
        behaviour.
        """
        per_session_verdicts: list[tuple[str, Verdict]] = []
        for spec in plan.auditor_sessions:
            brief = _auditor_brief(spec, ctx.round_index)
            outcome = auditor_runner(brief)
            session_name = spec.session_name(bead_id)
            session_dir = bead_dir / session_name
            _write_session_artifacts(session_dir, outcome)
            last_auditor_outcomes[session_name] = outcome
            verdict = _outcome_to_verdict(outcome, ctx.round_index)
            if config.auditor_conflict_checker is not None:
                conflicts = config.auditor_conflict_checker(outcome, verdict, brief)
                verdict = downgrade_accept_on_conflicts(verdict, tuple(conflicts))
            per_session_verdicts.append(
                (session_name, verdict)
            )

        if not per_session_verdicts:
            raise LifecycleError("no Auditor session outcomes recorded")
        return _combine_auditor_verdicts(per_session_verdicts, ctx.round_index)

    # Convergence loop (§8). The Doer round runner is the *only* place
    # Doer sessions are invoked; the Auditor round runner is the *only*
    # place Auditor sessions are invoked. Audit F7.
    primary_doer_session = plan.doer_sessions[0]
    primary_doer_session_dir = bead_dir / primary_doer_session.session_name(bead_id)
    primary_auditor_session = plan.auditor_sessions[0]
    primary_auditor_session_dir = bead_dir / primary_auditor_session.session_name(bead_id)
    convergence_transcript_path = (
        load_final_dir(config.cwd, bead_id) / "convergence.md"
    )
    convergence = iterate_until_converged(
        bead_id=bead_id,
        doer_session_dir=primary_doer_session_dir,
        auditor_session_dir=primary_auditor_session_dir,
        artefact_paths=_resolve_artefact_paths(primary_doer_session_dir),
        doer_runner=doer_round_runner,
        auditor_runner=auditor_round_runner,
        max_rounds=config.max_doer_auditor_rounds,
        transcript_path=convergence_transcript_path,
    )

    # Post role-attributed Bead comments (Doer + Auditor) using the
    # *converged* round's outcomes.
    session_outcomes: dict[str, tuple[SessionSpec, SessionOutcome, Path]] = {}
    for spec in plan.doer_sessions:
        session_name = spec.session_name(bead_id)
        outcome = last_doer_outcomes.get(session_name)
        if outcome is not None:
            session_outcomes[session_name] = (
                spec,
                outcome,
                bead_dir / session_name,
            )
    for spec in plan.auditor_sessions:
        session_name = spec.session_name(bead_id)
        outcome = last_auditor_outcomes.get(session_name)
        if outcome is not None:
            session_outcomes[session_name] = (
                spec,
                outcome,
                bead_dir / session_name,
            )

    posted_comments = _post_comments_for_sessions(
        config=config,
        plan=plan,
        cwd=config.cwd,
        bead_id=bead_id,
        session_outcomes=session_outcomes,
    )

    # Dependency-graph verification (Capability record). The runtime
    # always passes `for_issue_id=bead_id` so the verification works
    # against real bd 1.0.4 (the bare `bd dep list` exits 1 without
    # an issue id; see `mba_runtime.graph`).
    graph_state = assert_wire_clean(
        cwd=config.cwd,
        bd_binary=config.bd_binary,
        for_issue_id=bead_id,
        require_listing=True,
    )

    # Orchestrator comment for material coordination decision.
    #
    # Per Charter §10 + capabilities.md:74 the Orchestrator posts its
    # own Bead comment **only** for material coordination decisions,
    # results, exceptions, recovery or handoff — never to repackage or
    # relabel a worker's result, and never merely to announce work
    # started. ``drive_bead`` therefore does **not** auto-emit an
    # Orchestrator comment; the helper
    # ``mba_runtime.comments.render_orchestrator_comment_text`` is
    # exposed so an Orchestrator can compose an exceptional comment
    # via an explicit call. The closure path closes the Bead via
    # ``bd close`` without an Orchestrator comment of its own.
    orch_comment: PostedComment | None = None
    if config.post_orchestrator_comment:
        orch_comment_text = render_orchestrator_comment_text(
            result_line=(
                f"Runtime converged on {bead_id!r} in pattern {plan.pattern!r} "
                f"after {convergence.rounds} round(s); final verdict "
                f"= {convergence.final_verdict.verdict}"
            ),
            material_change=(
                f"pattern {plan.pattern!r}: {len(plan.doer_sessions)} Doer "
                f"sessions, {len(plan.auditor_sessions)} Auditor sessions; "
                f"doer_auditor_rounds={convergence.rounds}"
            ),
            verification=(
                "graph verified (`bd dep list` + `bd dep cycles`); "
                "bd_version=" + bd_version
            ),
            next_state=(
                "ready for `bd close`"
                if convergence.converged
                else "blocked: non-convergence recorded"
            ),
            path_tail=(
                "/".join(
                    (MBA_WORK_DIR, bead_id, FINAL_DIR, "convergence.md")
                )
            ),
        )
        orch_dir = load_orchestrator_dir(config.cwd, bead_id)
        orch_dir.mkdir(parents=True, exist_ok=True)
        orch_comment_path = orch_dir / "orchestrator-comment.md"
        orch_comment_path.write_text(orch_comment_text, encoding="utf-8")
        proc = bd_client.call(
            config.bd_binary,
            args=[
                "comments",
                "add",
                bead_id,
                "-f",
                str(orch_comment_path),
                "--actor",
                "Orchestrator",
            ],
            cwd=config.cwd,
        )
        from .comments import PostedComment  # local import for clarity

        orch_comment = PostedComment(
            bead_id=bead_id,
            actor="Orchestrator",
            text_path=orch_comment_path,
            path_tail=f"{MBA_WORK_DIR}/{bead_id}/{ORCHESTRATOR_DIR}/orchestrator-comment.md",
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

    # Closure (§11: source Git / Dolt / external actions are gated; the
    # Orchestrator's `bd close` invocation is internal and does not
    # require user authority — it is the lifecycle-coordination call
    # the Orchestrator owns).
    final_state = (
        "closed" if convergence.converged
        else "blocked: non-convergence recorded"
    )
    closed = False
    if convergence.converged:
        closed = _close_bead(
            bead_id=bead_id,
            cwd=config.cwd,
            bd_binary=config.bd_binary,
            reason=(
                f"Runtime converged in pattern {plan.pattern!r} after "
                f"{convergence.rounds} round(s); final verdict = "
                f"{convergence.final_verdict.verdict}"
            ),
        )
        final_state = "closed" if closed else "close_failed"

    return DriveResult(
        bead_id=bead_id,
        pattern=plan.pattern,
        plan=plan,
        convergence=convergence,
        graph_state=graph_state,
        doer_comments=tuple(
            posted_comments.get(spec.session_name(bead_id))
            for spec in plan.doer_sessions
            if posted_comments.get(spec.session_name(bead_id)) is not None
        ),
        auditor_comments=tuple(
            posted_comments.get(spec.session_name(bead_id))
            for spec in plan.auditor_sessions
            if posted_comments.get(spec.session_name(bead_id)) is not None
        ),
        orchestrator_comment=orch_comment,
        closed=closed,
        final_state=final_state,
    )


def _outcome_to_verdict(outcome: SessionOutcome, round_index: int) -> Verdict:
    """Map an Auditor :class:`SessionOutcome` to a :class:`Verdict`.

    Bridge function. Two inputs carry the verdict signal:

    1. ``outcome.next_state`` — the side-channel from the dispatcher's
       ``_verdict.txt`` (or the default runner's description string).
    2. ``outcome.result_text`` — the Auditor's ``result.md`` body,
       which may carry a structured ``VERDICT`` / ``RESOLUTION`` /
       ``EVIDENCE`` block (example-015.1).

    The structured ``VERDICT`` (when present and a member of
    ``VERDICTS``) takes precedence over ``next_state``; a mismatch
    between the two is raised. A side-channel ``ACCEPT`` with no
    ``EVIDENCE`` block in ``result_text`` is downgraded to ``FIND``
    (Charter §8: the runtime cannot backfill the Auditor's evidence;
    a missing-evidence ``ACCEPT`` is not convergence).
    """

    label = (outcome.next_state or "").strip().upper()
    parsed: ParsedAuditorProtocol = parse_auditor_protocol(
        outcome.result_text or ""
    )

    if parsed.verdict is not None and parsed.verdict not in VERDICTS:
        raise LifecycleError(
            f"Auditor result_text declared VERDICT={parsed.verdict!r} "
            f"which is not one of {VERDICTS}"
        )

    # Resolve the effective verdict label. The structured VERDICT
    # wins when present; otherwise the side-channel next_state.
    if parsed.verdict is not None:
        if label and label not in ("", parsed.verdict):
            raise LifecycleError(
                f"Auditor outcome.next_state={outcome.next_state!r} "
                f"conflicts with structured VERDICT={parsed.verdict!r} "
                "in result_text; the runtime refuses an inconsistent "
                "verdict signal (example-015.1)"
            )
        effective_label: str = parsed.verdict
    else:
        effective_label = label

    if effective_label == VERDICT_ACCEPT:
        evidence = parsed.evidence
        if not evidence:
            # Charter §8 + example-015.1: ACCEPT without Auditor evidence
            # degrades to unresolved/FIND, not convergence. The runtime
            # must never synthesise ACCEPT evidence.
            return Verdict.find(
                reasons=(
                    f"Auditor ACCEPT on round {round_index} without "
                    f"EVIDENCE block; degraded to FIND (Charter §8; "
                    f"example-015.1 evidence-required convergence). "
                    f"next_state={outcome.next_state!r}",
                ),
                evidence=(),
            )
        reasons = parsed.reasons or (
            f"Auditor accepted the Doer artefact (round {round_index})",
        )
        return Verdict.accept(
            reasons=reasons,
            evidence=evidence,
            resolution=parsed.resolution,
        )

    if effective_label == VERDICT_BLOCKED:
        return Verdict.blocked(
            reasons=(f"Auditor blocked (round {round_index})",)
        )

    if effective_label in (VERDICT_FIND, ""):
        evidence_for_find = parsed.evidence
        if not evidence_for_find and outcome.result_text:
            evidence_for_find = (outcome.result_text,)
        return Verdict.find(
            reasons=(
                f"Auditor finding (round {round_index}): "
                f"{outcome.next_state!r}",
            ),
            evidence=evidence_for_find,
        )

    # Anything else is an unexpected verdict string; raise to surface.
    raise LifecycleError(
        f"Auditor outcome.next_state={outcome.next_state!r} and "
        f"structured VERDICT={parsed.verdict!r} are not a recognised "
        f"verdict; expected one of {VERDICTS}"
    )


def _combine_auditor_verdicts(
    per_session_verdicts: list[tuple[str, Verdict]],
    round_index: int,
) -> Verdict:
    """Combine every Auditor verdict for one round into the round verdict.

    Priority (Charter §8 + example-014.1 audit): any BLOCKED ⇒ round
    BLOCKED; else any FIND ⇒ round FIND (collective evidence); ACCEPT
    only when **every** Auditor returned ACCEPT. A pattern-(c) roster
    is the typical case where this matters; the single-Auditor case
    reduces to the existing behaviour.

    example-015.1 evidence-required: when **all** Auditors returned
    ``ACCEPT``, the combined ``ACCEPT`` carries every Auditor's
    evidence so the loop sees the full chain. The bridge
    :func:`_outcome_to_verdict` is the canonical place that
    downgrades a per-Auditor ``ACCEPT`` without ``EVIDENCE`` to
    ``FIND``; this helper trusts that the per-Auditor verdicts are
    already evidence-bearing when they reach here as ``ACCEPT``.
    """

    blocked = [
        (name, verdict)
        for name, verdict in per_session_verdicts
        if verdict.verdict == VERDICT_BLOCKED
    ]
    if blocked:
        reasons: tuple[str, ...] = tuple(
            f"Auditor {name!r} BLOCKED (round {round_index}): {r}"
            for name, verdict in blocked
            for r in verdict.reasons
        )
        evidence: tuple[str, ...] = tuple(
            item
            for _, verdict in blocked
            for item in verdict.evidence
        )
        if evidence:
            return Verdict(
                verdict=VERDICT_BLOCKED,
                reasons=reasons,
                evidence=evidence,
            )
        return Verdict.blocked(reasons=reasons)

    findings = [
        (name, verdict)
        for name, verdict in per_session_verdicts
        if verdict.verdict == VERDICT_FIND
    ]
    if findings:
        reasons = tuple(
            f"Auditor {name!r} FIND (round {round_index}): {r}"
            for name, verdict in findings
            for r in verdict.reasons
        )
        evidence = tuple(
            item
            for _, verdict in findings
            for item in verdict.evidence
        )
        return Verdict(
            verdict=VERDICT_FIND,
            reasons=reasons,
            evidence=evidence,
        )

    accepts = [
        (name, verdict)
        for name, verdict in per_session_verdicts
        if verdict.verdict == VERDICT_ACCEPT
    ]
    if len(accepts) != len(per_session_verdicts):
        # Defensive: an unrecognised verdict would have raised in
        # ``_outcome_to_verdict``; this branch documents the invariant
        # so a future verdict addition fails loudly here.
        raise LifecycleError(
            f"round {round_index}: Auditor verdicts in unexpected state: "
            f"{[v.verdict for _, v in per_session_verdicts]}"
        )
    reasons = tuple(
        f"Auditor {name!r} ACCEPT (round {round_index}): {r}"
        for name, verdict in accepts
        for r in verdict.reasons
    )
    combined_evidence: tuple[str, ...] = tuple(
        item
        for name, verdict in accepts
        for item in verdict.evidence
    )
    # Carry the most informative resolution forward: any
    # ``no-change-proof`` is preserved; otherwise the first
    # ``changed`` or None is used.
    combined_resolution: str | None = None
    for _, verdict in accepts:
        if verdict.resolution == "no-change-proof":
            combined_resolution = "no-change-proof"
            break
        if verdict.resolution == "changed":
            combined_resolution = "changed"
    return Verdict.accept(
        reasons=reasons,
        evidence=combined_evidence,
        resolution=combined_resolution,
    )


# ---------------------------------------------------------------------------
# Verdict bridge (production Auditor → convergence loop)
# ---------------------------------------------------------------------------


def _combine_doer_findings(
    *,
    bead_dir: Path,
    bead_id: str,
    plan: SessionPlan,
    session_outcomes: dict[str, tuple[SessionSpec, SessionOutcome, Path]],
) -> Path:
    """Combine Doer sessions' findings into a single working.md.

    Used by pattern (c) so the Auditor reviews the merged artefact.
    """

    doer_sessions = plan.doer_sessions
    if not doer_sessions:
        raise LifecycleError("no Doer sessions to combine")

    primary = doer_sessions[0]
    primary_dir = bead_dir / primary.session_name(bead_id)
    combined_path = primary_dir / "combined-working.md"

    body = [
        "<!-- auto-generated by mba_runtime.lifecycle._combine_doer_findings -->",
        f"# Combined Doer findings for {bead_id} (pattern c)",
        "",
        f"- pattern: c (several sessions for one responsibility on one artefact)",
        f"- doer_sessions: {len(doer_sessions)}",
        "",
    ]
    for spec in doer_sessions:
        name = spec.session_name(bead_id)
        spec_outcome = session_outcomes.get(name)
        outcome = spec_outcome[1] if spec_outcome is not None else None
        body.append(f"## {name} ({spec.hat}, AI={spec.ai_id})")
        body.append("")
        if outcome is not None:
            body.append("### Working")
            body.append(outcome.working_text.rstrip())
            body.append("")
            body.append("### Result")
            body.append(outcome.result_text.rstrip())
            body.append("")
        else:
            body.append("(no outcome captured)")
            body.append("")

    combined_path.parent.mkdir(parents=True, exist_ok=True)
    combined_path.write_text("\n".join(body).rstrip() + "\n", encoding="utf-8")
    return combined_path
