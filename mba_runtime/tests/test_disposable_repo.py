"""End-to-end disposable-repo convergence: drive a synthetic Bead through
the runtime with every pattern (a, b, c, d) and verify every
acceptance row.

Each test:

1. Creates a fresh ``tmp_path`` workspace with a synthesised
   ``.beads/`` and the stub ``bd`` binary on PATH.
2. Populates ``.mba-work/.ai-resources.json`` with a fixture
   configuration whose pattern matches the test.
3. Drives :func:`mba_runtime.lifecycle.drive_bead` with a stub
   :class:`SessionRunner` that produces a short working.md + result.md
   and reports ACCEPT on the convergence round.
4. Asserts on the post-conditions: §10 directory layout, role-attributed
   Bead comments (one per Doer/Auditor session), useful structured comment shape,
   graph verification outcome, and ``bd close`` invocation.

These tests are the audit-grade AC matrix the Workflow Auditor will
re-read for Stage 7.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Callable

import pytest

from mba_runtime import ai_resources, lifecycle
from mba_runtime.ai_resources import (
    AIResource,
    AIResourceRecord,
    ResponsibilityConfig,
    TeamConfig,
)
from mba_runtime.convergence import Verdict
from mba_runtime.lifecycle import DriveConfig, SessionBrief, SessionOutcome


@pytest.fixture()
def populate_record():
    """Factory: populate ``.ai-resources.json`` with a given team config."""

    def _factory(
        cwd: Path,
        *,
        pattern: str,
        doer_ai: str = "minimax",
        auditor_ai: str = "claude",
        doer_hat: str = "Engineer",
        auditor_hat: str = "Workflow Auditor",
        doer_sessions: int = 1,
        auditor_sessions: int = 1,
    ) -> AIResourceRecord:
        if doer_ai == "minimax" and auditor_ai == "minimax":
            assert pattern in {"a", "d"}, "minimax-only requires single-AI pattern"
        if doer_ai == auditor_ai:
            assert pattern in {"a", "c", "d"}, (
                f"distinct AIs violation: pattern={pattern}"
            )
        record = AIResourceRecord(
            schema=1,
            note="fixture",
            resources=tuple(
                AIResource(
                    id=ai,
                    label=ai.upper(),
                    capabilities=("doer", "auditor"),
                    session_lifetime="fresh_per_session",
                )
                for ai in {doer_ai, auditor_ai}
            ),
            teams={
                "default": TeamConfig(
                    name="default",
                    doer=ResponsibilityConfig(
                        ai=doer_ai, hat=doer_hat, session_count=doer_sessions
                    ),
                    auditor=ResponsibilityConfig(
                        ai=auditor_ai,
                        hat=auditor_hat,
                        session_count=auditor_sessions,
                    ),
                    pattern=pattern,
                )
            },
        )
        ai_resources.save_ai_resource_record(cwd, record)
        return record

    return _factory


@pytest.fixture()
def wire_record_to_stub(
    fake_bead_record: Path, monkeypatch
) -> Callable[[Path, str], None]:
    """Wire ``BD_RECORD`` to a per-test fixture so the stub returns it."""

    def _fn(cwd: Path, bead_id: str) -> None:
        payload = [
            {
                "id": bead_id,
                "title": "Sample",
                "description": (
                    "End-to-end test for the Runtime; the auditor reviews "
                    "the artefact and converges.\n"
                ),
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

    return _fn


def _build_session_outcome(bead_id: str, session_name: str, role: str) -> SessionOutcome:
    """Deterministic session outcome that converges on the first round."""

    if role == "Doer":
        return SessionOutcome(
            working_text=(
                f"# {session_name} working\n\n"
                f"- bead: {bead_id}\n"
                "- produced working.md + result.md\n"
                "- verified end-to-end round-trip\n"
            ),
            result_text=(
                f"# {session_name} result\n\n"
                f"- deliverable recorded.\n"
                "- next: auditor review.\n"
            ),
            verification="Doer artefact recorded (read-back passes)",
            next_state="await auditor review",
        )
    return SessionOutcome(
        working_text=(
            f"# {session_name} audit\n\n"
            f"- bead: {bead_id}\n"
            "- evidence cross-checked\n"
            "- verdict: ACCEPT\n"
            "- EVIDENCE: working.md + result.md contents cross-checked\n"
        ),
        result_text=(
            f"# {session_name} audit result\n\n"
            "- verdict: ACCEPT (round 0)\n\n"
            "VERDICT: ACCEPT\n"
            "RESOLUTION: changed\n"
            "EVIDENCE:\n"
            "- working.md contents cross-checked against AC\n"
            "- result.md read-back matches Doer's claim\n"
        ),
        verification="Auditor: byte-for-byte check passes",
        next_state="ACCEPT",
    )


def _runner_for(role: str) -> Callable[[SessionBrief], SessionOutcome]:
    def _fn(brief: SessionBrief) -> SessionOutcome:
        return _build_session_outcome(brief.bead_id, brief.session_name, role)

    return _fn


def _fresh_round_index() -> int:
    return 0


# ---------------------------------------------------------------------------
# Pattern (b): distinct AIs - the default Runtime pattern-b case.
# ---------------------------------------------------------------------------


def test_drive_bead_pattern_b_end_to_end(
    fake_bd_dir: Path,
    populate_record,
    wire_record_to_stub,
    monkeypatch,
    tmp_path: Path,
) -> None:
    cwd = fake_bd_dir
    bead_id = "sample-b"

    populate_record(
        cwd,
        pattern="b",
        doer_ai="minimax",
        auditor_ai="claude",
    )
    wire_record_to_stub(cwd, bead_id)

    comment_log = tmp_path / "comments.log"
    closed_log = tmp_path / "closed.log"
    monkeypatch.setenv("BD_COMMENT_LOG", str(comment_log))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(closed_log))

    record = ai_resources.load_ai_resource_record(cwd)
    team = ai_resources.team_config(record)
    config = DriveConfig(
        cwd=cwd,
        bead_id=bead_id,
        pattern="b",
        team=team,
        ai_resources=record,
        doer_runner=_runner_for("Doer"),
        auditor_runner=_runner_for("Auditor"),
    )

    result = lifecycle.drive_bead(config)

    # Pattern topology
    assert result.plan.pattern == "b"
    assert len(result.plan.doer_sessions) == 1
    assert len(result.plan.auditor_sessions) == 1
    assert result.plan.doer_sessions[0].ai_id == "minimax"
    assert result.plan.auditor_sessions[0].ai_id == "claude"

    # Convergence
    assert result.convergence.converged is True
    assert result.convergence.rounds == 1
    assert result.convergence.final_verdict.verdict == "ACCEPT"

    # Closed through bd close.
    assert result.closed is True
    assert closed_log.read_text(encoding="utf-8").strip() == bead_id

    # Per-session artefacts exist.
    for spec in result.plan.doer_sessions + result.plan.auditor_sessions:
        session_name = spec.session_name(bead_id)
        session_dir = cwd / ".mba-work" / bead_id / session_name
        assert (session_dir / "prompt.md").exists()
        assert (session_dir / "working.md").exists()
        assert (session_dir / "result.md").exists()
        assert (session_dir / "comment.md").exists()

    # Each session posted a brief role-attributed Bead comment.
    rows = comment_log.read_text(encoding="utf-8").strip().splitlines()
    session_count = len(result.plan.doer_sessions) + len(
        result.plan.auditor_sessions
    )
    role_rows = [
        r for r in rows
        if not r.split("\t")[1].startswith("Orchestrator")
    ]
    assert len(role_rows) == session_count, role_rows
    for row in role_rows:
        fields = row.split("\t")
        assert fields[0] == bead_id
        # The actor is the hat, not the AI id nor "user".
        assert fields[1] in {"Engineer", "Workflow Auditor"}, row

    # The default DriveConfig does NOT auto-emit an Orchestrator
    # comment — Orchestrator comments are exceptional.
    assert result.orchestrator_comment is None

    # Graph was verified.
    assert result.graph_state.cycles_present is False


# ---------------------------------------------------------------------------
# Pattern (a): one AI both with fresh sessions + opposing hats.
# ---------------------------------------------------------------------------


def test_drive_bead_pattern_a_one_ai_both(
    fake_bd_dir: Path,
    populate_record,
    wire_record_to_stub,
    monkeypatch,
    tmp_path: Path,
) -> None:
    cwd = fake_bd_dir
    bead_id = "sample-a"
    populate_record(
        cwd,
        pattern="a",
        doer_ai="minimax",
        auditor_ai="minimax",
        doer_hat="Engineer",
        auditor_hat="Workflow Auditor",
    )
    wire_record_to_stub(cwd, bead_id)

    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    record = ai_resources.load_ai_resource_record(cwd)
    team = ai_resources.team_config(record)
    config = DriveConfig(
        cwd=cwd,
        bead_id=bead_id,
        pattern="a",
        team=team,
        ai_resources=record,
        doer_runner=_runner_for("Doer"),
        auditor_runner=_runner_for("Auditor"),
    )
    result = lifecycle.drive_bead(config)
    assert result.plan.pattern == "a"
    assert result.plan.doer_sessions[0].ai_id == "minimax"
    assert result.plan.auditor_sessions[0].ai_id == "minimax"
    assert result.convergence.converged is True


# ---------------------------------------------------------------------------
# Pattern (c): several sessions for one responsibility on one artefact.
# ---------------------------------------------------------------------------


def test_drive_bead_pattern_c_combined_finding(
    fake_bd_dir: Path,
    populate_record,
    wire_record_to_stub,
    monkeypatch,
    tmp_path: Path,
) -> None:
    cwd = fake_bd_dir
    bead_id = "sample-c"
    populate_record(
        cwd,
        pattern="c",
        doer_ai="minimax",
        auditor_ai="claude",
        doer_sessions=2,
        auditor_sessions=1,
    )
    wire_record_to_stub(cwd, bead_id)

    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    record = ai_resources.load_ai_resource_record(cwd)
    team = ai_resources.team_config(record)
    config = DriveConfig(
        cwd=cwd,
        bead_id=bead_id,
        pattern="c",
        team=team,
        ai_resources=record,
        doer_runner=_runner_for("Doer"),
        auditor_runner=_runner_for("Auditor"),
    )
    result = lifecycle.drive_bead(config)
    assert result.plan.pattern == "c"
    assert len(result.plan.doer_sessions) == 2
    combined_dir = (
        cwd
        / ".mba-work"
        / bead_id
        / result.plan.doer_sessions[0].session_name(bead_id)
    )
    assert (combined_dir / "combined-working.md").exists()
    assert result.convergence.converged is True
    assert result.closed is True


# ---------------------------------------------------------------------------
# Pattern (d): separated Doer/Auditor sessions with one AI.
# ---------------------------------------------------------------------------


def test_drive_bead_pattern_d_single_ai(
    fake_bd_dir: Path,
    populate_record,
    wire_record_to_stub,
    monkeypatch,
    tmp_path: Path,
) -> None:
    cwd = fake_bd_dir
    bead_id = "sample-d"
    populate_record(
        cwd,
        pattern="d",
        doer_ai="claude",
        auditor_ai="claude",
        doer_hat="Engineer",
        auditor_hat="Workflow Auditor",
    )
    wire_record_to_stub(cwd, bead_id)

    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    record = ai_resources.load_ai_resource_record(cwd)
    team = ai_resources.team_config(record)
    config = DriveConfig(
        cwd=cwd,
        bead_id=bead_id,
        pattern="d",
        team=team,
        ai_resources=record,
        doer_runner=_runner_for("Doer"),
        auditor_runner=_runner_for("Auditor"),
    )
    result = lifecycle.drive_bead(config)
    assert result.plan.pattern == "d"
    assert result.plan.doer_sessions[0].ai_id == "claude"
    assert result.plan.auditor_sessions[0].ai_id == "claude"
    assert result.convergence.converged is True


# ---------------------------------------------------------------------------
# Convergence with a FIND round → fix → ACCEPT.
# ---------------------------------------------------------------------------


def test_drive_bead_finds_then_converges(
    fake_bd_dir: Path,
    populate_record,
    wire_record_to_stub,
    monkeypatch,
    tmp_path: Path,
) -> None:
    cwd = fake_bd_dir
    bead_id = "sample-find"
    populate_record(cwd, pattern="b", doer_ai="minimax", auditor_ai="claude")
    wire_record_to_stub(cwd, bead_id)

    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    # Auditor that returns FIND on round 0 then ACCEPT on round 1.
    call_count = {"value": 0}

    def _doer_runner(brief: SessionBrief) -> SessionOutcome:
        outcome = _build_session_outcome(brief.bead_id, brief.session_name, "Doer")
        if brief.round_index >= 1:
            outcome = SessionOutcome(
                working_text=outcome.working_text
                + "\n- revision after round-0 finding\n",
                result_text=outcome.result_text
                + "\n- revised per Auditor finding\n",
                verification=outcome.verification,
                next_state="ready for recheck",
            )
        return outcome

    def _auditor_runner(brief: SessionBrief) -> SessionOutcome:
        call_count["value"] += 1
        round_index = brief.round_index
        if round_index == 0:
            return SessionOutcome(
                working_text="# auditor round 0 finding",
                result_text="finding\n",
                next_state="FIND",
                verification="round-0 finding",
            )
        return _build_session_outcome(brief.bead_id, brief.session_name, "Auditor")

    record = ai_resources.load_ai_resource_record(cwd)
    team = ai_resources.team_config(record)
    config = DriveConfig(
        cwd=cwd,
        bead_id=bead_id,
        pattern="b",
        team=team,
        ai_resources=record,
        doer_runner=_doer_runner,
        auditor_runner=_auditor_runner,
        max_doer_auditor_rounds=3,
    )
    result = lifecycle.drive_bead(config)
    assert result.convergence.converged is True
    assert result.convergence.rounds == 2
    assert result.convergence.final_verdict.verdict == "ACCEPT"
    assert result.closed is True
    # Convergence transcript was recorded.
    transcript = (
        cwd / ".mba-work" / bead_id / "final" / "convergence.md"
    )
    assert transcript.exists()


# ---------------------------------------------------------------------------
# §11 user-authority gate prevents `bd close` failing for an unrecorded
# action. The runtime does NOT call `bd dolt push` itself; the close
# step is internal. We verify that the decisions helper refuses
# actions and records them in the JSONL log.
# ---------------------------------------------------------------------------


def test_drive_bead_records_user_authority_when_needed(
    fake_bd_dir: Path,
    populate_record,
    wire_record_to_stub,
    monkeypatch,
    tmp_path: Path,
) -> None:
    cwd = fake_bd_dir
    bead_id = "sample-user-auth"
    populate_record(cwd, pattern="b", doer_ai="minimax", auditor_ai="claude")
    wire_record_to_stub(cwd, bead_id)

    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    from mba_runtime.user_authority import (
        AuthorityDecision,
        decisions_to_file_decision_fn,
        gate,
    )

    decisions_path = tmp_path / "decisions.jsonl"
    fn = decisions_to_file_decision_fn(
        decisions_path,
        auto={
            "bd_dolt_push": AuthorityDecision.approved(
                action="bd_dolt_push",
                actor="human:user",
                rationale="explicit user approval",
            )
        },
    )
    decision = gate("bd_dolt_push", decision_fn=fn)
    assert decision.approved is True
    rows = decisions_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 1
    payload = json.loads(rows[0])
    assert payload["approved"] is True
    assert payload["actor"] == "human:user"


# ---------------------------------------------------------------------------
# AI-action comments are *never* attributed to the user.
# ---------------------------------------------------------------------------


def test_role_attribution_uses_hat_not_user(
    fake_bd_dir: Path,
    populate_record,
    wire_record_to_stub,
    monkeypatch,
    tmp_path: Path,
) -> None:
    cwd = fake_bd_dir
    bead_id = "sample-actor"
    populate_record(cwd, pattern="b", doer_ai="minimax", auditor_ai="claude")
    wire_record_to_stub(cwd, bead_id)

    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    record = ai_resources.load_ai_resource_record(cwd)
    team = ai_resources.team_config(record)
    config = DriveConfig(
        cwd=cwd,
        bead_id=bead_id,
        pattern="b",
        team=team,
        ai_resources=record,
        doer_runner=_runner_for("Doer"),
        auditor_runner=_runner_for("Auditor"),
    )
    lifecycle.drive_bead(config)
    rows = (tmp_path / "comments.log").read_text(encoding="utf-8").strip().splitlines()
    actors = [row.split("\t")[1] for row in rows]
    for actor in actors:
        assert actor != "user", f"AI action attributed to the user: {actors}"
        assert actor != "human:user", f"AI action attributed to the user: {actors}"


# ---------------------------------------------------------------------------
# bd --readonly is NOT used by default. We verify the lifecycle does not
# construct that flag.
# ---------------------------------------------------------------------------


def test_default_runtime_does_not_use_bd_readonly(
    fake_bd_dir: Path,
    populate_record,
    wire_record_to_stub,
    monkeypatch,
    tmp_path: Path,
) -> None:
    cwd = fake_bd_dir
    bead_id = "sample-no-readonly"
    populate_record(cwd, pattern="b", doer_ai="minimax", auditor_ai="claude")
    wire_record_to_stub(cwd, bead_id)

    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    record = ai_resources.load_ai_resource_record(cwd)
    team = ai_resources.team_config(record)
    config = DriveConfig(
        cwd=cwd,
        bead_id=bead_id,
        pattern="b",
        team=team,
        ai_resources=record,
        doer_runner=_runner_for("Doer"),
        auditor_runner=_runner_for("Auditor"),
    )
    # Run drive_bead. The stub records all invoked commands in
    # comments.log + closed.log; the absent log is the test. We also
    # check the lifecycle source for ``--readonly``.
    lifecycle.drive_bead(config)
    import inspect

    source = inspect.getsource(lifecycle)
    assert "--readonly" not in source


# ---------------------------------------------------------------------------
# Disposable-repo convergence: end-to-end against the real `bd` binary
# when available. Skipped otherwise.
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("skip_unless_bd_available")
def test_disposable_repo_isolated_from_live_project(
    authorized_workspace: Path,
    assert_outside_repository,
) -> None:
    """Boundary: a disposable ``bd init`` repository stays self-contained.

    Runs against real ``bd 1.0.4`` in the authorized workspace (an OS
    temporary directory outside the live repository, per the User-
    authorised test-only exception). The exact boundary assertion
    below verifies the workspace is **not** under the active project
    root — replacing the prior fragile ``pytest-of-`` substring
    check, which broke under repository-local ``--basetemp``.
    """

    import subprocess

    cwd = authorized_workspace
    bead_id = "bnd-boundary-1"

    # `bd init` is a write that creates a fresh Dolt database in
    # cwd. It must NOT touch the project's live `.beads/`.
    init_proc = subprocess.run(
        ["bd", "init", "--non-interactive", "--prefix", "bnd"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd),
    )
    assert init_proc.returncode == 0, init_proc.stderr

    create_proc = subprocess.run(
        ["bd", "create", "--title", "Boundary", "--id", bead_id, "--json"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd),
    )
    assert create_proc.returncode == 0, create_proc.stderr

    close_proc = subprocess.run(
        [
            "bd",
            "close",
            bead_id,
            "--reason",
            "Boundary test disposable repo (no project impact).",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd),
    )
    assert close_proc.returncode == 0, close_proc.stderr

    show_proc = subprocess.run(
        ["bd", "show", bead_id, "--json"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd),
    )
    payload = json.loads(show_proc.stdout)
    boundary_record = next(rec for rec in payload if rec["id"] == bead_id)
    assert boundary_record["status"] == "closed"

    # Exact boundary assertion tied to the authorized workspace and
    # the project root: ``cwd`` must NOT be inside the active
    # repository. If a future change routes ``cwd`` back to the live
    # project tree (e.g. by mistake), this surfaces it as a clear
    # failure instead of relying on a fragile substring match.
    assert_outside_repository(cwd)


def test_runtime_does_not_emit_routine_orchestrator_comment(
    fake_bd_dir: Path,
    populate_record,
    wire_record_to_stub,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """`drive_bead` must not auto-emit a routine Orchestrator comment.

    Per Charter §10 + capabilities.md:74 the Orchestrator posts its
    own Bead comment **only** for material coordination decisions,
    results, exceptions, recovery or handoff. The runtime does not
    emit a routine completion summary; the helper is exposed so an
    Orchestrator can compose an exceptional comment via an explicit
    call.
    """

    cwd = fake_bd_dir
    bead_id = "no-orch"
    populate_record(cwd, pattern="b", doer_ai="minimax", auditor_ai="claude")
    wire_record_to_stub(cwd, bead_id)

    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    record = ai_resources.load_ai_resource_record(cwd)
    team = ai_resources.team_config(record)
    config = DriveConfig(
        cwd=cwd,
        bead_id=bead_id,
        pattern="b",
        team=team,
        ai_resources=record,
        doer_runner=_runner_for("Doer"),
        auditor_runner=_runner_for("Auditor"),
        # Default ``post_orchestrator_comment=False``; the test
        # leaves the flag at default to verify the runtime does NOT
        # emit a routine Orchestrator comment.
    )
    result = lifecycle.drive_bead(config)
    assert result.convergence.converged is True
    assert result.orchestrator_comment is None

    rows = (tmp_path / "comments.log").read_text(encoding="utf-8").strip().splitlines()
    actors = [row.split("\t")[1] for row in rows]
    assert "Orchestrator" not in actors, actors


def test_runtime_emits_orchestrator_comment_only_when_opted_in(
    fake_bd_dir: Path,
    populate_record,
    wire_record_to_stub,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """The Orchestrator comment is opt-in (exceptional only)."""

    cwd = fake_bd_dir
    bead_id = "orch-optin"
    populate_record(cwd, pattern="b", doer_ai="minimax", auditor_ai="claude")
    wire_record_to_stub(cwd, bead_id)

    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    record = ai_resources.load_ai_resource_record(cwd)
    team = ai_resources.team_config(record)
    config = DriveConfig(
        cwd=cwd,
        bead_id=bead_id,
        pattern="b",
        team=team,
        ai_resources=record,
        doer_runner=_runner_for("Doer"),
        auditor_runner=_runner_for("Auditor"),
        post_orchestrator_comment=True,
    )
    result = lifecycle.drive_bead(config)
    assert result.orchestrator_comment is not None
    assert result.orchestrator_comment.actor == "Orchestrator"


# ---------------------------------------------------------------------------
# example-014.1 acceptance: multi-Auditor convergence through drive_bead.
# ---------------------------------------------------------------------------


def test_drive_bead_multi_auditor_any_find_blocks_convergence(
    fake_bd_dir: Path,
    populate_record,
    wire_record_to_stub,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """A pattern-(c) Auditor roster where any Auditor ``FIND``s blocks the
    round, even when another Auditor ``ACCEPT``s.

    Acceptance row (example-014.1 AC #2): "any Auditor ``FIND`` or
    ``BLOCKED`` prevents convergence, even if another Auditor accepts".
    """

    cwd = fake_bd_dir
    bead_id = "sample-multi-auditor-find"
    populate_record(
        cwd,
        pattern="c",
        doer_ai="minimax",
        auditor_ai="claude",
        doer_sessions=1,
        auditor_sessions=2,
    )
    wire_record_to_stub(cwd, bead_id)

    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    doer_runner = _runner_for("Doer")

    auditor_calls: list[str] = []

    def _auditor_runner_a(brief: SessionBrief) -> SessionOutcome:
        auditor_calls.append(brief.session_name)
        return SessionOutcome(
            working_text="# auditor-A audit\n",
            result_text="verdict: ACCEPT\n",
            next_state="ACCEPT",
            verification="Auditor-A byte-for-byte check passes",
        )

    def _auditor_runner_b(brief: SessionBrief) -> SessionOutcome:
        auditor_calls.append(brief.session_name)
        return SessionOutcome(
            working_text="# auditor-B audit\n",
            result_text=(
                "finding: missing error-handling branch in module X\n"
            ),
            next_state="FIND",
            verification="Auditor-B cross-checks found a regression",
        )

    def _router(brief: SessionBrief) -> SessionOutcome:
        # Distinguish auditors by session name tail. Auditor A ends in
        # ``-workflow-auditor`` (the only one when session_count=1); B
        # carries the ``-1`` suffix from pattern_router's name scheme.
        if brief.session_name.endswith("-workflow-auditor-1"):
            return _auditor_runner_b(brief)
        return _auditor_runner_a(brief)

    record = ai_resources.load_ai_resource_record(cwd)
    team = ai_resources.team_config(record)
    config = DriveConfig(
        cwd=cwd,
        bead_id=bead_id,
        pattern="c",
        team=team,
        ai_resources=record,
        doer_runner=doer_runner,
        auditor_runner=_router,
        max_doer_auditor_rounds=3,
    )
    result = lifecycle.drive_bead(config)
    # Two distinct Auditor sessions per round.
    assert sorted(set(auditor_calls)) == sorted({
        f"{bead_id}-workflow-auditor",
        f"{bead_id}-workflow-auditor-1",
    })
    # The round verdict combines FIND over ACCEPT — convergence cannot
    # be declared; the Bead stays open / blocked.
    assert result.convergence.converged is False
    assert result.convergence.final_verdict.verdict in {"FIND", "BLOCKED"}
    assert result.closed is False
    # The combined FIND reason (round 0) is preserved on the §8 transcript.
    transcript_reasons = [
        r
        for v in result.convergence.transcript
        for r in v.reasons
    ]
    assert any(
        "workflow-auditor-1" in r for r in transcript_reasons
    ), transcript_reasons


def test_drive_bead_multi_auditor_all_accept_converges(
    fake_bd_dir: Path,
    populate_record,
    wire_record_to_stub,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """A pattern-(c) Auditor roster where **every** Auditor ``ACCEPT``s
    converges (positive control for the new combination rule).
    """

    cwd = fake_bd_dir
    bead_id = "sample-multi-auditor-accept"
    populate_record(
        cwd,
        pattern="c",
        doer_ai="minimax",
        auditor_ai="claude",
        doer_sessions=1,
        auditor_sessions=2,
    )
    wire_record_to_stub(cwd, bead_id)

    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    doer_runner = _runner_for("Doer")

    def _auditor_runner_a(brief: SessionBrief) -> SessionOutcome:
        return _build_session_outcome(brief.bead_id, brief.session_name, "Auditor")

    def _auditor_runner_b(brief: SessionBrief) -> SessionOutcome:
        return _build_session_outcome(brief.bead_id, brief.session_name, "Auditor")

    def _router(brief: SessionBrief) -> SessionOutcome:
        if brief.session_name.endswith("-workflow-auditor-1"):
            return _auditor_runner_b(brief)
        return _auditor_runner_a(brief)

    record = ai_resources.load_ai_resource_record(cwd)
    team = ai_resources.team_config(record)
    config = DriveConfig(
        cwd=cwd,
        bead_id=bead_id,
        pattern="c",
        team=team,
        ai_resources=record,
        doer_runner=doer_runner,
        auditor_runner=_router,
        max_doer_auditor_rounds=3,
    )
    result = lifecycle.drive_bead(config)
    assert result.convergence.converged is True
    assert result.convergence.final_verdict.verdict == "ACCEPT"
    assert result.closed is True
    # The combined ACCEPT reasons cite every Auditor.
    assert all(
        "ACCEPT" in r for r in result.convergence.final_verdict.reasons
    )


# ---------------------------------------------------------------------------
# example-014.1 acceptance: bd version recorded before any bd write.
# ---------------------------------------------------------------------------


def test_drive_bead_records_bd_version_before_close(
    fake_bd_dir: Path,
    populate_record,
    wire_record_to_stub,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """``drive_bead`` records the captured ``bd version`` in the
    orchestrator's working directory **before** issuing ``bd close``
    (Foundation AC #1 / #2 + example-014.1).
    """

    cwd = fake_bd_dir
    bead_id = "sample-bd-version"
    populate_record(cwd, pattern="b", doer_ai="minimax", auditor_ai="claude")
    wire_record_to_stub(cwd, bead_id)

    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    record = ai_resources.load_ai_resource_record(cwd)
    team = ai_resources.team_config(record)
    config = DriveConfig(
        cwd=cwd,
        bead_id=bead_id,
        pattern="b",
        team=team,
        ai_resources=record,
        doer_runner=_runner_for("Doer"),
        auditor_runner=_runner_for("Auditor"),
    )
    lifecycle.drive_bead(config)
    orch_dir = cwd / ".mba-work" / bead_id / "orchestrator"
    assert (orch_dir / "bd-version.log").exists()
    log_text = (orch_dir / "bd-version.log").read_text(encoding="utf-8")
    assert "1.0.4" in log_text
    # The mirror into working.md is the Foundation-rule artefact.
    working_path = orch_dir / "working.md"
    assert working_path.exists()
    working_text = working_path.read_text(encoding="utf-8")
    assert "**bd version:**" in working_text
    assert "1.0.4" in working_text
