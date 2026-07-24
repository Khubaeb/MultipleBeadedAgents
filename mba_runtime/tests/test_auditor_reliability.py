"""Tests for known-fact conflict handling in Auditor ACCEPTs."""

from __future__ import annotations

import json
from pathlib import Path

from mba_runtime import ai_resources, lifecycle
from mba_runtime.ai_resources import (
    AIResource,
    AIResourceRecord,
    ResponsibilityConfig,
    TeamConfig,
)
from mba_runtime.auditor_reliability import (
    EvidenceConflict,
    ReliabilityIncident,
    append_reliability_incident,
    downgrade_accept_on_conflicts,
    render_compact_audit_packet,
    unreliable_model_role_pairs,
)
from mba_runtime.convergence import Verdict
from mba_runtime.lifecycle import DriveConfig, SessionBrief, SessionOutcome


def _populate_record(cwd: Path) -> AIResourceRecord:
    record = AIResourceRecord(
        schema=1,
        note="auditor reliability test fixture",
        resources=(
            AIResource(
                id="minimax",
                label="MiniMax-M3",
                capabilities=("doer", "auditor"),
                session_lifetime="fresh_per_session",
            ),
            AIResource(
                id="glm",
                label="GLM-5.2 Max",
                capabilities=("doer", "auditor"),
                session_lifetime="fresh_per_session",
            ),
        ),
        teams={
            "default": TeamConfig(
                name="default",
                doer=ResponsibilityConfig(
                    ai="minimax", hat="Implementation Lead", session_count=1
                ),
                auditor=ResponsibilityConfig(
                    ai="glm", hat="Workflow Auditor", session_count=1
                ),
                pattern="b",
            )
        },
    )
    ai_resources.save_ai_resource_record(cwd, record)
    return record


def _wire_bead_record(cwd: Path, bead_id: str, monkeypatch) -> None:
    payload = [
        {
            "id": bead_id,
            "title": "Auditor reliability sample",
            "description": "Known-fact conflict must prevent convergence.",
            "acceptance_criteria": "conflicting ACCEPT is unresolved",
            "status": "in_progress",
            "assignee": "Implementation Lead",
            "issue_type": "task",
            "priority": 1,
            "labels": ["mba", "workflow"],
        }
    ]
    record = cwd / "_bead_record.json"
    record.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("BD_RECORD", str(record))


def test_downgrade_accept_on_known_fact_conflict() -> None:
    verdict = Verdict.accept(
        reasons=("accepted",),
        evidence=("Auditor claims user-level .beads was untouched",),
    )
    conflict = EvidenceConflict(
        known_fact="user-level .beads was touched",
        auditor_claim="user-level .beads was untouched",
        evidence=".mba-work/example-022.1/orchestrator/probe.md",
    )

    downgraded = downgrade_accept_on_conflicts(verdict, (conflict,))
    assert downgraded.verdict == "FIND"
    assert "conflicts with known fact" in downgraded.reasons[0]
    assert ".mba-work/example-022.1/orchestrator/probe.md" in downgraded.evidence


def test_drive_bead_conflicting_accept_does_not_converge(
    fake_bd_dir: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    cwd = fake_bd_dir
    bead_id = "sample-conflict"
    record = _populate_record(cwd)
    _wire_bead_record(cwd, bead_id, monkeypatch)
    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    def _doer_runner(brief: SessionBrief) -> SessionOutcome:
        return SessionOutcome(
            working_text="# Doer\n- produced result\n",
            result_text="# Result\n- ok\n",
            verification="result recorded",
            next_state="await auditor",
        )

    def _auditor_runner(brief: SessionBrief) -> SessionOutcome:
        return SessionOutcome(
            working_text="# Auditor\n- accepted but contradicted fact\n",
            result_text=(
                "VERDICT: ACCEPT\n"
                "RESOLUTION: changed\n"
                "EVIDENCE:\n"
                "- user-level .beads was untouched\n"
            ),
            verification="claimed clean",
            next_state="ACCEPT",
        )

    def _conflict_checker(outcome, verdict, brief):
        return (
            EvidenceConflict(
                known_fact="user-level .beads was touched",
                auditor_claim="user-level .beads was untouched",
                evidence="known fact packet: user-level probe log",
            ),
        )

    team = ai_resources.team_config(record)
    result = lifecycle.drive_bead(
        DriveConfig(
            cwd=cwd,
            bead_id=bead_id,
            pattern="b",
            team=team,
            ai_resources=record,
            doer_runner=_doer_runner,
            auditor_runner=_auditor_runner,
            auditor_conflict_checker=_conflict_checker,
            max_doer_auditor_rounds=1,
        )
    )
    assert result.convergence.converged is False
    assert result.closed is False


def test_compact_packet_and_incident_record(tmp_path: Path) -> None:
    packet = render_compact_audit_packet(
        bead_id="example-024.1",
        known_facts=("source Beads unchanged", "user-level .beads touched"),
        artefact_paths=(tmp_path / "result.md",),
        prior_findings=("GLM r1 claim conflicted with probe evidence",),
    )
    assert "Known facts" in packet
    assert "user-level .beads touched" in packet
    assert "If your ACCEPT contradicts" in packet

    incidents = tmp_path / "reliability.jsonl"
    append_reliability_incident(
        incidents,
        ReliabilityIncident(
            bead_id="example-024.1",
            model="glm-5.2-max",
            role="Workflow Auditor",
            issue="false clean claim",
            evidence="known fact packet contradicted result",
        ),
    )
    assert unreliable_model_role_pairs(incidents) == {("glm-5.2-max", "Workflow Auditor")}
