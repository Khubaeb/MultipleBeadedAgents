"""CLI dispatcher for the Runtime.

Subcommands:

* ``drive-bead``         — ``drive_bead(...)`` end-to-end.
* ``first-contact``      — show activation + AI-resource setup status.
* ``route``              — show the SessionPlan for a team + pattern.
* ``gate``               — exercise the §11 user-authority gate.
* ``comment render``     — preview a role-attributed comment Markdown.
* ``graph verify``       — run ``bd dep list`` + ``bd dep cycles``.
* ``convergence check``  — render an example §8 transcript shape.

The CLI emits JSON for every subcommand so a shell-driven audit can
parse the result deterministically. Errors are still exceptions; the
    dispatcher catches top-level ``LifecycleError`` /
    ``UserAuthorityRequired`` and prints the message verbatim so an
    external audit can capture refusals without a custom decoder.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from mba_version import __version__

from . import (
    ai_resources,
    comments,
    convergence,
    external_dispatch,
    graph,
    lifecycle,
    pattern_router,
    session_recovery,
    stream_capture,
    user_authority,
)


def _emit(obj: dict[str, Any]) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def cmd_drive_bead(args: argparse.Namespace) -> int:
    """Drive a Bead end-to-end via the Runtime.

    ``--dispatch-worker <script>`` swaps the default in-process
    runners for :class:`mba_runtime.external_dispatch.ExternalProcessSessionRunner`,
    which spawns the supplied script as a real subprocess per
    session. Pass the path to a Python script (any executable that
    consumes the runtime's CLI flags) and the runtime invokes it
    Doer-times-Auditor-times to drive the artefact chain.

    The adapter is the only path through the **public** CLI to a
    real session dispatch. Without ``--dispatch-worker``, the
    default in-process runners emit a descriptive
    ``next_state`` that the Auditor bridge refuses to map onto
    a verdict, so the loop ends in ``exit 99`` — that refusal is
    the AC for the surface that does NOT use the adapter.
    """

    cwd = Path(args.cwd).resolve()
    record = ai_resources.load_ai_resource_record(cwd)
    team = ai_resources.team_config(record, team_name=args.team)

    doer_runner = None
    auditor_runner = None
    if args.dispatch_worker:
        import sys
        from mba_runtime.external_dispatch import (
            read_only_decision_fn_from_decisions_file,
            AuthorityContext,
        )

        script = Path(args.dispatch_worker).resolve()
        if not script.exists():
            raise external_dispatch.ExternalDispatchError(
                f"--dispatch-worker script {script} does not exist"
            )

        # Authorisation: the CLI **only reads** a pre-existing JSONL
        # decisions file the user maintains. The CLI never writes a
        # row, never chooses an actor, and never manufactures
        # consent. Without the file (or absent any human-origin row
        # for ``external_dispatch_unconfinement``) the dispatcher
        # refuses before launch.
        decision_fn = None
        decisions_path = None
        if args.dispatch_decision_file:
            decisions_path = Path(args.dispatch_decision_file).resolve()
            decision_fn = read_only_decision_fn_from_decisions_file(
                decisions_path
            )

        authority = external_dispatch.AuthorityContext(
            decision_fn=decision_fn,
            project_cwd=cwd,
            trusted_confinement=False,  # trusted_confinement is an
                                       # internal capability for a concrete
                                       # dispatcher that proves confinement;
                                       # the generic CLI never sets it.
            decisions_path=decisions_path,
        )

        runner = external_dispatch.ExternalProcessSessionRunner(
            dispatch_argv=(sys.executable, str(script)),
            authority=authority,
            timeout_seconds=float(args.dispatch_timeout_seconds),
        )
        doer_runner = runner
        auditor_runner = runner

    config = lifecycle.DriveConfig(
        cwd=cwd,
        bead_id=args.bead_id,
        pattern=args.pattern,
        team=team,
        ai_resources=record,
        bd_binary=args.bd,
        doer_runner=doer_runner,
        auditor_runner=auditor_runner,
        post_role_comments=not args.skip_role_comments,
        # No routine Orchestrator comment (Charter §10 + example-010.1):
        # posting is **opt-in** via ``--orchestrator-comment``. The
        # default matches ``DriveConfig.post_orchestrator_comment=False``
        # so ``drive-bead`` never emits a routine Orchestrator comment.
        post_orchestrator_comment=bool(args.orchestrator_comment),
    )
    result = lifecycle.drive_bead(config)
    _emit(result.as_dict())
    return 0 if result.closed else 2


def cmd_first_contact(args: argparse.Namespace) -> int:
    """Read-only cold-start preflight for an MBA-aware repo.

    The active AI session becomes the Orchestrator only because the
    repo instructions / MBA skill were loaded; MBA is not a resident
    daemon. This command makes that first step observable and gives
    the Orchestrator deterministic questions to ask when the private
    AI-resource record is absent or incomplete.

    Pass ``--apply-setup`` to make the runtime itself create or
    update the blocked ``MBA setup`` Bead and post the setup
    question comment on the Orchestrator's behalf. The flag is the
    deterministic, runtime-assisted path the Orchestrator prefers
    over hand-written setup writes; it stays **opt-in** so a routine
    ``first-contact`` check is read-only.
    """

    cwd = Path(args.cwd).resolve()
    bd_binary = args.bd
    resource_state = ai_resources.resource_preflight(
        cwd, team_name=args.team
    )
    output: dict[str, Any] = {
        "activation": {
            "orchestrator_mode": True,
            "reason": (
                "MBA instructions are present; the active AI session "
                "must act as the thin Orchestrator before assigning "
                "Doer/Auditor worker sessions."
            ),
            "not_a_daemon": True,
        },
        "resource_preflight": resource_state.to_dict(),
        "next": (
            "create or update the blocked `MBA setup` task, assign it to "
            "Human, post its comment, then stop before executable Beads "
            "or worker launches"
            if not resource_state.ok
            else "read the Bead, write worker prompt.md files, and launch "
            "separate Doer/Auditor sessions"
        ),
    }
    if not resource_state.ok:
        output["recommended_setup_bead"] = ai_resources.setup_bead_guidance(
            resource_state
        )

    if args.apply_setup:
        output["setup_handoff"] = ai_resources.apply_setup_handoff(
            resource_state, bd_binary=bd_binary, cwd=cwd
        ).to_dict()

    _emit(output)
    return 0 if resource_state.ok else 4


def cmd_resources(args: argparse.Namespace) -> int:
    """Show the ordered suitable-resource fallback for a responsibility.

    Mirrors ``route`` for the recovery layer: it makes the ordered
    fallback (the team's configured AI first, then every other
    catalogue resource capable of the responsibility) observable
    end-to-end. The runtime falls back down this list on
    provider-unavailability, recording every substitution — never a
    silent model/effort downgrade. See
    :mod:`mba_runtime.session_recovery`.
    """

    cwd = Path(args.cwd).resolve()
    record = ai_resources.load_ai_resource_record(cwd)
    team = ai_resources.team_config(record, team_name=args.team)
    responsibility = (
        lifecycle.RESP_DOER
        if args.responsibility == "doer"
        else lifecycle.RESP_AUDITOR
    )
    order = session_recovery.suitable_resources(record, team, responsibility)
    _emit(
        {
            "team": team.name,
            "responsibility": responsibility,
            "suitable_order": list(order),
            "bounds": {
                "max_scheduled_retries": session_recovery.MAX_SCHEDULED_RETRIES,
                "max_probes_when_reset_unknown": (
                    session_recovery.MAX_PROBES_WHEN_RESET_UNKNOWN
                ),
                "probe_interval_max_seconds": (
                    session_recovery.PROBE_INTERVAL_MAX_SECONDS
                ),
            },
        }
    )
    return 0


def cmd_route(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve()
    record = ai_resources.load_ai_resource_record(cwd)
    team = ai_resources.team_config(record, team_name=args.team)
    plan = pattern_router.route(record, bead_id=args.bead_id, team=team)
    _emit(
        {
            "bead_id": plan.bead_id,
            "pattern": plan.pattern,
            "doer_sessions": [
                {
                    "session_name": spec.session_name(plan.bead_id),
                    "hat": spec.hat,
                    "ai_id": spec.ai_id,
                    "session_index": spec.session_index,
                }
                for spec in plan.doer_sessions
            ],
            "auditor_sessions": [
                {
                    "session_name": spec.session_name(plan.bead_id),
                    "hat": spec.hat,
                    "ai_id": spec.ai_id,
                    "session_index": spec.session_index,
                }
                for spec in plan.auditor_sessions
            ],
        }
    )
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    """Exercise the §11 user-authority gate.

    Authority rule: the CLI **must not** invent or self-record user
    approval. ``actor: cli`` is NOT user authority (correction F6 +
    authority invariant). To approve a §11 action, supply a JSON-Lines
    decisions file (``--decision-file <path>``) that already records
    a decision made by a human-origin actor (e.g. ``"human:user"``).

    The CLI reads the file, surfaces the decision the user supplied,
    and refuses to invent one. Pass ``--decision-file`` and
    ``--require-existing`` to forbid auto-approval.
    """

    decisions_path = Path(args.decision_file).resolve()
    if not decisions_path.exists():
        if args.refuse_if_missing:
            raise user_authority.UserAuthorityRequired(
                action=args.action,
                reason=(
                    f"decision file {decisions_path} is missing; the CLI "
                    f"refuses to invent a user-authority decision for "
                    f"action {args.action!r}"
                ),
            )
        # The CLI falls back to record-and-REFUSE behaviour when
        # no file is present: it writes a refusal so an audit trail
        # exists for the attempt.
        decisions_path.parent.mkdir(parents=True, exist_ok=True)
        refusal = user_authority.AuthorityDecision.refused(
            action=args.action,
            actor="cli",
            rationale=(
                f"no decision file at {decisions_path}; CLI refused to "
                f"self-approve §11 action {args.action!r}"
            ),
        )
        user_authority.record_decision(decisions_path, refusal)
        _emit(refusal.to_dict())
        return 3

    # Read previously recorded decisions and pick the first decision
    # for ``args.action`` recorded by a non-CLI, human-origin actor.
    rows = user_authority.load_decision_log(decisions_path)
    matched = None
    for row in rows:
        if row.get("action") != args.action:
            continue
        actor = row.get("actor") or ""
        if actor == "cli" or actor.startswith("decision_fn_default"):
            continue
        matched = row
        break
    if matched is None:
        raise user_authority.UserAuthorityRequired(
            action=args.action,
            reason=(
                f"decision file {decisions_path} contains no human-origin "
                f"decision for action {args.action!r}"
            ),
        )
    decision = user_authority.AuthorityDecision(
        action=str(matched["action"]),
        actor=str(matched["actor"]),
        rationale=str(matched.get("rationale", "")),
        approved=bool(matched.get("approved", False)),
        recorded_at=str(matched.get("recorded_at", "")),
    )
    _emit(decision.to_dict())
    return 0 if decision.approved else 3


def cmd_comment_render(args: argparse.Namespace) -> int:
    text = comments.render_role_comment_text(
        organisational_role=args.role,
        result_line=args.result,
        changes=list(args.change or []),
        verification=args.verification,
        next_state=args.next,
        path_tail=args.path_tail,
    )
    print(text)
    return 0


def cmd_graph_verify(args: argparse.Namespace) -> int:
    """Verify the dependency graph.

    Audit F5: the previous implementation passed no ``for_issue_id``;
    on bd 1.0.4 the bare ``bd dep list`` exits 1 even on a clean
    graph, so the CLI raised ``GraphVerificationError``. Pass
    ``--for-issue-id`` so the call becomes ``bd dep list <id>`` and
    the verification is usable against the real binary.
    """

    cwd = Path(args.cwd).resolve()
    state = graph.assert_wire_clean(
        cwd=cwd,
        bd_binary=args.bd,
        for_issue_id=args.for_issue_id,
        require_listing=bool(args.for_issue_id),
    )
    _emit(
        {
            "list_returncode": state.list_returncode,
            "cycles_returncode": state.cycles_returncode,
            "cycles_present": state.cycles_present,
        }
    )
    return 0


def cmd_convergence_check(args: argparse.Namespace) -> int:
    """Render a stub §8 convergence run against the supplied bead id.

    Audit F4: the previous implementation referenced
    ``result.bead_id``, but :class:`ConvergenceResult` carries no
    such field. The CLI now echoes the requested ``bead_id`` and
    runs a small synthetic transcript through :func:`iterate_until_converged`.

    example-015.1 evidence-required: the stub transcript is a single
    round (no prior FIND) so the post-FIND check is bypassed and
    the Auditor's ``ACCEPT`` only needs to carry non-empty
    evidence. The doer also writes a real artefact on round 0 so
    the auditor's evidence chain has a referent on disk.
    """

    transcript = [
        convergence.Verdict.accept(
            reasons=("recheck accepted",),
            evidence=("stub: §8 recheck on a synthetic transcript",),
        ),
    ]
    tmp_dir = Path(".mba-work") / args.bead_id / "convergence-stub"
    artefact_path = tmp_dir / "working.md"
    artefact_path.parent.mkdir(parents=True, exist_ok=True)
    artefact_path.write_text(
        f"# stub artefact for {args.bead_id}\n", encoding="utf-8"
    )

    def _doer(ctx: convergence.DoerRoundContext) -> convergence.DoerOutcome:
        artefact_path.write_text(
            f"# stub artefact for {args.bead_id} (round {ctx.round_index + 1})\n",
            encoding="utf-8",
        )
        return convergence.DoerOutcome(
            artefact_paths={"working": artefact_path},
            note="artefact written",
        )

    result = convergence.iterate_until_converged(
        bead_id=args.bead_id,
        doer_session_dir=tmp_dir,
        auditor_session_dir=tmp_dir,
        artefact_paths={"working": artefact_path},
        doer_runner=_doer,
        auditor_runner=lambda ctx: transcript[ctx.round_index],
        max_rounds=3,
    )
    _emit(
        {
            "bead_id": args.bead_id,
            "converged": result.converged,
            "rounds": result.rounds,
            "final_verdict": result.final_verdict.verdict,
        }
    )
    return 0 if result.converged else 4


def cmd_stream(args: argparse.Namespace) -> int:
    """Read-only follower for one MBA-owned worker capture.

    The captured files live at
    ``.mba-work/<bead-id>/<session-name>/run.log`` and
    ``.mba-work/<bead-id>/<session-name>/run.err`` (or any
    ``--stdout-file`` / ``--stderr-file`` override). The follower
    is **read-only**: it never sits between the worker and its
    durable sink, so a viewer disconnect cannot interrupt capture.
    A restarted viewer simply replays complete records and,
    optionally, follows new growth.

    Default mode renders a bounded, redacted view of every
    complete NDJSON record (step_start / step_finish / tool_use /
    text / reasoning / error). Tool inputs / outputs and reasoning
    text are **truncated** to ``--max-field-chars`` /
    ``--max-tool-payload-chars`` to keep the view bounded; pass
    ``--raw`` to print the verbatim JSON line (diagnostic only —
    never a Bead-comment transport).

    The follower buffers a partial final line so a crash never
    produces a fake completed event. ``--follow`` polls
    ``--poll-interval-ms`` for new bytes and renders each complete
    record as it appears; ``--max-events`` caps how many records
    the follower emits before exiting.
    """

    cwd = Path(args.cwd).resolve()
    if not args.bead_id or not args.bead_id.strip():
        raise stream_capture.StreamCaptureError(
            "stream requires a non-empty --bead-id"
        )
    if not args.session_name or not args.session_name.strip():
        raise stream_capture.StreamCaptureError(
            "stream requires a non-empty --session-name"
        )
    if (
        args.stream != "stdout"
        and args.stream != "stderr"
    ):
        raise stream_capture.StreamCaptureError(
            f"--stream must be one of 'stdout' or 'stderr'; got {args.stream!r}"
        )
    if args.max_field_chars is not None and args.max_field_chars < 0:
        raise stream_capture.StreamCaptureError(
            "--max-field-chars must be a non-negative integer"
        )
    if (
        args.max_tool_payload_chars is not None
        and args.max_tool_payload_chars < 0
    ):
        raise stream_capture.StreamCaptureError(
            "--max-tool-payload-chars must be a non-negative integer"
        )
    if args.poll_interval_ms is not None and args.poll_interval_ms <= 0:
        raise stream_capture.StreamCaptureError(
            "--poll-interval-ms must be a positive integer"
        )

    session_dir = (cwd / ".mba-work" / args.bead_id / args.session_name)
    if not args.stdout_file:
        stdout_file = session_dir / "run.log"
    else:
        stdout_file = Path(args.stdout_file)
    if not args.stderr_file:
        stderr_file = session_dir / "run.err"
    else:
        stderr_file = Path(args.stderr_file)

    # Bead-scoped path guard: refuse friendly names and ref hashes.
    target = stdout_file if args.stream == "stdout" else stderr_file
    target = stream_capture.validate_bead_scoped_path(
        cwd, target, bead_id=args.bead_id
    )

    config = stream_capture.FollowConfig(
        follow=bool(args.follow),
        poll_interval_seconds=(
            float(args.poll_interval_ms) / 1000.0
            if args.poll_interval_ms is not None
            else 0.25
        ),
        max_events=args.max_events,
        max_bytes=args.max_bytes,
        max_field_chars=(
            args.max_field_chars
            if args.max_field_chars is not None
            else stream_capture.DEFAULT_MAX_FIELD_CHARS
        ),
        max_tool_payload_chars=(
            args.max_tool_payload_chars
            if args.max_tool_payload_chars is not None
            else stream_capture.DEFAULT_MAX_TOOL_PAYLOAD_CHARS
        ),
        raw=bool(args.raw),
    )
    for line in stream_capture.follow_stream(target, config=config):
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mba-runtime")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser(
        "first-contact",
        help="read-only activation + AI-resource setup preflight",
    )
    p.add_argument("--cwd", default=".")
    p.add_argument("--team", default="default")
    p.add_argument("--bd", default="bd")
    p.add_argument(
        "--apply-setup",
        action="store_true",
        help=(
            "when AI resources are missing, also create or update "
            "the blocked `MBA setup` Bead (assignee=Human, "
            "labels=mba,setup,human) and post the deterministic "
            "setup-question comment with explicit `--actor "
            "Orchestrator`. Off by default so a routine check "
            "stays read-only; the runtime returns the existing "
            "exit code 4 either way so the Orchestrator still "
            "stops before executable Beads or worker launches."
        ),
    )
    p.set_defaults(func=cmd_first_contact)

    p = sub.add_parser(
        "drive-bead", help="drive a single executable Bead end-to-end"
    )
    p.add_argument("--bead-id", required=True)
    p.add_argument("--cwd", default=".")
    p.add_argument(
        "--pattern",
        choices=["a", "b", "c", "d"],
        required=True,
        help="canonical §4 pattern",
    )
    p.add_argument("--team", default="default")
    p.add_argument("--bd", default="bd")
    p.add_argument(
        "--dispatch-worker",
        default=None,
        help=(
            "path to a Python script the runtime spawns as a real "
            "external subprocess per Doer/Auditor session. Without "
            "this flag the CLI uses the default in-process runners "
            "(which the Auditor bridge will refuse with an explicit "
            "loud error on a missing verdict). Pass this flag to "
            "exercise the ExternalProcessSessionRunner path."
        ),
    )
    p.add_argument(
        "--dispatch-timeout-seconds",
        default="60",
        help="per-session subprocess timeout (default 60 s)",
    )
    p.add_argument(
        "--dispatch-decision-file",
        default=None,
        help=(
            "path to a JSONL decisions file containing a recorded, "
            "human-origin approval for the "
            "``external_dispatch_unconfinement`` action. When "
            "supplied, the dispatcher READS the file (it does NOT "
            "modify it). Persistent / project-scoped across "
            "rounds. Missing / empty / refused / non-human-origin "
            "rows cause the dispatcher to refuse before launch."
        ),
    )
    p.add_argument("--skip-role-comments", action="store_true")
    p.add_argument(
        "--orchestrator-comment",
        action="store_true",
        help=(
            "opt in to a single Orchestrator comment at closure. Off by "
            "default: the runtime posts no routine Orchestrator comment "
            "(Charter §10; only material coordination moments warrant one)."
        ),
    )
    p.set_defaults(func=cmd_drive_bead)

    p = sub.add_parser(
        "resources",
        help="show the ordered suitable-resource fallback for a responsibility",
    )
    p.add_argument("--responsibility", choices=["doer", "auditor"], required=True)
    p.add_argument("--team", default="default")
    p.add_argument("--cwd", default=".")
    p.set_defaults(func=cmd_resources)

    p = sub.add_parser("route", help="show the SessionPlan for a team + pattern")
    p.add_argument("--bead-id", required=True)
    p.add_argument("--team", default="default")
    p.add_argument("--cwd", default=".")
    p.set_defaults(func=cmd_route)

    p = sub.add_parser("gate", help="exercise the §11 user-authority gate")
    p.add_argument(
        "action",
        choices=sorted(
            [
                "source_git_commit",
                "source_git_push",
                "bd_dolt_push",
                "bd_dolt_pull",
                "deployment",
                "external_message",
                "credentials_or_spending",
                "destructive_change",
                "reusable_workflow_change",
            ]
        ),
    )
    p.add_argument(
        "--decision-file",
        required=True,
        help=(
            "path to a JSON-Lines decisions file. The CLI refuses to "
            "approve an action without a pre-recorded human-origin "
            "decision in this file."
        ),
    )
    p.add_argument(
        "--refuse-if-missing",
        action="store_true",
        help=(
            "fail fast when the decision file is absent instead of "
            "recording a CLI refusal."
        ),
    )
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser(
        "comment",
        help="render or post a role-attributed Bead comment",
    )
    sub_cm = p.add_subparsers(dest="cm_cmd", required=True)
    pm = sub_cm.add_parser("render", help="render a structured comment Markdown")
    pm.add_argument("--role", required=True)
    pm.add_argument("--result", required=True)
    pm.add_argument("--change", action="append", default=[])
    pm.add_argument("--verification", required=True)
    pm.add_argument("--next", required=True)
    pm.add_argument("--path-tail", required=True)
    pm.set_defaults(func=cmd_comment_render)

    p = sub.add_parser("graph", help="verify the dependency graph")
    sub_gr = p.add_subparsers(dest="gr_cmd", required=True)
    pg = sub_gr.add_parser(
        "verify", help="run `bd dep list` + `bd dep cycles`"
    )
    pg.add_argument("--cwd", default=".")
    pg.add_argument("--bd", default="bd")
    pg.add_argument(
        "--for-issue-id",
        default=None,
        help=(
            "issue id forwarded to `bd dep list`. Required on bd 1.0.4 "
            "because the bare subcommand exits 1 without an id."
        ),
    )
    pg.set_defaults(func=cmd_graph_verify)

    p = sub.add_parser(
        "convergence",
        help="iterate a convergence-shaped loop on a stub transcript",
    )
    sub_co = p.add_subparsers(dest="co_cmd", required=True)
    pc = sub_co.add_parser("check", help="render a stub convergence run")
    pc.add_argument("--bead-id", required=True)
    pc.set_defaults(func=cmd_convergence_check)

    p = sub.add_parser(
        "stream",
        help=(
            "read-only follower for an MBA-owned worker capture "
            "(.mba-work/<bead-id>/<session-name>/run.log). "
            "Renders a bounded, redacted NDJSON summary; --follow "
            "polls the file for new complete records; --raw emits "
            "the verbatim JSON line (diagnostic only)."
        ),
    )
    p.add_argument("--bead-id", required=True)
    p.add_argument("--session-name", required=True)
    p.add_argument("--cwd", default=".")
    p.add_argument(
        "--stream",
        choices=("stdout", "stderr"),
        default="stdout",
        help=(
            "which captured stream to follow. 'stdout' reads "
            "run.log (the worker's NDJSON / output stream); "
            "'stderr' reads run.err (worker diagnostics)."
        ),
    )
    p.add_argument(
        "--stdout-file",
        default=None,
        help=(
            "override path to the worker's stdout capture file "
            "(default: .mba-work/<bead-id>/<session-name>/run.log). "
            "The path must still resolve under "
            ".mba-work/<bead-id>/ for the Bead-scoped guard."
        ),
    )
    p.add_argument(
        "--stderr-file",
        default=None,
        help=(
            "override path to the worker's stderr capture file "
            "(default: .mba-work/<bead-id>/<session-name>/run.err). "
            "The path must still resolve under "
            ".mba-work/<bead-id>/ for the Bead-scoped guard."
        ),
    )
    p.add_argument(
        "--follow",
        action="store_true",
        help=(
            "after replaying complete records, poll the capture "
            "file for new bytes and render each complete record "
            "as it appears. Exits only on --max-events, EOF after "
            "the file is removed, or a fatal error."
        ),
    )
    p.add_argument(
        "--raw",
        action="store_true",
        help=(
            "emit the verbatim NDJSON line instead of the bounded "
            "summary. Diagnostic only; the raw bytes can include "
            "tool inputs/outputs and reasoning text and must never "
            "be pasted into a Bead comment."
        ),
    )
    p.add_argument(
        "--max-bytes",
        type=int,
        default=None,
        help=(
            "soft cap on how many bytes of the capture to render. "
            "When the file exceeds the cap the follower reads the "
            "tail and flags 'truncated_tail=true' on the header."
        ),
    )
    p.add_argument(
        "--max-events",
        type=int,
        default=None,
        help=(
            "stop after emitting this many complete records "
            "(useful with --follow for a bounded UI scan)."
        ),
    )
    p.add_argument(
        "--max-field-chars",
        type=int,
        default=None,
        help=(
            "cap on the bounded text / reasoning / error field "
            "length. Tool payloads use --max-tool-payload-chars. "
            "Default: 240."
        ),
    )
    p.add_argument(
        "--max-tool-payload-chars",
        type=int,
        default=None,
        help=(
            "cap on tool input / output / error length in the "
            "bounded summary. Default: 480."
        ),
    )
    p.add_argument(
        "--poll-interval-ms",
        type=int,
        default=None,
        help=(
            "polling interval for --follow (default 250 ms). "
            "OpenCode may flush stdout late; the captured file is "
            "durable even when the live stream is silent."
        ),
    )
    p.set_defaults(func=cmd_stream)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (
        ai_resources.AIResourceError,
        comments.CommentFormatError,
        graph.GraphVerificationError,
        lifecycle.LifecycleError,
        pattern_router.PatternError,
        session_recovery.SessionRecoveryError,
        stream_capture.BeadScopedPathError,
        stream_capture.StreamCaptureError,
        user_authority.UserAuthorityRequired,
    ) as exc:
        print(f"mba-runtime: error: {exc}", file=sys.stderr)
        return 99


if __name__ == "__main__":
    raise SystemExit(main())
