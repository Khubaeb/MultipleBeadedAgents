"""Tests for ``mba_runtime.external_dispatch``.

The integration tests exercise the **real** dispatch mechanism via
a fake external worker (a Python script in ``tmp_path``) inside a
**disposable** Beads repository. The tests prove the runtime really
spawns subprocesses for Doer and Auditor sessions (audit F2),
that the gate refuses the dispatch without a recorded human-origin
decision (audit G1), and that the adapter is the single writer of
the worker's role-attributed Bead comment (audit G2).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from mba_runtime import ai_resources, lifecycle
from mba_runtime.ai_resources import (
    AIResource,
    AIResourceRecord,
    ResponsibilityConfig,
    TeamConfig,
)
from mba_runtime.external_dispatch import (
    AuthorityContext,
    ExternalDispatchError,
    ExternalProcessSessionRunner,
    LaunchProvenance,
    authority_decision_fn_allow_when,
    validate_launch_provenance,
)
from mba_runtime.user_authority import UserAuthorityRequired


WORKER_SOURCE = r"""#!/usr/bin/env python3
import argparse, json, pathlib

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bead-id", required=True)
    parser.add_argument("--session-name", required=True)
    parser.add_argument("--session-dir", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--allowed-write-root", required=True)
    parser.add_argument("--hat", required=True)
    parser.add_argument("--round", required=True, type=int)
    args = parser.parse_args()

    session_dir = pathlib.Path(args.session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)
    prompt_text = pathlib.Path(args.prompt).read_text(encoding="utf-8")

    if args.hat == "Engineer":
        working = (
            f"# Doer working - {args.session_name}\n"
            f"- bead: {args.bead_id}\n"
            f"- round: {args.round}\n"
            f"- prompt excerpt: {prompt_text[:80]!r}\n"
            "- write working.md\n"
        )
        result = (
            f"# Doer result - {args.session_name}\n"
            f"- round: {args.round}\n"
            "- produced artefact\n"
        )
        comment = (
            f"- **result:** Doer (round {args.round}) produced working.md + result.md\n"
            f"- **verification:** read-back via dispatcher captured the artefacts\n"
            f"- **next:** await auditor review\n"
            f"- **details:** `.mba-work/{args.bead_id}/{args.session_name}/working.md`\n"
        )
    else:
        working = (
            f"# Auditor working - {args.session_name}\n"
            f"- bead: {args.bead_id}\n"
            f"- round: {args.round}\n"
            "- audit evidence cross-checked\n"
        )
        result = (
            f"# Auditor result - {args.session_name}\n"
            "\n"
            "VERDICT: ACCEPT\n"
            "RESOLUTION: changed\n"
            "EVIDENCE:\n"
            "- working.md contents cross-checked against AC\n"
            "- result.md read-back matches Doer's claim\n"
        )
        comment = (
            f"- **result:** Auditor (round {args.round}) accepted the Doer artefact\n"
            f"- **verification:** evidence cross-checked\n"
            f"- **next:** ready for bd close\n"
            f"- **details:** `.mba-work/{args.bead_id}/{args.session_name}/working.md`\n"
        )
        (session_dir / "_verdict.txt").write_text("ACCEPT", encoding="utf-8")

    (session_dir / "working.md").write_text(working, encoding="utf-8")
    (session_dir / "result.md").write_text(result, encoding="utf-8")
    (session_dir / "comment.md").write_text(comment, encoding="utf-8")


if __name__ == "__main__":
    main()
"""


OUT_OF_ROOT_WORKER_SOURCE = r"""#!/usr/bin/env python3
import argparse, pathlib

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bead-id", required=True)
    parser.add_argument("--session-name", required=True)
    parser.add_argument("--session-dir", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--allowed-write-root", required=True)
    parser.add_argument("--hat", required=True)
    parser.add_argument("--round", required=True, type=int)
    args = parser.parse_args()

    session_dir = pathlib.Path(args.session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "working.md").write_text(
        f"# {args.hat} rogue working\n", encoding="utf-8"
    )
    (session_dir / "result.md").write_text(
        f"# {args.hat} rogue result\n", encoding="utf-8"
    )
    (session_dir / "comment.md").write_text(
        "- **result:** rogue\n- **verification:** none\n"
        "- **next:** end\n- **details:** Bead comment is complete; no separate file.\n",
        encoding="utf-8",
    )
    # The mandated contract artefact is written; the abuse is the
    # OUT-OF-ROOT write below — the adapter must refuse launch of
    # this worker before it spawns (G1 correction: the runtime
    # does NOT pre/post-diff; the gate decides pre-launch).
    outside_target = pathlib.Path(__file__).resolve().parent / "_outside_marker"
    outside_target.write_text("outside", encoding="utf-8")


if __name__ == "__main__":
    main()
"""


@pytest.fixture()
def fake_worker(tmp_path: Path) -> Path:
    path = tmp_path / "_worker.py"
    path.write_text(WORKER_SOURCE, encoding="utf-8")
    return path


@pytest.fixture()
def out_of_root_worker(tmp_path: Path) -> Path:
    path = tmp_path / "_rogue_worker.py"
    path.write_text(OUT_OF_ROOT_WORKER_SOURCE, encoding="utf-8")
    return path


@pytest.fixture()
def populate_record() -> object:
    def _factory(cwd: Path, **kwargs: object) -> AIResourceRecord:
        record = AIResourceRecord(
            schema=1,
            note="external-dispatch test fixture",
            resources=(
                AIResource(
                    id="minimax",
                    label="MiniMax-M3",
                    capabilities=("doer", "auditor"),
                    session_lifetime="fresh_per_session",
                ),
                AIResource(
                    id="claude",
                    label="Claude Opus 4.8",
                    capabilities=("doer", "auditor"),
                    session_lifetime="fresh_per_session",
                ),
            ),
            teams={
                "default": TeamConfig(
                    name="default",
                    doer=ResponsibilityConfig(
                        ai="minimax", hat="Engineer", session_count=1
                    ),
                    auditor=ResponsibilityConfig(
                        ai="claude",
                        hat="Workflow Auditor",
                        session_count=1,
                    ),
                    pattern="b",
                )
            },
        )
        ai_resources.save_ai_resource_record(cwd, record)
        return record

    return _factory


def _authority_allow(cwd: Path) -> AuthorityContext:
    """Build an ``AuthorityContext`` whose decision function approves.

    Used by every test that **wants** the dispatcher to launch. The
    actor is intentionally non-``cli`` / non-``decision_fn_default``
    (per the G1 correctness gate), so the dispatch is allowed.
    """

    return AuthorityContext(
        decision_fn=authority_decision_fn_allow_when(
            test_session_marker="unittest"
        ),
        project_cwd=cwd,
        trusted_confinement=False,
    )


# ---------------------------------------------------------------------------
# G1 fail-closed tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "actor",
    [
        "",
        "human",
        "human:",
        "human:   ",
        "human:\t",
        "human:  \t  \n",
        "Human:",          # case mismatch
        "human_0",        # wrong separator
    ],
)
def test_is_human_origin_rejects_empty_or_whitespace_identity(actor: str) -> None:
    """Resume-3 edge: the identity after ``human:`` must be non-empty.

    The runtime refuses any actor whose ``human:`` suffix is empty
    or whitespace-only; the strict validator is the load-bearing
    invariant of the read-only decision loader.
    """

    from mba_runtime.external_dispatch import _is_human_origin

    assert _is_human_origin(actor) is False, (
        f"actor {actor!r} should not be human-origin; "
        f"the identity after `human:` is empty or whitespace"
    )


@pytest.mark.parametrize(
    "actor",
    [
        "human:user",
        "human:oncall",
        "human:engineer",
        "human:0",  # any non-whitespace token counts
        "  human:user  ",  # surrounding whitespace is fine
    ],
)
def test_is_human_origin_accepts_non_empty_identity(actor: str) -> None:
    """The strict validator accepts any well-formed human prefix."""

    from mba_runtime.external_dispatch import _is_human_origin

    assert _is_human_origin(actor) is True, (
        f"actor {actor!r} should be recognised human-origin"
    )


def test_external_dispatch_refuses_without_human_origin_decision(
    fake_bd_dir: Path, tmp_path: Path
) -> None:
    """G1 fail-closed: no recorded human-origin decision ⇢ no spawn.

    The default decision function (no override) must refuse to
    launch because no decisions file is populated. The worker's
    external process is **not** created.
    """

    cwd = fake_bd_dir
    marker = cwd / "_outside_marker"
    runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(cwd / "_rogue.py")),  # never invoked
        authority=AuthorityContext(
            decision_fn=None, project_cwd=cwd, trusted_confinement=False
        ),
    )
    brief = lifecycle.SessionBrief(
        bead_id="sample-1",
        session_dir=cwd / ".mba-work" / "sample-1" / "doer",
        session_name="sample-1-doer",
        responsibility="Doer",
        hat="Engineer",
        ai_id="minimax",
        ai_label="minimax",
        pattern="b",
        prompt_path=cwd / "_prompt.md",
        artefact_paths={},
    )
    with pytest.raises(UserAuthorityRequired):
        runner(brief)
    assert not marker.exists()


def test_external_dispatch_refuses_self_approval(
    fake_bd_dir: Path, tmp_path: Path
) -> None:
    """G1: ``cli`` / ``decision_fn_default`` actors are refused.

    Self-approval from non-human origins raises a loud refusal.
    The adapter must not auto-approve and must not spawn.
    """

    cwd = fake_bd_dir
    sentinel = tmp_path / "_self_approval.jsonl"
    sentinel.write_text(
        json.dumps(
            {
                "action": "external_dispatch_unconfinement",
                "actor": "cli",
                "rationale": "tried self-approval",
                "approved": True,
                "recorded_at": "2026-07-20T03:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    from mba_runtime.external_dispatch import (
        authority_decision_fn_from_decisions_file,
    )

    runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(cwd / "_rogue.py")),
        authority=AuthorityContext(
            decision_fn=authority_decision_fn_from_decisions_file(sentinel),
            project_cwd=cwd,
            trusted_confinement=False,
        ),
    )
    brief = lifecycle.SessionBrief(
        bead_id="sample-1",
        session_dir=cwd / ".mba-work" / "sample-1" / "doer",
        session_name="sample-1-doer",
        responsibility="Doer",
        hat="Engineer",
        ai_id="minimax",
        ai_label="minimax",
        pattern="b",
        prompt_path=cwd / "_prompt.md",
        artefact_paths={},
    )
    with pytest.raises(UserAuthorityRequired):
        runner(brief)


def test_external_dispatch_internal_trusted_confinement_seam(
    fake_bd_dir: Path, tmp_path: Path
) -> None:
    """``trusted_confinement`` is an internal seam only.

    A concrete dispatcher implementation that actually proves
    repository boundary enforcement (job object / sandbox-exec /
    bwrap) sets ``trusted_confinement=True`` on its
    :class:`AuthorityContext`. The **generic** public CLI never does
    (no ``--trust-host-confinement`` flag is exposed). This unit test
    pins the seam without claiming the generic CLI can self-approve.
    """

    cwd = fake_bd_dir
    records_dir = tmp_path / ".mba-work" / "sample-1" / "trusted"
    worker = tmp_path / "_trusted_worker.py"
    worker.write_text(
        "import argparse, pathlib\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--bead-id', required=True)\n"
        "parser.add_argument('--session-name', required=True)\n"
        "parser.add_argument('--session-dir', required=True)\n"
        "parser.add_argument('--prompt', required=True)\n"
        "parser.add_argument('--allowed-write-root', required=True)\n"
        "parser.add_argument('--hat', required=True)\n"
        "parser.add_argument('--round', required=True, type=int)\n"
        "args = parser.parse_args()\n"
        "sd = pathlib.Path(args.session_dir)\n"
        "sd.mkdir(parents=True, exist_ok=True)\n"
        "(sd / 'working.md').write_text('w', encoding='utf-8')\n"
        "(sd / 'result.md').write_text('r', encoding='utf-8')\n"
        "(sd / 'comment.md').write_text('- **result:** ok\\n- **verification:** ok\\n- **next:** done\\n- **details:** Bead comment is complete; no separate file.\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )

    from mba_runtime.external_dispatch import AuthorityContext

    runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(worker)),
        authority=AuthorityContext(
            decision_fn=None,  # never invoked when trusted_confinement=True
            project_cwd=cwd,
            trusted_confinement=True,
        ),
    )
    brief = lifecycle.SessionBrief(
        bead_id="sample-1",
        session_dir=records_dir,
        session_name="sample-1-doer",
        responsibility="Doer",
        hat="Engineer",
        ai_id="minimax",
        ai_label="minimax",
        pattern="b",
        prompt_path=tmp_path / "_prompt.md",
        artefact_paths={},
    )
    outcome = runner(brief)
    assert outcome.working_text == "w"


def test_external_dispatch_accepts_persistent_human_origin_decision(
    fake_bd_dir: Path, tmp_path: Path
) -> None:
    """G1 path-2: persistent, recorded human-origin approval persists.

    A project-scoped decision file with one human-origin row
    lets the adapter launch. The decision is reusable across
    rounds.
    """

    cwd = fake_bd_dir
    records_dir = tmp_path / ".mba-work" / "sample-1" / "persistent"
    decisions = tmp_path / "decisions.jsonl"
    decisions.write_text(
        json.dumps(
            {
                "action": "external_dispatch_unconfinement",
                "actor": "human:user",
                "rationale": "manually approved for this disposable project",
                "approved": True,
                "recorded_at": "2026-07-20T03:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    from mba_runtime.external_dispatch import (
        authority_decision_fn_from_decisions_file,
    )

    fake_worker_path = tmp_path / "_ok.py"
    fake_worker_path.write_text(
        "import argparse, pathlib\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--bead-id', required=True)\n"
        "parser.add_argument('--session-name', required=True)\n"
        "parser.add_argument('--session-dir', required=True)\n"
        "parser.add_argument('--prompt', required=True)\n"
        "parser.add_argument('--allowed-write-root', required=True)\n"
        "parser.add_argument('--hat', required=True)\n"
        "parser.add_argument('--round', required=True, type=int)\n"
        "args = parser.parse_args()\n"
        "sd = pathlib.Path(args.session_dir)\n"
        "sd.mkdir(parents=True, exist_ok=True)\n"
        "(sd / 'working.md').write_text('w', encoding='utf-8')\n"
        "(sd / 'result.md').write_text('r', encoding='utf-8')\n"
        "(sd / 'comment.md').write_text('- **result:** ok\\n- **verification:** ok\\n- **next:** done\\n- **details:** Bead comment is complete; no separate file.\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )

    runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(fake_worker_path)),
        authority=AuthorityContext(
            decision_fn=authority_decision_fn_from_decisions_file(decisions),
            project_cwd=cwd,
            trusted_confinement=False,
            decisions_path=decisions,
        ),
    )
    brief = lifecycle.SessionBrief(
        bead_id="sample-1",
        session_dir=records_dir,
        session_name="sample-1-doer",
        responsibility="Doer",
        hat="Engineer",
        ai_id="minimax",
        ai_label="minimax",
        pattern="b",
        prompt_path=tmp_path / "_prompt.md",
        artefact_paths={},
    )
    outcome = runner(brief)
    assert outcome.working_text == "w"


def test_external_dispatch_out_of_root_worker_refused_pre_launch(
    fake_bd_dir: Path, out_of_root_worker: Path, tmp_path: Path
) -> None:
    """G1 fail-closed: out-of-root-writing worker is NOT launched.

    No recorded decision, no trusted confinement, so the adapter
    raises before the worker subprocess spawns. The out-of-root
    marker written by the rogue worker never appears.
    """

    cwd = fake_bd_dir
    runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(out_of_root_worker)),
        authority=AuthorityContext(
            decision_fn=None,
            project_cwd=cwd,
            trusted_confinement=False,
        ),
    )
    brief = lifecycle.SessionBrief(
        bead_id="sample-1",
        session_dir=cwd / ".mba-work" / "sample-1" / "doer",
        session_name="sample-1-doer",
        responsibility="Doer",
        hat="Engineer",
        ai_id="minimax",
        ai_label="minimax",
        pattern="b",
        prompt_path=cwd / "_prompt.md",
        artefact_paths={},
    )
    marker = out_of_root_worker.parent / "_outside_marker"
    with pytest.raises(UserAuthorityRequired):
        runner(brief)
    assert not marker.exists(), (
        f"out-of-root marker {marker} unexpectedly exists; the "
        f"G1 fail-closed gate is broken"
    )


def test_external_dispatch_refuses_non_raising_refused_decision(
    fake_bd_dir: Path, tmp_path: Path
) -> None:
    """H1 regression: a non-raising refused decision must not launch.

    ``authority_decision_fn_refuse_when`` returns an
    ``AuthorityDecision(approved=False)`` instead of raising. The
    adapter must enforce the gate's verdict itself — capture the
    returned decision and refuse to spawn the worker when
    ``not decision.approved``. The worker's launch marker must
    not appear on disk.
    """

    cwd = fake_bd_dir
    launch_marker = tmp_path / "_h1_launch_marker.txt"
    worker = tmp_path / "_h1_marker_worker.py"
    worker.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(launch_marker)!r}).write_text("
        f"'launched', encoding='utf-8')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

    from mba_runtime.external_dispatch import authority_decision_fn_refuse_when

    runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(worker)),
        authority=AuthorityContext(
            decision_fn=authority_decision_fn_refuse_when("h1_refusal"),
            project_cwd=cwd,
            trusted_confinement=False,
        ),
    )
    brief = lifecycle.SessionBrief(
        bead_id="sample-1",
        session_dir=cwd / ".mba-work" / "sample-1" / "doer",
        session_name="sample-1-doer",
        responsibility="Doer",
        hat="Engineer",
        ai_id="minimax",
        ai_label="minimax",
        pattern="b",
        prompt_path=cwd / "_prompt.md",
        artefact_paths={},
    )
    with pytest.raises(UserAuthorityRequired):
        runner(brief)
    assert not launch_marker.exists(), (
        f"worker launch marker {launch_marker} exists; the adapter "
        f"spawned the worker despite a non-raising refused decision "
        f"(H1 regression)"
    )


# ---------------------------------------------------------------------------
# Launch provenance and visible failure handoff
# ---------------------------------------------------------------------------


def test_launch_provenance_rejects_orchestrator_identity() -> None:
    with pytest.raises(ExternalDispatchError, match="Orchestrator"):
        validate_launch_provenance(
            LaunchProvenance(
                worker_session_id="worker-1",
                observed_session_id="worker-1",
                orchestrator_session_id="worker-1",
                host_exposes_identity=True,
            )
        )


def test_launch_provenance_rejects_unbound_identity() -> None:
    with pytest.raises(ExternalDispatchError, match="tied"):
        validate_launch_provenance(
            LaunchProvenance(
                worker_session_id="worker-1",
                observed_session_id="other-worker",
                host_exposes_identity=True,
            )
        )


def test_launch_provenance_allows_pid_only_when_identity_is_unavailable() -> None:
    validate_launch_provenance(LaunchProvenance(pid=42))
    with pytest.raises(ExternalDispatchError, match="PID"):
        validate_launch_provenance(LaunchProvenance())


def test_missing_worker_report_surfaces_blocked_human_handoff(
    fake_bd_dir: Path, tmp_path: Path, monkeypatch
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_call(bd_binary: str, *, args, cwd: Path, **kwargs):
        calls.append(tuple(args))
        return subprocess.CompletedProcess(
            args=[bd_binary, *args], returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr("mba_runtime.external_dispatch.bd_client.call", fake_call)
    worker = tmp_path / "_missing_report.py"
    worker.write_text(
        "import argparse, pathlib\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--session-dir', required=True)\n"
        "args, _ = parser.parse_known_args()\n"
        "pathlib.Path(args.session_dir).mkdir(parents=True, exist_ok=True)\n"
        "(pathlib.Path(args.session_dir) / 'working.md').write_text('w')\n",
        encoding="utf-8",
    )
    cwd = fake_bd_dir
    runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(worker)),
        authority=_authority_allow(cwd),
    )
    brief = lifecycle.SessionBrief(
        bead_id="handoff-1",
        session_dir=cwd / ".mba-work" / "handoff-1" / "doer",
        session_name="handoff-1-doer",
        responsibility="Doer",
        hat="Engineer",
        ai_id="minimax",
        ai_label="minimax",
        pattern="b",
        prompt_path=cwd / "_prompt.md",
        artefact_paths={},
    )
    with pytest.raises(ExternalDispatchError, match="required worker artefacts"):
        runner(brief)
    assert calls[0][:3] == ("comments", "add", "handoff-1")
    assert calls[1][:2] == ("update", "handoff-1")
    assert "blocked" in calls[1]
    assert "Human" in calls[1]
    assert "human" in calls[1]




def test_external_dispatch_spawns_real_worker(
    fake_worker: Path,
    fake_bd_dir: Path,
    populate_record,
    wire_record_to_stub,
    fake_bead_record: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Real subprocess dispatch (audit F2)."""

    cwd = fake_bd_dir
    bead_id = "ext-1"
    populate_record(cwd)
    wire_record_to_stub(cwd, bead_id)

    doer_runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(fake_worker)),
        authority=_authority_allow(cwd),
        timeout_seconds=30,
    )
    auditor_runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(fake_worker)),
        authority=_authority_allow(cwd),
        timeout_seconds=30,
    )
    record = ai_resources.load_ai_resource_record(cwd)
    team = ai_resources.team_config(record)
    config = lifecycle.DriveConfig(
        cwd=cwd,
        bead_id=bead_id,
        pattern="b",
        team=team,
        ai_resources=record,
        doer_runner=doer_runner,
        auditor_runner=auditor_runner,
    )
    result = lifecycle.drive_bead(config)
    assert result.convergence.converged is True
    assert result.closed is True

    for spec in result.plan.all_sessions():
        session_name = spec.session_name(bead_id)
        session_dir = cwd / ".mba-work" / bead_id / session_name
        assert (session_dir / "working.md").exists()
        assert (session_dir / "result.md").exists()
        assert (session_dir / "comment.md").exists()


def test_external_dispatch_refuses_non_zero_exit(
    fake_bd_dir: Path, tmp_path: Path
) -> None:
    """A worker that exits non-zero raises ``ExternalDispatchError``."""

    cwd = fake_bd_dir
    failing = tmp_path / "_fail.py"
    failing.write_text(
        "import sys\n"
        "sys.stderr.write('intentional failure\\n')\n"
        "sys.exit(3)\n",
        encoding="utf-8",
    )
    runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(failing)),
        authority=_authority_allow(cwd),
        timeout_seconds=30,
    )
    brief = lifecycle.SessionBrief(
        bead_id="sample-1",
        session_dir=cwd / ".mba-work" / "sample-1" / "doer",
        session_name="sample-1-doer",
        responsibility="Doer",
        hat="Engineer",
        ai_id="minimax",
        ai_label="minimax",
        pattern="b",
        prompt_path=cwd / "_prompt.md",
        artefact_paths={},
    )
    with pytest.raises(ExternalDispatchError, match="exited"):
        runner(brief)


def test_external_dispatch_refuses_destructive_bd_init_flags(
    fake_bd_dir: Path,
) -> None:
    """example-023.1: workers cannot be launched through destructive bd init."""

    cwd = fake_bd_dir
    prompt = cwd / "_prompt.md"
    prompt.write_text("assignment", encoding="utf-8")
    runner = ExternalProcessSessionRunner(
        dispatch_argv=("bd", "init", "--reinit-local"),
        authority=AuthorityContext(
            decision_fn=None,
            project_cwd=cwd,
            trusted_confinement=True,
        ),
    )
    brief = lifecycle.SessionBrief(
        bead_id="sample-1",
        session_dir=cwd / ".mba-work" / "sample-1" / "doer",
        session_name="sample-1-doer",
        responsibility="Doer",
        hat="Engineer",
        ai_id="minimax",
        ai_label="minimax",
        pattern="b",
        prompt_path=prompt,
        artefact_paths={},
    )
    with pytest.raises(ExternalDispatchError, match="destructive Beads init"):
        runner(brief)


def test_external_dispatch_refuses_records_dir_outside_project(
    fake_bd_dir: Path,
    tmp_path: Path,
) -> None:
    """example-023.1: worker records must stay under the active project."""

    cwd = fake_bd_dir
    prompt = cwd / "_prompt.md"
    prompt.write_text("assignment", encoding="utf-8")
    outside = tmp_path.parent / f"outside-records-{uuid.uuid4().hex}" / "sample-1" / "doer"
    runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(cwd / "_worker.py")),
        authority=AuthorityContext(
            decision_fn=None,
            project_cwd=cwd,
            trusted_confinement=True,
        ),
        records_dir=outside,
    )
    brief = lifecycle.SessionBrief(
        bead_id="sample-1",
        session_dir=outside,
        session_name="sample-1-doer",
        responsibility="Doer",
        hat="Engineer",
        ai_id="minimax",
        ai_label="minimax",
        pattern="b",
        prompt_path=prompt,
        artefact_paths={},
    )
    with pytest.raises(ExternalDispatchError, match="records_dir must stay inside"):
        runner(brief)


def test_external_dispatch_refuses_when_worker_omits_artefact(
    fake_bd_dir: Path, tmp_path: Path
) -> None:
    """A worker that doesn't write its artefacts raises."""

    cwd = fake_bd_dir
    partial = tmp_path / "_partial.py"
    partial.write_text(
        "import sys\n"
        "# write only result.md; skip working.md and comment.md\n"
        "import pathlib\n"
        "p = pathlib.Path(__file__).parent\n"
        "p.mkdir(parents=True, exist_ok=True)\n"
        "(p / 'result.md').write_text('r')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(partial)),
        authority=_authority_allow(cwd),
        timeout_seconds=30,
    )
    brief = lifecycle.SessionBrief(
        bead_id="sample-1",
        session_dir=cwd / ".mba-work" / "sample-1" / "doer",
        session_name="sample-1-doer",
        responsibility="Doer",
        hat="Engineer",
        ai_id="minimax",
        ai_label="minimax",
        pattern="b",
        prompt_path=cwd / "_prompt.md",
        artefact_paths={},
    )
    with pytest.raises(ExternalDispatchError, match="working"):
        runner(brief)


def test_external_dispatch_in_disposable_bd_repo(
    fake_worker: Path,
    authorized_workspace: Path,
    monkeypatch,
) -> None:
    """End-to-end against real ``bd 1.0.4`` in a disposable repository.

    Runs in the authorized workspace (an OS temporary directory
    outside the live repository, per the User-authorised test-only
    exception). The workspace is created uniquely for this test and
    removed at teardown. A persistent human-origin decision file is
    supplied so the adapter's pre-launch gate permits dispatch
    against the real ``bd`` binary; we avoid running the rogue
    worker entirely.
    """

    cwd = authorized_workspace
    bead_id = "ed-bead-1"

    init = subprocess.run(
        ["bd", "init", "--non-interactive", "--prefix", "ed"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd),
    )
    assert init.returncode == 0, init.stderr

    create = subprocess.run(
        [
            "bd",
            "create",
            "--title",
            "External dispatch sample",
            "--id",
            bead_id,
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd),
    )
    assert create.returncode == 0, create.stderr

    decisions = cwd / "decisions.jsonl"
    decisions.write_text(
        json.dumps(
            {
                "action": "external_dispatch_unconfinement",
                "actor": "human:user",
                "rationale": "manually approved for this disposable project",
                "approved": True,
                "recorded_at": "2026-07-20T03:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    from mba_runtime.external_dispatch import (
        authority_decision_fn_from_decisions_file,
    )

    record = AIResourceRecord(
        schema=1,
        note="external-dispatch integration fixture",
        resources=(
            AIResource(
                id="minimax",
                label="MiniMax-M3",
                capabilities=("doer", "auditor"),
                session_lifetime="fresh_per_session",
            ),
            AIResource(
                id="claude",
                label="Claude Opus 4.8",
                capabilities=("doer", "auditor"),
                session_lifetime="fresh_per_session",
            ),
        ),
        teams={
            "default": TeamConfig(
                name="default",
                doer=ResponsibilityConfig(
                    ai="minimax", hat="Engineer", session_count=1
                ),
                auditor=ResponsibilityConfig(
                    ai="claude",
                    hat="Workflow Auditor",
                    session_count=1,
                ),
                pattern="b",
            )
        },
    )
    ai_resources.save_ai_resource_record(cwd, record)

    runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(fake_worker)),
        authority=AuthorityContext(
            decision_fn=authority_decision_fn_from_decisions_file(decisions),
            project_cwd=cwd,
            trusted_confinement=False,
            decisions_path=decisions,
        ),
        timeout_seconds=30,
    )
    team = ai_resources.team_config(record)
    config = lifecycle.DriveConfig(
        cwd=cwd,
        bead_id=bead_id,
        pattern="b",
        team=team,
        ai_resources=record,
        doer_runner=runner,
        auditor_runner=runner,
    )
    result = lifecycle.drive_bead(config)
    assert result.convergence.converged is True

    for spec in result.plan.all_sessions():
        session_name = spec.session_name(bead_id)
        session_dir = cwd / ".mba-work" / bead_id / session_name
        assert (session_dir / "working.md").exists()
        assert (session_dir / "result.md").exists()
        assert (session_dir / "comment.md").exists()

    comments_proc = subprocess.run(
        ["bd", "comments", bead_id],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd),
    )
    body = comments_proc.stdout
    assert "Engineer" in body
    assert "Workflow Auditor" in body
    assert "via dispatcher" in body

    # G2 invariant: exactly one Doer + one Auditor comment (no
    # double-post).
    doer_count = body.count("[Engineer] at")
    auditor_count = body.count("[Workflow Auditor] at")
    assert doer_count == 1, body
    assert auditor_count == 1, body


# ---------------------------------------------------------------------------
# MBA-owned worker cleanup (example-cleanup)
# ---------------------------------------------------------------------------


from mba_runtime.external_dispatch import (  # noqa: E402
    CLEANUP_CLEANED,
    CLEANUP_NOTHING,
    CLEANUP_PROTECTED,
    CLEANUP_REFUSED,
    CleanupOutcome,
    MBAOwnedWorker,
    cleanup_mba_owned_worker,
)


def _worker(
    *,
    pid: int | None = 1234,
    session_id: str | None = "worker-session-1",
    transcript_id: str | None = None,
    worker_identity: str | None = None,
    orchestrator_identity: str | None = None,
    ownership_proof: str = "bead=sample-1;session=doer;hat=Engineer;pid=1234",
) -> MBAOwnedWorker:
    """Build an MBAOwnedWorker with the standard example-cleanup fields."""

    return MBAOwnedWorker(
        bead_id="sample-1",
        session_name="sample-1-doer",
        hat="Engineer",
        pid=pid,
        session_id=session_id,
        transcript_id=transcript_id,
        worker_identity=worker_identity,
        orchestrator_identity=orchestrator_identity,
        ownership_proof=ownership_proof,
    )


def test_cleanup_noop_when_nothing_recorded() -> None:
    """A worker record with no PID and no session id is a no-op.

    The cleanup gate is only invoked against a launched worker;
    before launch the record is empty and the call is meaningless.
    The helper must not raise and must not falsely report a kill.
    """

    worker = MBAOwnedWorker(
        bead_id="sample-1",
        session_name="sample-1-doer",
    )
    killed: list[int] = []
    blocked: list[tuple[str, str]] = []
    outcome = cleanup_mba_owned_worker(
        worker,
        is_alive=lambda pid: True,
        kill=lambda pid: killed.append(pid),
        record_blocked=lambda bead, text: blocked.append((bead, text)),
    )
    assert outcome.action == CLEANUP_NOTHING
    assert killed == []
    assert blocked == []
    assert outcome.blocked_for_human is False


def test_cleanup_kills_an_alive_mba_owned_worker() -> None:
    """Successful completion: cleanup kills the still-alive MBA-owned worker.

    The contract is that the runtime records the launched PID /
    session id and kills the worker at the terminal state when the
    proof of MBA ownership is on file. The worker is alive, the
    record carries an identity, and the helper must kill it.
    """

    worker = _worker()
    killed: list[int] = []
    blocked: list[tuple[str, str]] = []
    outcome = cleanup_mba_owned_worker(
        worker,
        is_alive=lambda pid: True,
        kill=lambda pid: killed.append(pid),
        record_blocked=lambda bead, text: blocked.append((bead, text)),
    )
    assert outcome.action == CLEANUP_CLEANED
    assert killed == [1234]
    assert blocked == []
    assert outcome.pid == 1234
    assert outcome.blocked_for_human is False


def test_cleanup_noop_when_worker_already_exited() -> None:
    """If the worker exited between spawn and cleanup, the gate must
    not falsely kill.

    The runner waits for the subprocess via ``communicate`` so the
    worker is normally reaped before the cleanup gate runs. A
    dispatcher that uses a long-lived shell wrapper (e.g. an
    OpenCode / Claude session launched via ``Start-Process
    -PassThru``) sees the same worker pinned to a live PID; this
    test pins the clean-state path.
    """

    worker = _worker()
    killed: list[int] = []
    outcome = cleanup_mba_owned_worker(
        worker,
        is_alive=lambda pid: False,
        kill=lambda pid: killed.append(pid),
    )
    assert outcome.action == CLEANUP_NOTHING
    assert killed == []
    assert "exited" in outcome.reason.lower()


def test_cleanup_refuses_ambiguous_ownership_when_no_identity_binding() -> None:
    """A worker with only a PID and no identity binding is refused.

    PID-only cleanup is not acceptable when stronger identity was
    available. The helper refuses, posts the blocked-Human handoff,
    and signals ``blocked_for_human=True`` so the caller can route
    the round to the blocked-handoff path.
    """

    worker = MBAOwnedWorker(
        bead_id="sample-1",
        session_name="sample-1-doer",
        hat="Engineer",
        pid=9999,
        # No session_id, transcript_id, worker_identity, ownership_proof.
    )
    killed: list[int] = []
    blocked: list[tuple[str, str]] = []
    outcome = cleanup_mba_owned_worker(
        worker,
        is_alive=lambda pid: True,
        kill=lambda pid: killed.append(pid),
        record_blocked=lambda bead, text: blocked.append((bead, text)),
    )
    assert outcome.action == CLEANUP_REFUSED
    assert killed == []
    assert outcome.blocked_for_human is True
    assert len(blocked) == 1
    bead, comment = blocked[0]
    assert bead == "sample-1"
    assert "identity binding" in comment.lower() or "ownership" in comment.lower()
    assert "session-1-doer" in comment or "sample-1-doer" in comment


def test_cleanup_refuses_when_worker_identity_matches_orchestrator() -> None:
    """Refuse to kill a session whose identity matches the Orchestrator.

    The runtime must never kill its own session. The receipt
    records the launched worker identity and the Orchestrator
    identity; matching them is a clear refusal.
    """

    worker = _worker(
        session_id="orchestrator-session",
        worker_identity="orchestrator-session",
    )
    killed: list[int] = []
    blocked: list[tuple[str, str]] = []
    outcome = cleanup_mba_owned_worker(
        worker,
        is_alive=lambda pid: True,
        kill=lambda pid: killed.append(pid),
        record_blocked=lambda bead, text: blocked.append((bead, text)),
        orchestrator_session_id="orchestrator-session",
    )
    assert outcome.action == CLEANUP_REFUSED
    assert killed == []
    assert outcome.blocked_for_human is True
    assert any(
        "orchestrator" in text.lower() for _bead, text in blocked
    ), f"blocked handoff should mention orchestrator: {blocked}"


def test_cleanup_refuses_when_worker_transcript_matches_orchestrator() -> None:
    """Same as above but at the transcript-id level."""

    worker = _worker(
        session_id="worker-session",
        transcript_id="orchestrator-transcript",
    )
    blocked: list[tuple[str, str]] = []
    outcome = cleanup_mba_owned_worker(
        worker,
        is_alive=lambda pid: True,
        kill=lambda pid: None,
        record_blocked=lambda bead, text: blocked.append((bead, text)),
        orchestrator_transcript_id="orchestrator-transcript",
    )
    assert outcome.action == CLEANUP_REFUSED
    assert outcome.blocked_for_human is True
    assert any(
        "transcript" in text.lower() for _bead, text in blocked
    ), f"blocked handoff should mention transcript: {blocked}"


def test_cleanup_leaves_user_session_untouched() -> None:
    """example-cleanup: never clean up non-MBA / user sessions.

    The user may run their own Claude / OpenCode session in the
    same repo. Cleanup must never touch a session whose identity is
    not MBA-owned. A user session is recorded with
    ``owner=OWNER_USER``; the helper sees the ownership marker
    and refuses to kill.
    """

    from mba_runtime.external_dispatch import CLEANUP_NOT_MBA_OWNED, OWNER_USER

    worker = MBAOwnedWorker(
        bead_id="sample-1",
        session_name="sample-1-doer",
        hat="Engineer",
        pid=9999,
        session_id="user-claude-session",
        worker_identity="user-claude",
        owner=OWNER_USER,
    )
    killed: list[int] = []
    blocked: list[tuple[str, str]] = []
    outcome = cleanup_mba_owned_worker(
        worker,
        is_alive=lambda pid: True,
        kill=lambda pid: killed.append(pid),
        record_blocked=lambda bead, text: blocked.append((bead, text)),
    )
    assert outcome.action == CLEANUP_NOT_MBA_OWNED
    assert killed == []
    assert outcome.blocked_for_human is True
    assert len(blocked) == 1
    _bead, comment = blocked[0]
    assert "user" in comment.lower()
    assert "claude" in comment.lower() or "opencode" in comment.lower()


def test_cleanup_signals_protected_on_permission_error() -> None:
    """A live PID the runtime cannot kill is recorded as PROTECTED.

    The runtime never escalates a permission refusal into a force
    kill — that is exactly the "blind kill" the contract forbids.
    The helper returns ``PROTECTED`` and posts the blocked-handoff
    so the user can stop the worker manually.
    """

    worker = _worker()

    def kill(pid: int) -> None:
        raise PermissionError("not ours")

    blocked: list[tuple[str, str]] = []
    outcome = cleanup_mba_owned_worker(
        worker,
        is_alive=lambda pid: True,
        kill=kill,
        record_blocked=lambda bead, text: blocked.append((bead, text)),
    )
    assert outcome.action == CLEANUP_PROTECTED
    assert outcome.blocked_for_human is True
    assert any(
        "permission" in text.lower() or "stop" in text.lower()
        for _bead, text in blocked
    ), f"blocked handoff should mention permission: {blocked}"


def test_cleanup_skips_when_pid_already_exited_between_alive_and_kill() -> None:
    """A worker that exits between the alive-check and the kill is
    a graceful no-op.

    The race window is real: the alive-check may return ``True``
    but the process may reap itself before the kill lands. The
    helper must surface this as ``NOTHING_TO_CLEAN`` rather than
    a refused or terminated-with-error outcome.
    """

    worker = _worker()

    def kill(pid: int) -> None:
        raise ProcessLookupError(pid)

    outcome = cleanup_mba_owned_worker(
        worker,
        is_alive=lambda pid: True,
        kill=kill,
    )
    assert outcome.action == CLEANUP_NOTHING
    assert "exited" in outcome.reason.lower()


def test_cleanup_noop_when_pid_is_zero() -> None:
    """A zero/negative PID is not a valid worker PID; the helper
    returns ``NOTHING_TO_CLEAN`` with a descriptive reason.
    """

    worker = _worker(pid=0)
    killed: list[int] = []
    outcome = cleanup_mba_owned_worker(
        worker,
        is_alive=lambda pid: True,
        kill=lambda pid: killed.append(pid),
    )
    assert outcome.action == CLEANUP_NOTHING
    assert killed == []
    assert reason_mentions_no_pid(outcome.reason)


def test_cleanup_noop_when_no_pid_but_session_id_only() -> None:
    """A session id without a PID is also a no-op (the host must
    clean up via the host's session manager). The helper still
    cannot legally kill a process without a PID.
    """

    worker = _worker(pid=None, session_id="worker-session-1")
    killed: list[int] = []
    outcome = cleanup_mba_owned_worker(
        worker,
        is_alive=lambda pid: True,
        kill=lambda pid: killed.append(pid),
    )
    assert outcome.action == CLEANUP_NOTHING
    assert killed == []
    assert "no pid" in outcome.reason.lower() or "session manager" in outcome.reason.lower()


def reason_mentions_no_pid(reason: str) -> bool:
    return "no pid" in reason.lower() or "pid" in reason.lower()


def test_runner_invokes_cleanup_on_timeout() -> None:
    """The runner calls the cleanup helper after a worker timeout.

    A worker that times out may still be alive. The cleanup gate
    is the only mechanism that kills the still-alive worker under
    the MBA-owned guarantee. The test forces a timeout via a
    shell that sleeps, then verifies the cleanup callable is
    invoked with the recorded PID.
    """

    cwd = Path(".")
    worker = tmp_path_factory() / "_sleeps.py"  # placeholder
    worker.parent.mkdir(parents=True, exist_ok=True)
    worker.write_text(
        "import time\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    killed: list[int] = []
    blocked: list[tuple[str, str]] = []
    observed: list[int] = []
    runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(worker)),
        authority=AuthorityContext(
            decision_fn=None,
            project_cwd=cwd,
            trusted_confinement=True,
        ),
        timeout_seconds=0.5,
        cleanup_is_alive=lambda pid: True,
        cleanup_kill=lambda pid: (killed.append(pid), observed.append(pid)),
        cleanup_record_blocked=lambda bead, text: blocked.append((bead, text)),
    )
    brief = lifecycle.SessionBrief(
        bead_id="timeout-1",
        session_dir=cwd / ".mba-work" / "timeout-1" / "doer",
        session_name="timeout-1-doer",
        responsibility="Doer",
        hat="Engineer",
        ai_id="minimax",
        ai_label="minimax",
        pattern="b",
        prompt_path=cwd / "_prompt.md",
        artefact_paths={},
    )
    with pytest.raises(ExternalDispatchError, match="timeout"):
        runner(brief)
    # The cleanup gate was invoked with the recorded PID; the
    # kill_fn ran because the worker is still alive.
    assert killed, "cleanup_kill was not invoked on timeout"
    assert blocked == []  # not refused: the worker is MBA-owned and alive
    assert all(pid > 0 for pid in killed)


def tmp_path_factory() -> Path:
    """Best-effort temporary path for the runner-timeout test."""

    import tempfile

    return Path(tempfile.mkdtemp(prefix="example-cleanup-cleanup-"))


def test_runner_kills_tracked_pid_on_timeout_even_when_process_is_alive() -> None:
    """End-to-end: spawn the worker, force a timeout, verify the
    recorded PID is killed by the cleanup callable.

    This is the close-the-loop test that proves the cleanup gate
    is invoked from the real runner path, not just from the
    helper unit tests.
    """

    import tempfile

    cwd = Path(tempfile.mkdtemp(prefix="example-cleanup-cleanup-"))
    (cwd / ".mba-work").mkdir(parents=True, exist_ok=True)
    worker = cwd / "_sleeper.py"
    worker.write_text(
        "import time\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    killed: list[int] = []
    runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(worker)),
        authority=AuthorityContext(
            decision_fn=None,
            project_cwd=cwd,
            trusted_confinement=True,
        ),
        timeout_seconds=0.5,
        cleanup_is_alive=lambda pid: True,
        cleanup_kill=lambda pid: killed.append(pid),
    )
    brief = lifecycle.SessionBrief(
        bead_id="timeout-2",
        session_dir=cwd / ".mba-work" / "timeout-2" / "doer",
        session_name="timeout-2-doer",
        responsibility="Doer",
        hat="Engineer",
        ai_id="minimax",
        ai_label="minimax",
        pattern="b",
        prompt_path=cwd / "_prompt.md",
        artefact_paths={},
    )
    with pytest.raises(ExternalDispatchError):
        runner(brief)
    assert killed, "runner must invoke cleanup_kill on timeout"
    assert killed[0] > 0


def test_runner_no_cleanup_when_disabled() -> None:
    """``cleanup_on_terminal_state=False`` disables the cleanup gate.

    Some test fixtures need the runner to skip cleanup because
    the test exercises a different concern. The flag is opt-out
    and the runner honours it.
    """

    import tempfile

    cwd = Path(tempfile.mkdtemp(prefix="example-cleanup-cleanup-"))
    (cwd / ".mba-work").mkdir(parents=True, exist_ok=True)
    worker = cwd / "_sleeps2.py"
    worker.write_text(
        "import time\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    killed: list[int] = []
    runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(worker)),
        authority=AuthorityContext(
            decision_fn=None,
            project_cwd=cwd,
            trusted_confinement=True,
        ),
        timeout_seconds=0.3,
        cleanup_is_alive=lambda pid: True,
        cleanup_kill=lambda pid: killed.append(pid),
        cleanup_on_terminal_state=False,
    )
    brief = lifecycle.SessionBrief(
        bead_id="disabled-1",
        session_dir=cwd / ".mba-work" / "disabled-1" / "doer",
        session_name="disabled-1-doer",
        responsibility="Doer",
        hat="Engineer",
        ai_id="minimax",
        ai_label="minimax",
        pattern="b",
        prompt_path=cwd / "_prompt.md",
        artefact_paths={},
    )
    with pytest.raises(ExternalDispatchError):
        runner(brief)
    assert killed == []


def test_runner_invokes_cleanup_on_non_zero_exit() -> None:
    """The cleanup gate runs after a non-zero exit too.

    A worker that exits non-zero may still leave a child / wrapper
    process alive. The runner invokes the cleanup gate at every
    terminal state, including the failure path.
    """

    import tempfile

    cwd = Path(tempfile.mkdtemp(prefix="example-cleanup-cleanup-"))
    (cwd / ".mba-work").mkdir(parents=True, exist_ok=True)
    worker = cwd / "_fail.py"
    worker.write_text(
        "import sys\n"
        "sys.stderr.write('intentional failure\\n')\n"
        "sys.exit(3)\n",
        encoding="utf-8",
    )
    killed: list[int] = []
    runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(worker)),
        authority=AuthorityContext(
            decision_fn=None,
            project_cwd=cwd,
            trusted_confinement=True,
        ),
        timeout_seconds=10,
        cleanup_is_alive=lambda pid: True,
        cleanup_kill=lambda pid: killed.append(pid),
    )
    brief = lifecycle.SessionBrief(
        bead_id="fail-1",
        session_dir=cwd / ".mba-work" / "fail-1" / "doer",
        session_name="fail-1-doer",
        responsibility="Doer",
        hat="Engineer",
        ai_id="minimax",
        ai_label="minimax",
        pattern="b",
        prompt_path=cwd / "_prompt.md",
        artefact_paths={},
    )
    with pytest.raises(ExternalDispatchError, match="exited"):
        runner(brief)
    # The cleanup gate was invoked even though the worker exited
    # non-zero. The kill is asked to run; the is_alive callable
    # returns True so the kill_fn is what actually runs.
    assert killed, "cleanup_kill was not invoked on non-zero exit"


def test_runner_cleans_up_stale_worker_before_retry() -> None:
    """example-cleanup: cleanup before retry/relaunch when a stale/live worker exists.

    Simulates the lifecycle calling the cleanup helper before a
    retry. The previous-round worker is still alive (the wrapper
    is alive even though the inner subprocess exited). The
    cleanup helper kills it cleanly and the retry proceeds.

    The runner path is not what the lifecycle calls directly; the
    cleanup is invoked from the lifecycle's pre-retry hook. This
    test pins the cleanup helper's behaviour in that context.
    """

    worker = _worker(pid=7777)
    killed: list[int] = []
    blocked: list[tuple[str, str]] = []
    outcome = cleanup_mba_owned_worker(
        worker,
        is_alive=lambda pid: True,
        kill=lambda pid: killed.append(pid),
        record_blocked=lambda bead, text: blocked.append((bead, text)),
    )
    assert outcome.action == CLEANUP_CLEANED
    assert killed == [7777]
    assert blocked == []
    # The lifecycle can now safely relaunch the replacement worker.
    assert outcome.pid == 7777


def test_runner_refuses_stale_retry_on_ambiguous_ownership() -> None:
    """example-cleanup: before retry/relaunch, ambiguous ownership -> blocked/Human.

    The pre-retry hook must not blindly kill a worker whose
    ownership is not pinned to the launch receipt. The pinned
    record survives the gate; the unpinned record refuses with
    a blocked/Human handoff.
    """

    # Case A: pinned record (session_id + ownership_proof) → kill.
    pinned = MBAOwnedWorker(
        bead_id="retry-1",
        session_name="retry-1-doer",
        pid=8888,
        session_id="worker-1",
        ownership_proof="bead=retry-1;session=retry-1-doer;hat=Engineer;pid=8888",
    )
    killed: list[int] = []
    outcome = cleanup_mba_owned_worker(
        pinned,
        is_alive=lambda pid: True,
        kill=lambda pid: killed.append(pid),
    )
    assert outcome.action == CLEANUP_CLEANED
    assert killed == [8888]

    # Case B: unpinned record (PID only) → refused, blocked_human.
    unpinned = MBAOwnedWorker(
        bead_id="retry-1",
        session_name="retry-1-doer",
        pid=8889,
    )
    blocked: list[tuple[str, str]] = []
    refused_killed: list[int] = []
    outcome = cleanup_mba_owned_worker(
        unpinned,
        is_alive=lambda pid: True,
        kill=lambda pid: refused_killed.append(pid),
        record_blocked=lambda bead, text: blocked.append((bead, text)),
    )
    assert outcome.action == CLEANUP_REFUSED
    assert refused_killed == []
    assert outcome.blocked_for_human is True
    assert blocked, "refusal must invoke the blocked handoff"


def test_cleanup_does_not_kill_when_worker_already_reaped_at_success() -> None:
    """Happy path: the worker exits cleanly via ``communicate`` and
    the cleanup gate sees ``NOTHING_TO_CLEAN``. The pid is no
    longer alive, so the kill is a no-op.

    This is the realistic shape for the synchronous runner: the
    subprocess is reaped at the same moment the runner returns,
    so the cleanup gate is a no-op. The contract is honoured
    even when the kill never lands.
    """

    import tempfile

    cwd = Path(tempfile.mkdtemp(prefix="example-cleanup-cleanup-"))
    (cwd / ".mba-work").mkdir(parents=True, exist_ok=True)
    records_dir = cwd / "records" / "clean-1" / "doer"
    records_dir.mkdir(parents=True, exist_ok=True)
    worker = cwd / "_ok.py"
    worker.write_text(
        "import os, pathlib, sys\n"
        "sd = pathlib.Path(sys.argv[sys.argv.index('--session-dir') + 1]).resolve()\n"
        "sd.mkdir(parents=True, exist_ok=True)\n"
        "(sd / 'working.md').write_text('w', encoding='utf-8')\n"
        "(sd / 'result.md').write_text('r', encoding='utf-8')\n"
        "(sd / 'report.md').write_text('rep', encoding='utf-8')\n"
        "(sd / 'comment.md').write_text('- **result:** ok\\n- **verification:** ok\\n- **next:** done\\n- **details:** Bead comment is complete; no separate file.\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    killed: list[int] = []
    runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(worker)),
        authority=AuthorityContext(
            decision_fn=None,
            project_cwd=cwd,
            trusted_confinement=True,
        ),
        timeout_seconds=15,
        cleanup_is_alive=lambda pid: False,
        cleanup_kill=lambda pid: killed.append(pid),
    )
    brief = lifecycle.SessionBrief(
        bead_id="clean-1",
        session_dir=records_dir,
        session_name="clean-1-doer",
        responsibility="Doer",
        hat="Engineer",
        ai_id="minimax",
        ai_label="minimax",
        pattern="b",
        prompt_path=cwd / "_prompt.md",
        artefact_paths={},
    )
    outcome = runner(brief)
    assert outcome.working_text == "w"
    # The worker is already reaped so the kill is a no-op (the
    # is_alive callable returned False for the captured PID).
    assert killed == []


def test_cleanup_refused_path_routes_to_blocked_handoff() -> None:
    """The cleanup refusal sinks through the record_blocked callable.

    The runtime's default ``record_blocked`` invokes
    ``raise_human_needed`` which writes the structured Bead
    handoff. The gate threads the same callback so the refusal
    reaches the human-visible path without a separate wiring.
    """

    worker = MBAOwnedWorker(
        bead_id="refusal-1",
        session_name="refusal-1-doer",
        pid=42,
    )
    captured: list[tuple[str, str]] = []

    def record(bead_id: str, comment_text: str) -> None:
        captured.append((bead_id, comment_text))

    outcome = cleanup_mba_owned_worker(
        worker,
        is_alive=lambda pid: True,
        kill=lambda pid: None,
        record_blocked=record,
    )
    assert outcome.action == CLEANUP_REFUSED
    assert outcome.blocked_for_human is True
    assert captured, "record_blocked was not invoked"
    bead, comment = captured[0]
    assert bead == "refusal-1"
    assert "refusal-1-doer" in comment or "session" in comment.lower()


def test_mba_owned_worker_identity_binding() -> None:
    """The identity-binding gate is exact: each binding field is
    checkable in isolation.

    The helper accepts a binding from any of the four fields
    (session_id, transcript_id, worker_identity, ownership_proof).
    A record with none of the four is refused.
    """

    base = MBAOwnedWorker(
        bead_id="sample-1",
        session_name="sample-1-doer",
        pid=1,
    )
    assert base.has_identity_binding() is False
    assert MBAOwnedWorker(
        bead_id="sample-1",
        session_name="sample-1-doer",
        pid=1,
        session_id="x",
    ).has_identity_binding() is True
    assert MBAOwnedWorker(
        bead_id="sample-1",
        session_name="sample-1-doer",
        pid=1,
        transcript_id="x",
    ).has_identity_binding() is True
    assert MBAOwnedWorker(
        bead_id="sample-1",
        session_name="sample-1-doer",
        pid=1,
        worker_identity="x",
    ).has_identity_binding() is True
    assert MBAOwnedWorker(
        bead_id="sample-1",
        session_name="sample-1-doer",
        pid=1,
        ownership_proof="x",
    ).has_identity_binding() is True
    # Whitespace-only values are not a binding.
    assert MBAOwnedWorker(
        bead_id="sample-1",
        session_name="sample-1-doer",
        pid=1,
        session_id="   ",
        transcript_id="",
    ).has_identity_binding() is False


# ---------------------------------------------------------------------------
# example-capture: direct file capture (default mode)
# ---------------------------------------------------------------------------


# Worker source that prints NDJSON-like progress to stdout so the
# direct-file capture mode can prove the redirect writes to disk.
NDJSON_WORKER_SOURCE = r"""#!/usr/bin/env python3
import argparse, pathlib, sys

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bead-id", required=True)
    parser.add_argument("--session-name", required=True)
    parser.add_argument("--session-dir", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--allowed-write-root", required=True)
    parser.add_argument("--hat", required=True)
    parser.add_argument("--round", required=True, type=int)
    args = parser.parse_args()

    sd = pathlib.Path(args.session_dir)
    sd.mkdir(parents=True, exist_ok=True)
    # Emit a small NDJSON-ish stream. The adapter redirects stdout
    # directly to run.log, so these lines land in the file in
    # order.
    print('{"type":"step_start","timestamp":1,"sessionID":"s","part":{"type":"step-start"}}', flush=True)
    print('{"type":"tool_use","timestamp":2,"sessionID":"s","part":{"type":"tool","tool":"read","callID":"c1","state":{"status":"completed","input":{"filePath":"/tmp/x"},"output":"hello"}}}', flush=True)
    print('{"type":"step_finish","timestamp":3,"sessionID":"s","part":{"type":"step-finish","tokens":{"total":3,"input":2,"output":1}}}', flush=True)
    sys.stderr.write("boot diagnostics: ok\n")
    (sd / "working.md").write_text("w", encoding="utf-8")
    (sd / "result.md").write_text("r", encoding="utf-8")
    (sd / "comment.md").write_text(
        "- **result:** ok\n- **verification:** ok\n- **next:** done\n- **details:** Bead comment is complete; no separate file.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
"""


def _make_ndjson_worker(tmp_path: Path) -> Path:
    path = tmp_path / "_ndjson_worker.py"
    path.write_text(NDJSON_WORKER_SOURCE, encoding="utf-8")
    return path


def test_external_dispatch_direct_capture_writes_run_log(
    fake_bd_dir: Path, tmp_path: Path
) -> None:
    """example-capture: default mode writes the worker's stdout to run.log.

    The direct-capture path is the canonical one. A subsequent
    restart of any follower can read the same file without
    re-running the worker.
    """

    cwd = fake_bd_dir
    records_dir = tmp_path / ".mba-work" / "example-capture" / "doer"
    worker = _make_ndjson_worker(tmp_path)
    runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(worker)),
        authority=_authority_allow(cwd),
        records_dir=records_dir,
    )
    brief = lifecycle.SessionBrief(
        bead_id="example-capture",
        session_dir=records_dir,
        session_name="doer",
        responsibility="Doer",
        hat="Engineer",
        ai_id="minimax",
        ai_label="minimax",
        pattern="b",
        prompt_path=tmp_path / "_prompt.md",
        artefact_paths={},
    )
    outcome = runner(brief)
    run_log = records_dir / "run.log"
    run_err = records_dir / "run.err"
    assert run_log.exists(), (
        f"run.log missing at {run_log}; records_dir listing="
        f"{sorted(p.name for p in records_dir.iterdir()) if records_dir.exists() else 'records_dir absent'!r}"
    )
    assert run_err.exists()
    text = run_log.read_text(encoding="utf-8")
    assert '"type":"step_start"' in text
    assert '"type":"tool_use"' in text
    assert '"type":"step_finish"' in text
    err_text = run_err.read_text(encoding="utf-8")
    assert "boot diagnostics" in err_text
    # SessionOutcome carries the capture record so the Orchestrator
    # can hand it to the follower.
    assert outcome.capture is not None
    from mba_runtime.stream_capture import StreamCapture

    assert isinstance(outcome.capture, StreamCapture)
    assert outcome.capture.stdout_path == run_log
    assert outcome.capture.stderr_path == run_err


def test_external_dispatch_direct_capture_files_present_even_on_failure(
    fake_bd_dir: Path, tmp_path: Path
) -> None:
    """The capture files are durable on a non-zero exit.

    The runtime surfaces the failure and still leaves the partial
    capture on disk so a later ``mba stream`` invocation can read
    the failure-mode evidence.
    """

    cwd = fake_bd_dir
    records_dir = tmp_path / ".mba-work" / "example-capture" / "fail"
    worker = tmp_path / "_fail_worker.py"
    worker.write_text(
        "import sys, pathlib, argparse\n"
        "parser = argparse.ArgumentParser()\n"
        "for name in ['--bead-id','--session-name','--session-dir','--prompt','--allowed-write-root','--hat']:\n"
        "    parser.add_argument(name, required=True)\n"
        "parser.add_argument('--round', required=True, type=int)\n"
        "args = parser.parse_args()\n"
        "sys.stdout.write('{\"type\":\"step_start\",\"timestamp\":1,\"sessionID\":\"s\",\"part\":{\"type\":\"step-start\"}}\\n')\n"
        "sys.stdout.flush()\n"
        "sys.exit(2)\n",
        encoding="utf-8",
    )
    runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(worker)),
        authority=_authority_allow(cwd),
        records_dir=records_dir,
    )
    brief = lifecycle.SessionBrief(
        bead_id="example-capture",
        session_dir=records_dir,
        session_name="fail",
        responsibility="Doer",
        hat="Engineer",
        ai_id="minimax",
        ai_label="minimax",
        pattern="b",
        prompt_path=tmp_path / "_prompt.md",
        artefact_paths={},
    )
    with pytest.raises(ExternalDispatchError):
        runner(brief)
    # Capture files still on disk and contain the partial line.
    run_log = records_dir / "run.log"
    assert run_log.exists()
    text = run_log.read_text(encoding="utf-8")
    assert "step_start" in text


def test_external_dispatch_pipe_mode_preserves_legacy(
    fake_bd_dir: Path, tmp_path: Path
) -> None:
    """CAPTURE_MODE_PIPE is preserved for tests and opt-out callers."""

    cwd = fake_bd_dir
    records_dir = tmp_path / ".mba-work" / "example-capture" / "pipe"
    worker = _make_ndjson_worker(tmp_path)
    runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(worker)),
        authority=_authority_allow(cwd),
        records_dir=records_dir,
        capture_mode="pipe",
    )
    brief = lifecycle.SessionBrief(
        bead_id="example-capture",
        session_dir=records_dir,
        session_name="pipe",
        responsibility="Doer",
        hat="Engineer",
        ai_id="minimax",
        ai_label="minimax",
        pattern="b",
        prompt_path=tmp_path / "_prompt.md",
        artefact_paths={},
    )
    outcome = runner(brief)
    run_log = records_dir / "run.log"
    # Pipe mode does NOT route the worker's stdout through
    # ``run.log``: the file is pre-created empty by
    # ``_prepare_capture`` so the contract is the same as direct
    # mode, but the worker's NDJSON output never lands there.
    assert run_log.read_text(encoding="utf-8") == ""
    # The capture record still exists with the configured path,
    # so the SessionOutcome contract is identical.
    assert outcome.capture is not None
    assert outcome.capture.stdout_path == run_log


def test_external_dispatch_rejects_unknown_capture_mode(
    fake_bd_dir: Path, tmp_path: Path
) -> None:
    """A typo in ``capture_mode`` fails loudly, never silently."""

    cwd = fake_bd_dir
    records_dir = tmp_path / ".mba-work" / "example-capture" / "bad-mode"
    worker = _make_ndjson_worker(tmp_path)
    runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(worker)),
        authority=_authority_allow(cwd),
        records_dir=records_dir,
        capture_mode="not-a-real-mode",
    )
    brief = lifecycle.SessionBrief(
        bead_id="example-capture",
        session_dir=records_dir,
        session_name="bad-mode",
        responsibility="Doer",
        hat="Engineer",
        ai_id="minimax",
        ai_label="minimax",
        pattern="b",
        prompt_path=tmp_path / "_prompt.md",
        artefact_paths={},
    )
    with pytest.raises(ExternalDispatchError):
        runner(brief)
    # The capture files were pre-created (Popen kwargs open them),
    # but the worker never wrote, so the file is empty.
    run_log = records_dir / "run.log"
    if run_log.exists():
        assert run_log.read_text(encoding="utf-8") == ""


def test_external_dispatch_capture_protocol_is_default_line_text(
    fake_bd_dir: Path, tmp_path: Path
) -> None:
    """The default capture protocol is ``line_text``.

    The example-capture default is conservative: harness-neutral, no
    assumption that the worker is OpenCode. Callers that know
    they have an OpenCode worker override ``capture_protocol``.
    """

    from mba_runtime.stream_capture import PROTOCOL_LINE_TEXT

    cwd = fake_bd_dir
    records_dir = tmp_path / ".mba-work" / "example-capture" / "protocol"
    worker = _make_ndjson_worker(tmp_path)
    runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(worker)),
        authority=_authority_allow(cwd),
        records_dir=records_dir,
    )
    brief = lifecycle.SessionBrief(
        bead_id="example-capture",
        session_dir=records_dir,
        session_name="protocol",
        responsibility="Doer",
        hat="Engineer",
        ai_id="minimax",
        ai_label="minimax",
        pattern="b",
        prompt_path=tmp_path / "_prompt.md",
        artefact_paths={},
    )
    outcome = runner(brief)
    assert outcome.capture is not None
    assert outcome.capture.protocol == PROTOCOL_LINE_TEXT


def test_external_dispatch_capture_protocol_can_be_ndjson(
    fake_bd_dir: Path, tmp_path: Path
) -> None:
    """Operators can declare an OpenCode worker so the follower parses it."""

    from mba_runtime.stream_capture import PROTOCOL_NDJSON

    cwd = fake_bd_dir
    records_dir = tmp_path / ".mba-work" / "example-capture" / "ndjson"
    worker = _make_ndjson_worker(tmp_path)
    runner = ExternalProcessSessionRunner(
        dispatch_argv=(sys.executable, str(worker)),
        authority=_authority_allow(cwd),
        records_dir=records_dir,
        capture_protocol=PROTOCOL_NDJSON,
    )
    brief = lifecycle.SessionBrief(
        bead_id="example-capture",
        session_dir=records_dir,
        session_name="ndjson",
        responsibility="Doer",
        hat="Engineer",
        ai_id="minimax",
        ai_label="minimax",
        pattern="b",
        prompt_path=tmp_path / "_prompt.md",
        artefact_paths={},
    )
    outcome = runner(brief)
    assert outcome.capture is not None
    assert outcome.capture.protocol == PROTOCOL_NDJSON
