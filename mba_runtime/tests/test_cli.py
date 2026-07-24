"""Smoke tests for the ``mba_runtime`` CLI dispatcher (audit F4).

Every advertised CLI subcommand is exercised end-to-end against the
stub ``bd`` binary (no live project dependency). The tests pin exit
codes and JSON shape so the CLI is covered even when the runtime is
invoked by a shell script.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mba_runtime import ai_resources

PACKAGE_PARENT = Path(__file__).resolve().parents[2]
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))


@pytest.fixture()
def cli_env(fake_bd_dir: Path, monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("BD_COMMENT_LOG", str(fake_bd_dir / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(fake_bd_dir / "closed.log"))
    monkeypatch.setenv("BD_DEP_LISTING", "")
    monkeypatch.setenv("BD_DEP_CYCLES", "")
    # ai_resources fixture lives alongside; populate a default record.
    record = ai_resources.default_record()
    ai_resources.save_ai_resource_record(fake_bd_dir, record)
    return {}


def _run_cli(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke ``python -m mba_runtime <args>`` with ``cwd`` as the cwd.

    ``cwd`` is the first positional parameter (the working directory
    for the subprocess); subsequent positionals are concatenated into
    the CLI argv. ``PYTHONPATH`` is augmented with the repo root so
    the subprocess can resolve the ``mba_runtime`` package when the
    interpreter is invoked outside this repository's working
    directory.
    """

    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    sep = os.pathsep
    if existing:
        extra = str(PACKAGE_PARENT) + sep + existing
    else:
        extra = str(PACKAGE_PARENT)
    env["PYTHONPATH"] = extra
    return subprocess.run(
        [sys.executable, "-m", "mba_runtime", *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd),
        env=env,
    )


def test_cli_route_returns_plan(cli_env, fake_bd_dir: Path) -> None:
    proc = _run_cli(fake_bd_dir,
        "route",
        "--bead-id",
        "sample",
        "--team",
        "default",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["bead_id"] == "sample"
    assert payload["pattern"] == "b"
    assert len(payload["doer_sessions"]) == 1
    assert len(payload["auditor_sessions"]) == 1
    assert payload["doer_sessions"][0]["hat"] == "Engineer"
    assert payload["auditor_sessions"][0]["hat"] == "Workflow Auditor"


def test_cli_first_contact_ready_when_resources_present(
    cli_env, fake_bd_dir: Path
) -> None:
    proc = _run_cli(fake_bd_dir, "first-contact")

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["activation"]["orchestrator_mode"] is True
    assert payload["activation"]["not_a_daemon"] is True
    assert payload["resource_preflight"]["ok"] is True


def test_cli_first_contact_returns_questions_when_resources_missing(
    tmp_path: Path,
) -> None:
    proc = _run_cli(tmp_path, "first-contact")

    assert proc.returncode == 4, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["activation"]["orchestrator_mode"] is True
    assert payload["resource_preflight"]["ok"] is False
    assert payload["resource_preflight"]["questions"]
    setup = payload["recommended_setup_bead"]
    assert setup["title"] == "MBA setup"
    assert setup["type"] == "task"
    assert setup["status"] == "blocked"
    assert setup["assignee"] == "Human"
    assert set(("mba", "setup", "human")) <= set(setup["labels"])
    assert setup["action"] == "create_or_update"
    assert setup["allowed_now"] == [
        "create_or_update_setup_bead",
        "post_setup_questions",
    ]
    assert setup["blocked_until_ready"] == [
        "create_or_drive_executable_beads",
        "launch_workers",
    ]
    assert "MBA setup" in payload["next"]
    assert "stop" in payload["next"]


def test_cli_first_contact_plain_missing_is_read_only(
    fake_bd_dir: Path, monkeypatch
) -> None:
    issue_log = fake_bd_dir / "issues.json"
    issue_log.write_text(
        json.dumps(
            [
                {
                    "id": "existing-1",
                    "title": "Unrelated",
                    "status": "open",
                    "assignee": "Engineer",
                    "labels": ["existing"],
                }
            ]
        ),
        encoding="utf-8",
    )
    comment_log = fake_bd_dir / "comments.log"
    monkeypatch.setenv("BD_ISSUE_LOG", str(issue_log))
    monkeypatch.setenv("BD_COMMENT_LOG", str(comment_log))
    before = issue_log.read_bytes()

    proc = _run_cli(fake_bd_dir, "first-contact")

    assert proc.returncode == 4, proc.stderr
    assert issue_log.read_bytes() == before
    assert not comment_log.exists()
    payload = json.loads(proc.stdout)
    assert "setup_handoff" not in payload


def test_cli_first_contact_apply_setup_creates_blocked_handoff(
    fake_bd_dir: Path, monkeypatch
) -> None:
    issue_log = fake_bd_dir / "issues.json"
    issue_log.write_text("[]", encoding="utf-8")
    comment_log = fake_bd_dir / "comments.log"
    monkeypatch.setenv("BD_ISSUE_LOG", str(issue_log))
    monkeypatch.setenv("BD_COMMENT_LOG", str(comment_log))

    proc = _run_cli(fake_bd_dir, "first-contact", "--apply-setup")

    assert proc.returncode == 4, proc.stderr
    payload = json.loads(proc.stdout)
    handoff = payload["setup_handoff"]
    assert handoff["applied"] is True
    assert handoff["created"] is True
    assert handoff["bead_id"] == "stub-1"
    issues = json.loads(issue_log.read_text(encoding="utf-8"))
    assert len(issues) == 1
    issue = issues[0]
    assert issue["status"] == "blocked"
    assert issue["assignee"] == "Human"
    assert set(("mba", "setup", "human")) <= set(issue["labels"])
    assert issue["created_by"] == "Orchestrator"
    assert issue["updated_by"] == "Orchestrator"
    rows = comment_log.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    bead_id, actor, comment_path = rows[0].split("\t")
    assert bead_id == "stub-1"
    assert actor == "Orchestrator"
    comment = Path(comment_path).read_text(encoding="utf-8")
    assert "**questions:**" in comment
    assert 4 <= len([line for line in comment.splitlines() if line.strip()]) <= 16


def test_cli_first_contact_apply_setup_reuses_existing_handoff(
    fake_bd_dir: Path, monkeypatch
) -> None:
    issue_log = fake_bd_dir / "issues.json"
    issue_log.write_text(
        json.dumps(
            [
                {
                    "id": "existing-1",
                    "title": "MBA setup",
                    "status": "open",
                    "assignee": "Engineer",
                    "labels": ["old"],
                }
            ]
        ),
        encoding="utf-8",
    )
    comment_log = fake_bd_dir / "comments.log"
    monkeypatch.setenv("BD_ISSUE_LOG", str(issue_log))
    monkeypatch.setenv("BD_COMMENT_LOG", str(comment_log))

    proc = _run_cli(fake_bd_dir, "first-contact", "--apply-setup")

    assert proc.returncode == 4, proc.stderr
    payload = json.loads(proc.stdout)
    handoff = payload["setup_handoff"]
    assert handoff["applied"] is True
    assert handoff["created"] is False
    assert handoff["bead_id"] == "existing-1"
    issues = json.loads(issue_log.read_text(encoding="utf-8"))
    assert len(issues) == 1
    issue = issues[0]
    assert issue["status"] == "blocked"
    assert issue["assignee"] == "Human"
    assert set(("mba", "setup", "human")) <= set(issue["labels"])
    assert issue["updated_by"] == "Orchestrator"
    row = comment_log.read_text(encoding="utf-8").splitlines()[0].split("\t")
    assert row[0] == "existing-1"
    assert row[1] == "Orchestrator"


def test_cli_first_contact_apply_setup_ready_is_read_only(
    cli_env, fake_bd_dir: Path, monkeypatch
) -> None:
    issue_log = fake_bd_dir / "issues.json"
    issue_log.write_text(
        json.dumps(
            [
                {
                    "id": "existing-1",
                    "title": "MBA setup",
                    "status": "blocked",
                    "assignee": "Human",
                    "labels": ["mba", "setup", "human"],
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BD_ISSUE_LOG", str(issue_log))
    before = issue_log.read_bytes()

    proc = _run_cli(fake_bd_dir, "first-contact", "--apply-setup")

    assert proc.returncode == 0, proc.stderr
    assert issue_log.read_bytes() == before
    payload = json.loads(proc.stdout)
    handoff = payload["setup_handoff"]
    assert handoff["applied"] is False
    assert handoff["skipped"] is True
    assert handoff["skipped_reason"]
    assert not (fake_bd_dir / ".mba-work" / "_setup-runtime").exists()


def test_cli_resources_emits_ordered_fallback(cli_env, fake_bd_dir: Path) -> None:

    """`resources` surfaces the ordered suitable-resource fallback."""

    proc = _run_cli(
        fake_bd_dir,
        "resources",
        "--responsibility",
        "doer",
        "--team",
        "default",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["responsibility"] == "Doer"
    assert payload["suitable_order"] == ["minimax", "claude"]
    assert payload["bounds"]["max_scheduled_retries"] == 3

    proc = _run_cli(
        fake_bd_dir,
        "resources",
        "--responsibility",
        "auditor",
        "--team",
        "default",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["suitable_order"] == ["claude", "minimax"]


def test_cli_route_uses_supplied_bead_id_not_residual_state(
    cli_env, fake_bd_dir: Path
) -> None:
    """Audit F4: prior implementation referenced ``result.bead_id``.

    Ensure the rebuilt CLI surfaces ``args.bead_id`` directly; the
    previous reference was an AttributeError on
    :class:`ConvergenceResult`.
    """

    proc = _run_cli(fake_bd_dir,
        "convergence",
        "check",
        "--bead-id",
        "explicit-bead-1",
    )
    assert proc.returncode in (0, 4), proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["bead_id"] == "explicit-bead-1"
    assert "converged" in payload
    assert "rounds" in payload
    assert "final_verdict" in payload


def test_cli_graph_verify_requires_for_issue_id(cli_env, fake_bd_dir: Path) -> None:
    """Audit F5 fix: ``graph verify`` accepts ``--for-issue-id``.

    Without an id, on bd 1.0.4 the bare ``bd dep list`` exits 1 with a
    usage error. The CLI accepts the id so the verification is
    usable. The fixed bd returncode doesn't matter for this CLI
    smoke (stub); the assertion is that the CLI accepts the new flag
    without a noisy argparse error.
    """

    proc = _run_cli(fake_bd_dir,
        "graph",
        "verify",
        "--for-issue-id",
        "explicit-bead-1",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert "cycles_present" in payload
    assert "list_returncode" in payload
    assert "cycles_returncode" in payload


def test_cli_comment_render_outputs_5_lines(cli_env, fake_bd_dir: Path) -> None:
    proc = _run_cli(fake_bd_dir,
        "comment",
        "render",
        "--role",
        "Engineer",
        "--result",
        "Doer produced working.md + result.md",
        "--change",
        "working.md: 5 lines",
        "--change",
        "result.md: 4 lines",
        "--verification",
        "read-back verifier passed",
        "--next",
        "await auditor review",
        "--path-tail",
        ".mba-work/sample-engineer/working.md",
    )
    assert proc.returncode == 0, proc.stderr
    body = proc.stdout
    non_blank = [ln for ln in body.splitlines() if ln.strip()]
    assert 4 <= len(non_blank) <= 5, non_blank


def test_cli_gate_refuses_self_approval(
    cli_env, fake_bd_dir: Path, tmp_path: Path, monkeypatch
) -> None:
    """Audit F6 / authority invariant: the CLI must NOT invent user approval.

    With ``--refuse-if-missing`` and an absent decision file, the
    CLI must raise ``UserAuthorityRequired``. Without
    ``--refuse-if-missing``, the CLI writes a refusal record but
    still returns ``3`` (refusal exit).
    """

    refuse_path = tmp_path / "refuse.jsonl"

    proc = _run_cli(fake_bd_dir,
        "gate",
        "bd_dolt_push",
        "--decision-file",
        str(refuse_path),
        "--refuse-if-missing",
    )
    assert proc.returncode != 0
    assert "refuses" in proc.stderr.lower() or "missing" in proc.stderr.lower()

    # Without ``--refuse-if-missing`` the CLI writes a refusal row
    # and exits 3 — never 0, because no human-origin decision exists.
    refusal_only_path = tmp_path / "refusal_only.jsonl"
    proc = _run_cli(fake_bd_dir,
        "gate",
        "bd_dolt_pull",
        "--decision-file",
        str(refusal_only_path),
    )
    assert proc.returncode == 3, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["approved"] is False
    assert "cli" not in payload["actor"].lower() or "refused" in payload["rationale"].lower()


def test_cli_gate_records_human_origin_decision(
    cli_env, fake_bd_dir: Path, tmp_path: Path
) -> None:
    """A pre-recorded human-origin approval rounds-trips through the CLI."""

    decision_file = tmp_path / "decisions.jsonl"
    decision = {
        "action": "source_git_commit",
        "actor": "human:user",
        "rationale": "manual approval",
        "approved": True,
        "recorded_at": "2026-07-20T06:00:00Z",
    }
    decision_file.parent.mkdir(parents=True, exist_ok=True)
    decision_file.write_text(
        json.dumps(decision, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    proc = _run_cli(fake_bd_dir,
        "gate",
        "source_git_commit",
        "--decision-file",
        str(decision_file),
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["approved"] is True
    assert payload["actor"] == "human:user"


def test_cli_drive_bead_refuses_with_unrecognised_verdict(
    cli_env, fake_bd_dir: Path, monkeypatch, tmp_path: Path
) -> None:
    """``drive-bead`` CLI smoke against the stub ``bd`` (no live project).

    The CLI runs against the ``fake_bd_dir`` stub and the surface
    shape is verified (JSON emission, exit code in the documented
    range, no live-project reach). The default in-process runners
    produce a descriptive ``next_state`` string the Auditor bridge
    cannot map onto a verdict; the integration test asserts the
    loud refusal (audit F7 invariant: explicit verdicts only).
    """

    record_path = fake_bd_dir / "_drive_record.json"
    record_path.write_text(
        json.dumps(
            [
                {
                    "id": "sample-cli",
                    "title": "CLI smoke",
                    "description": "Drive from the CLI.",
                    "notes": "",
                    "design": "",
                    "acceptance_criteria": "every AC row passes",
                    "labels": ["implementation", "mba", "workflow"],
                    "status": "in_progress",
                    "assignee": "Engineer",
                    "issue_type": "task",
                    "priority": 1,
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BD_RECORD", str(record_path))
    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "cli_comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "cli_closed.log"))

    proc = _run_cli(fake_bd_dir,
        "drive-bead",
        "--bead-id",
        "sample-cli",
        "--pattern",
        "b",
        "--team",
        "default",
    )
    # The default runners emit a descriptive ``next_state``; the
    # Auditor bridge refuses unrecognised verdicts (F7). The error
    # surface is itself the AC pin: loud refusal, no silent fallback.
    assert proc.returncode == 99
    assert "not a recognised verdict" in proc.stderr.lower()


WORKER_SOURCE_CLI = r"""#!/usr/bin/env python3
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
    if args.hat == "Engineer":
        working = f"# Doer ({args.session_name})\n- produced working.md\n"
        result = f"# Doer result ({args.session_name})\n- ok\n"
        comment = (
            "- **result:** Doer produced the requested result\n"
            "- **verification:** read-back verified\n"
            "- **next:** ready for review\n"
            f"- **details:** `.mba-work/{args.bead_id}/{args.session_name}/working.md`\n"
        )
    else:
        working = f"# Auditor ({args.session_name})\n- audit ok\n"
        result = (
            f"# Auditor result ({args.session_name})\n"
            "\n"
            "VERDICT: ACCEPT\n"
            "RESOLUTION: changed\n"
            "EVIDENCE:\n"
            "- working.md contents cross-checked against AC\n"
            "- result.md read-back matches Doer's claim\n"
        )
        comment = (
            "- **result:** Auditor accepted the artefact\n"
            "- **verification:** cross-checked\n"
            "- **next:** ready for closure\n"
            f"- **details:** `.mba-work/{args.bead_id}/{args.session_name}/working.md`\n"
        )
        (session_dir / "_verdict.txt").write_text("ACCEPT", encoding="utf-8")
    (session_dir / "working.md").write_text(working, encoding="utf-8")
    (session_dir / "result.md").write_text(result, encoding="utf-8")
    (session_dir / "comment.md").write_text(comment, encoding="utf-8")


if __name__ == "__main__":
    main()
"""


def test_cli_drive_bead_external_dispatch_path(
    cli_env, fake_bd_dir: Path, monkeypatch, tmp_path: Path
) -> None:
    """Public CLI provokes the ExternalProcessSessionRunner via ``--dispatch-worker``.

    The CLI passes a pre-existing human-origin decisions file; the
    dispatcher reads it (does NOT modify it) and the subprocess is
    spawned for every required session. The decisions file is
    asserted unmodified at the end of the test.
    """

    record_path = fake_bd_dir / "_drive_record.json"
    record_path.write_text(
        json.dumps(
            [
                {
                    "id": "cli-dispatch-1",
                    "title": "CLI dispatch smoke",
                    "description": "Verify the CLI dispatches via the external adapter.",
                    "notes": "",
                    "design": "",
                    "acceptance_criteria": "every AC row passes",
                    "labels": ["implementation", "mba", "workflow"],
                    "status": "in_progress",
                    "assignee": "Engineer",
                    "issue_type": "task",
                    "priority": 1,
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BD_RECORD", str(record_path))
    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "cli_dispatch_comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "cli_dispatch_closed.log"))

    decisions_path = tmp_path / "decisions.jsonl"
    decisions_path.write_text(
        json.dumps(
            {
                "action": "external_dispatch_unconfinement",
                "actor": "human:user",
                "rationale": "approved for cli-dispatch smoke",
                "approved": True,
                "recorded_at": "2026-07-20T04:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    decisions_before = decisions_path.read_bytes()

    worker = tmp_path / "_dispatch_worker.py"
    worker.write_text(WORKER_SOURCE_CLI, encoding="utf-8")

    proc = _run_cli(fake_bd_dir,
        "drive-bead",
        "--bead-id",
        "cli-dispatch-1",
        "--pattern",
        "b",
        "--team",
        "default",
        "--dispatch-worker",
        str(worker),
        "--dispatch-decision-file",
        str(decisions_path),
    )
    assert proc.returncode in (0, 2), proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["bead_id"] == "cli-dispatch-1"
    assert payload["convergence"]["converged"] is True

    # The worker (a real subprocess via the external adapter) wrote
    # its artefacts inside the disposable cwd; nothing landed in
    # the live project.
    for session_dir_name in (
        "cli-dispatch-1-engineer",
        "cli-dispatch-1-workflow-auditor",
    ):
        path = fake_bd_dir / ".mba-work" / "cli-dispatch-1" / session_dir_name
        assert (path / "working.md").exists()
        assert (path / "result.md").exists()
        assert (path / "comment.md").exists()

    # The CLI does NOT modify the decisions file.
    assert decisions_path.read_bytes() == decisions_before


# ---------------------------------------------------------------------------
# Authority refusal tests (resume-2 / G1 corrections)
# ---------------------------------------------------------------------------


def _populate_drive_record(cwd: Path, bead_id: str = "cli-dispatch-1") -> Path:
    record = cwd / "_drive_record.json"
    record.write_text(
        json.dumps(
            [
                {
                    "id": bead_id,
                    "title": "CLI dispatch smoke",
                    "description": "Refusal test fixture.",
                    "notes": "",
                    "design": "",
                    "acceptance_criteria": "every AC row passes",
                    "labels": ["implementation", "mba", "workflow"],
                    "status": "in_progress",
                    "assignee": "Engineer",
                    "issue_type": "task",
                    "priority": 1,
                }
            ]
        ),
        encoding="utf-8",
    )
    return record


def test_cli_dispatch_refuses_without_decision_file(
    cli_env, fake_bd_dir: Path, monkeypatch, tmp_path: Path
) -> None:
    """G1: missing decision file ⇢ no spawn.

    Without ``--dispatch-decision-file`` the CLI passes no
    decision_fn; the dispatcher refuses before launch.
    """

    _populate_drive_record(fake_bd_dir)
    monkeypatch.setenv("BD_RECORD", str(fake_bd_dir / "_drive_record.json"))
    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    worker = tmp_path / "_w.py"
    worker.write_text(
        "import argparse, pathlib\n"
        "parser = argparse.ArgumentParser()\n"
        "for name in ['bead-id', 'session-name', 'session-dir', 'prompt',\n"
        "             'hat', 'round', 'allowed-write-root']:\n"
        "    parser.add_argument('--' + name, required=True)\n"
        "args = parser.parse_args()\n"
        "p = pathlib.Path(args.session_dir); p.mkdir(parents=True, exist_ok=True)\n"
        "(p / 'working.md').write_text('w', encoding='utf-8')\n"
        "(p / 'result.md').write_text('r', encoding='utf-8')\n"
        "(p / 'comment.md').write_text('- ok\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )

    proc = _run_cli(fake_bd_dir,
        "drive-bead",
        "--bead-id",
        "cli-dispatch-1",
        "--pattern",
        "b",
        "--team",
        "default",
        "--dispatch-worker",
        str(worker),
    )
    assert proc.returncode != 0
    # The runtime refuses the gate when ``--dispatch-decision-file``
    # is omitted, producing this characteristically explicit error.
    lowered = proc.stderr.lower()
    assert (
        "refused" in lowered
        and "external_dispatch_unconfinement" in lowered
    )


def test_cli_dispatch_refuses_when_decision_file_missing(
    cli_env, fake_bd_dir: Path, monkeypatch, tmp_path: Path
) -> None:
    """G1: ``--dispatch-decision-file <missing>`` ⇢ no spawn."""

    _populate_drive_record(fake_bd_dir)
    monkeypatch.setenv("BD_RECORD", str(fake_bd_dir / "_drive_record.json"))
    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    worker = tmp_path / "_w.py"
    worker.write_text("# placeholder\n", encoding="utf-8")

    proc = _run_cli(fake_bd_dir,
        "drive-bead",
        "--bead-id",
        "cli-dispatch-1",
        "--pattern",
        "b",
        "--team",
        "default",
        "--dispatch-worker",
        str(worker),
        "--dispatch-decision-file",
        str(tmp_path / "no_such_file.jsonl"),
    )
    assert proc.returncode != 0
    assert "missing" in proc.stderr.lower()


def test_cli_dispatch_refuses_when_decision_file_empty(
    cli_env, fake_bd_dir: Path, monkeypatch, tmp_path: Path
) -> None:
    """G1: empty decision file ⇢ no spawn."""

    _populate_drive_record(fake_bd_dir)
    monkeypatch.setenv("BD_RECORD", str(fake_bd_dir / "_drive_record.json"))
    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    decisions = tmp_path / "empty.jsonl"
    decisions.write_text("", encoding="utf-8")

    worker = tmp_path / "_w.py"
    worker.write_text("# placeholder\n", encoding="utf-8")

    proc = _run_cli(fake_bd_dir,
        "drive-bead",
        "--bead-id",
        "cli-dispatch-1",
        "--pattern",
        "b",
        "--team",
        "default",
        "--dispatch-worker",
        str(worker),
        "--dispatch-decision-file",
        str(decisions),
    )
    assert proc.returncode != 0
    assert "empty" in proc.stderr.lower()


def test_cli_dispatch_refuses_non_human_origin_actor(
    cli_env, fake_bd_dir: Path, monkeypatch, tmp_path: Path
) -> None:
    """G1: actor = ``cli`` / AI / role / default ⇢ refused."""

    _populate_drive_record(fake_bd_dir)
    monkeypatch.setenv("BD_RECORD", str(fake_bd_dir / "_drive_record.json"))
    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    for bad_actor in ("cli", "decision_fn_default", "Engineer", "minimax"):
        decisions = tmp_path / f"bad-{bad_actor}.jsonl"
        decisions.write_text(
            json.dumps(
                {
                    "action": "external_dispatch_unconfinement",
                    "actor": bad_actor,
                    "rationale": f"claiming {bad_actor} is human-origin",
                    "approved": True,
                    "recorded_at": "2026-07-20T04:00:00Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        worker = tmp_path / f"_w-{bad_actor}.py"
        worker.write_text("# placeholder\n", encoding="utf-8")
        proc = _run_cli(fake_bd_dir,
            "drive-bead",
            "--bead-id",
            "cli-dispatch-1",
            "--pattern",
            "b",
            "--team",
            "default",
            "--dispatch-worker",
            str(worker),
            "--dispatch-decision-file",
            str(decisions),
        )
        assert proc.returncode != 0, (
            f"actor {bad_actor!r} should have been refused"
        )


@pytest.mark.parametrize(
    "empty_actor",
    [
        "human:",            # truncated — nothing after the colon
        "human:   ",         # only whitespace after the colon
        "Human:",            # wrong case (must match the literal "human:" prefix)
        "human:   \t  \n",   # mixed whitespace
    ],
)
def test_cli_dispatch_refuses_empty_or_whitespace_human_identity(
    cli_env,
    fake_bd_dir: Path,
    monkeypatch,
    tmp_path: Path,
    empty_actor: str,
) -> None:
    """Resume-3 edge: ``human:`` with no identity ⇢ refused, file unchanged.

    The gate's human-origin validator requires at least one
    non-whitespace character after the literal ``human:`` prefix.
    An actor like ``human:`` or ``human:   `` is a malformed
    identity — the dispatcher refuses before launch and the CLI
    must NOT modify the supplied decisions file.
    """

    _populate_drive_record(fake_bd_dir)
    monkeypatch.setenv("BD_RECORD", str(fake_bd_dir / "_drive_record.json"))
    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    decisions = tmp_path / "empty_human.jsonl"
    decisions.write_text(
        json.dumps(
            {
                "action": "external_dispatch_unconfinement",
                "actor": empty_actor,
                "rationale": "claiming empty / whitespace human identity",
                "approved": True,
                "recorded_at": "2026-07-20T04:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    decisions_before = decisions.read_bytes()

    worker = tmp_path / "_w.py"
    worker.write_text("# placeholder\n", encoding="utf-8")
    proc = _run_cli(fake_bd_dir,
        "drive-bead",
        "--bead-id",
        "cli-dispatch-1",
        "--pattern",
        "b",
        "--team",
        "default",
        "--dispatch-worker",
        str(worker),
        "--dispatch-decision-file",
        str(decisions),
    )
    assert proc.returncode != 0, (
        f"actor {empty_actor!r} should have been refused"
    )
    # The decisions file MUST NOT have been modified.
    assert decisions.read_bytes() == decisions_before, (
        "the dispatcher must not write to the decisions file"
    )


def test_cli_dispatch_refuses_refused_decision(
    cli_env, fake_bd_dir: Path, monkeypatch, tmp_path: Path
) -> None:
    """G1: a recorded refusal (approved=False) is not an approval."""

    _populate_drive_record(fake_bd_dir)
    monkeypatch.setenv("BD_RECORD", str(fake_bd_dir / "_drive_record.json"))
    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    decisions = tmp_path / "refused.jsonl"
    decisions.write_text(
        json.dumps(
            {
                "action": "external_dispatch_unconfinement",
                "actor": "human:user",
                "rationale": "I refuse to authorise dispatch",
                "approved": False,
                "recorded_at": "2026-07-20T04:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    worker = tmp_path / "_w.py"
    worker.write_text("# placeholder\n", encoding="utf-8")
    proc = _run_cli(fake_bd_dir,
        "drive-bead",
        "--bead-id",
        "cli-dispatch-1",
        "--pattern",
        "b",
        "--team",
        "default",
        "--dispatch-worker",
        str(worker),
        "--dispatch-decision-file",
        str(decisions),
    )
    assert proc.returncode != 0


def test_cli_dispatch_refuses_malformed_decision_record(
    cli_env, fake_bd_dir: Path, monkeypatch, tmp_path: Path
) -> None:
    """G1: malformed JSONL rows are skipped; refusal if no valid row."""

    _populate_drive_record(fake_bd_dir)
    monkeypatch.setenv("BD_RECORD", str(fake_bd_dir / "_drive_record.json"))
    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    decisions = tmp_path / "malformed.jsonl"
    decisions.write_text(
        json.dumps({"action": "external_dispatch_unconfinement"})
        + "\n",
        encoding="utf-8",
    )
    worker = tmp_path / "_w.py"
    worker.write_text("# placeholder\n", encoding="utf-8")
    proc = _run_cli(fake_bd_dir,
        "drive-bead",
        "--bead-id",
        "cli-dispatch-1",
        "--pattern",
        "b",
        "--team",
        "default",
        "--dispatch-worker",
        str(worker),
        "--dispatch-decision-file",
        str(decisions),
    )
    assert proc.returncode != 0


def test_cli_dispatch_accepts_pre_existing_human_origin_decision(
    cli_env, fake_bd_dir: Path, monkeypatch, tmp_path: Path
) -> None:
    """G1: a pre-existing approved ``human:user`` row persists the dispatch.

    The CLI does NOT modify the file.
    """

    _populate_drive_record(fake_bd_dir)
    monkeypatch.setenv("BD_RECORD", str(fake_bd_dir / "_drive_record.json"))
    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    decisions = tmp_path / "ok.jsonl"
    decisions.write_text(
        json.dumps(
            {
                "action": "external_dispatch_unconfinement",
                "actor": "human:user",
                "rationale": "manually approved for cli-dispatch smoke",
                "approved": True,
                "recorded_at": "2026-07-20T04:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    decisions_before = decisions.read_bytes()

    worker = tmp_path / "_ok_worker.py"
    worker.write_text(WORKER_SOURCE_CLI, encoding="utf-8")

    proc = _run_cli(fake_bd_dir,
        "drive-bead",
        "--bead-id",
        "cli-dispatch-1",
        "--pattern",
        "b",
        "--team",
        "default",
        "--dispatch-worker",
        str(worker),
        "--dispatch-decision-file",
        str(decisions),
    )
    assert proc.returncode in (0, 2), proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["convergence"]["converged"] is True

    # The CLI does NOT touch the decisions file.
    assert decisions.read_bytes() == decisions_before


def test_cli_does_not_expose_trusted_confinement_bypass(
    cli_env, fake_bd_dir: Path
) -> None:
    """Trusted confinement is an internal capability only.

    The generic public CLI does not expose
    ``--trust-host-confinement`` (no flag of that name exists; the
    Capability Record Audit invariant forbids a generic CLI bypass).
    """

    proc = subprocess.run(
        [sys.executable, "-m", "mba_runtime", "drive-bead", "--help"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(fake_bd_dir),
    )
    assert "trust-host-confinement" not in proc.stdout.lower()
    assert "trusted-confinement" not in proc.stdout.lower()
