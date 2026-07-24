"""example-015.1 acceptance: evidence-required convergence.

This module exercises the six scenarios from the Bead's test matrix.
Each row fails on the prior implementation (example-014.1) where the
runtime could synthesise ``ACCEPT`` evidence from thin air, and
passes after the example-015.1 evidence-required refactor.

| Scenario | Expected |
|---|---|
| ``_verdict.txt = ACCEPT``, no ``EVIDENCE`` section | not converged / unresolved FIND |
| FIND → ACCEPT, artefact hash changed, evidence references it | converged |
| FIND → ACCEPT, artefact unchanged, ``RESOLUTION: no-change-proof`` + evidence | converged |
| FIND → ACCEPT, artefact unchanged, no ``no-change-proof`` | not converged |
| Round-0 ACCEPT, real Auditor evidence | converged |
| Multi-auditor: all ACCEPT but one lacks evidence | not converged |

The first row is tested at the bridge level
(:func:`mba_runtime.lifecycle._outcome_to_verdict`); the next four
exercise :func:`mba_runtime.convergence.iterate_until_converged`
directly; the last row exercises
:func:`mba_runtime.lifecycle._combine_auditor_verdicts` so a
pattern-(c) roster where one Auditor returns an evidence-less
``ACCEPT`` is downgraded to ``FIND`` and the round does not
converge.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mba_runtime.convergence import (
    ConvergenceError,
    DoerOutcome,
    DoerRoundContext,
    AuditorRoundContext,
    Verdict,
    iterate_until_converged,
    parse_auditor_protocol,
)
from mba_runtime.lifecycle import (
    DriveConfig,
    SessionBrief,
    SessionOutcome,
    _combine_auditor_verdicts,
    _outcome_to_verdict,
)
from mba_runtime import ai_resources, lifecycle
from mba_runtime.ai_resources import (
    AIResource,
    AIResourceRecord,
    ResponsibilityConfig,
    TeamConfig,
)


# ---------------------------------------------------------------------------
# Local fixtures for the end-to-end drive_bead tests. The same
# pattern lives in ``test_disposable_repo.py``; this copy keeps the
# proof-convergence tests self-contained.
# ---------------------------------------------------------------------------


def _populate_pattern_b_record(cwd: Path) -> AIResourceRecord:
    """Populate ``.mba-work/.ai-resources.json`` with a pattern-(b) team."""

    record = AIResourceRecord(
        schema=1,
        note="proof-convergence test fixture",
        resources=tuple(
            AIResource(
                id=ai,
                label=ai.upper(),
                capabilities=("doer", "auditor"),
                session_lifetime="fresh_per_session",
            )
            for ai in ("minimax", "claude")
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


def _wire_bead_record(
    monkeypatch: pytest.MonkeyPatch, cwd: Path, bead_id: str
) -> Path:
    """Write a fake ``bd show`` payload file and point ``BD_RECORD`` at it."""

    import json

    record = cwd / "_bead_record.json"
    record.write_text(
        json.dumps(
            [
                {
                    "id": bead_id,
                    "title": "Proof convergence sample",
                    "description": (
                        "End-to-end test for evidence-required convergence."
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
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BD_RECORD", str(record))
    return record


# ---------------------------------------------------------------------------
# Scenario 1: ACCEPT without EVIDENCE block degrades to FIND.
# ---------------------------------------------------------------------------


def _auditor_outcome_no_evidence(bead_id: str, session_name: str) -> SessionOutcome:
    """A side-channel ``ACCEPT`` (no EVIDENCE block) — scenario 1."""

    return SessionOutcome(
        working_text="# Auditor working\n",
        result_text=(
            f"# Auditor result - {session_name}\n"
            "\n"
            "- verdict: ACCEPT (round 0)\n"
            # NB: no VERDICT / RESOLUTION / EVIDENCE block; the
            # bridge must downgrade the side-channel ACCEPT to
            # FIND because the Auditor failed to provide evidence.
        ),
        verification="Auditor: cross-checked without evidence",
        next_state="ACCEPT",
    )


def test_outcome_to_verdict_accept_without_evidence_block_degrades_to_find() -> None:
    """Scenario 1: ``ACCEPT`` with no ``EVIDENCE`` block ⇒ FIND.

    The bridge :func:`_outcome_to_verdict` is the canonical place
    that downgrades an Auditor-side ``ACCEPT`` whose result text
    lacks the structured ``EVIDENCE`` block. The runtime never
    backfills the evidence; a missing-evidence ``ACCEPT`` is not
    convergence (Charter §8 + example-015.1).
    """

    outcome = _auditor_outcome_no_evidence("bead-1", "bead-1-workflow-auditor")
    verdict = _outcome_to_verdict(outcome, round_index=0)
    assert verdict.verdict == "FIND"
    assert "without" in verdict.reasons[0].lower() and (
        "EVIDENCE" in verdict.reasons[0]
    )
    # The Auditor's evidence chain is empty — the runtime did not
    # synthesise one.
    assert verdict.evidence == ()


def test_outcome_to_verdict_accept_with_evidence_block_returns_accept() -> None:
    """The positive control for scenario 1."""

    outcome = SessionOutcome(
        working_text="# Auditor working\n",
        result_text=(
            "# Auditor result\n"
            "\n"
            "VERDICT: ACCEPT\n"
            "RESOLUTION: changed\n"
            "EVIDENCE:\n"
            "- working.md cross-checked\n"
            "- result.md read-back matches Doer claim\n"
        ),
        verification="Auditor: cross-checked",
        next_state="ACCEPT",
        changes=("read-back verified",),
    )
    verdict = _outcome_to_verdict(outcome, round_index=0)
    assert verdict.verdict == "ACCEPT"
    assert verdict.evidence == (
        "working.md cross-checked",
        "result.md read-back matches Doer claim",
    )
    assert verdict.resolution == "changed"


def test_outcome_to_verdict_raises_on_conflicting_verdict_signal() -> None:
    """A mismatch between ``next_state`` and structured ``VERDICT:`` raises.

    The bridge prefers the structured ``VERDICT`` when present; a
    conflict between the two is a misbehaving runner, not a
    graceful ambiguity. The runtime fails loudly so the audit can
    see the discrepancy.
    """

    outcome = SessionOutcome(
        working_text="# Auditor working\n",
        result_text=(
            "VERDICT: BLOCKED\n"
            "EVIDENCE:\n"
            "- the artefact is malformed\n"
        ),
        next_state="ACCEPT",
        verification="contradictory verdict",
    )
    with pytest.raises(Exception, match="conflict"):
        _outcome_to_verdict(outcome, round_index=0)


def test_parse_auditor_protocol_extracts_evidence_resolution_verdict() -> None:
    """The parser is permissive: missing fields return ``None`` / empty."""

    parsed = parse_auditor_protocol(
        "VERDICT: ACCEPT\n"
        "RESOLUTION: no-change-proof\n"
        "EVIDENCE:\n"
        "- cross-checked against AC\n"
        "* bullet-style evidence\n"
        "no-bullet evidence line\n"
    )
    assert parsed.verdict == "ACCEPT"
    assert parsed.resolution == "no-change-proof"
    assert parsed.evidence == (
        "cross-checked against AC",
        "bullet-style evidence",
        "no-bullet evidence line",
    )
    assert parsed.reasons == ()


def test_parse_auditor_protocol_empty_text_returns_empty_protocol() -> None:
    """The parser never raises on empty / missing structured block."""

    parsed = parse_auditor_protocol("")
    assert parsed == parse_auditor_protocol("just some prose, no headers\n")
    assert parsed.verdict is None
    assert parsed.resolution is None
    assert parsed.evidence == ()
    assert parsed.reasons == ()


def test_parse_auditor_protocol_rejects_unknown_verdict_word() -> None:
    """An unrecognised ``VERDICT:`` value is surfaced by the bridge,
    not by the parser. The parser only normalises the value; the
    bridge raises when the value is not a member of ``VERDICTS``."""

    parsed = parse_auditor_protocol("VERDICT: MAYBE\n")
    assert parsed.verdict == "MAYBE"  # parser does not validate


# ---------------------------------------------------------------------------
# Scenario 5: round-0 ACCEPT with real Auditor evidence converges.
# ---------------------------------------------------------------------------


def test_round_zero_accept_with_real_evidence_converges(tmp_path: Path) -> None:
    """Round 0 has no prior FIND; the post-FIND check is skipped.

    The Auditor returns ``ACCEPT`` with non-empty evidence on the
    first round. The loop converges in a single round.
    """

    artefact = tmp_path / "working.md"
    artefact.write_text("initial\n", encoding="utf-8")

    def _doer(ctx: DoerRoundContext) -> DoerOutcome:
        artefact.write_text(
            f"doer round {ctx.round_index}\n", encoding="utf-8"
        )
        return DoerOutcome(
            artefact_paths={"working": artefact},
            note="artefact written",
        )

    def _auditor(ctx: AuditorRoundContext) -> Verdict:
        return Verdict.accept(
            reasons=("round-0 cross-check ok",),
            evidence=("working.md cross-checked against AC",),
        )

    result = iterate_until_converged(
        bead_id="example-015-r0",
        doer_session_dir=tmp_path,
        auditor_session_dir=tmp_path,
        artefact_paths={"working": artefact},
        doer_runner=_doer,
        auditor_runner=_auditor,
        max_rounds=3,
    )
    assert result.converged is True
    assert result.rounds == 1
    assert result.final_verdict.verdict == "ACCEPT"
    # example-015.1: the evidence is preserved on the final verdict.
    assert any(
        "working.md cross-checked" in item
        for item in result.final_verdict.evidence
    )


# ---------------------------------------------------------------------------
# Scenarios 2 / 3 / 4: post-FIND convergence with the structured
# protocol. The doer writes a real artefact each round so the
# content-hash actually changes.
# ---------------------------------------------------------------------------


def test_post_find_artefact_changed_with_resolution_changed_converges(
    tmp_path: Path,
) -> None:
    """Scenario 2: FIND → ACCEPT with a changed artefact converges."""

    artefact = tmp_path / "working.md"
    artefact.write_text("initial\n", encoding="utf-8")

    def _doer(ctx: DoerRoundContext) -> DoerOutcome:
        # Round 1 actually changes the artefact; round 0 leaves it
        # at "initial".
        if ctx.round_index >= 1:
            artefact.write_text("revised per Auditor finding\n", encoding="utf-8")
        return DoerOutcome(
            artefact_paths={"working": artefact},
            note=f"round {ctx.round_index}",
        )

    def _auditor(ctx: AuditorRoundContext) -> Verdict:
        if ctx.round_index == 0:
            return Verdict.find(
                reasons=("round-0 finding",),
                evidence=("the artefact is missing the fix",),
            )
        # Round 1: artefact changed → ACCEPT with `RESOLUTION: changed`.
        return Verdict.accept(
            reasons=("round-1 fix verified",),
            evidence=("the fix is in working.md (post-revision)",),
            resolution="changed",
        )

    result = iterate_until_converged(
        bead_id="example-015-r1-changed",
        doer_session_dir=tmp_path,
        auditor_session_dir=tmp_path,
        artefact_paths={"working": artefact},
        doer_runner=_doer,
        auditor_runner=_auditor,
        max_rounds=3,
    )
    assert result.converged is True
    assert result.rounds == 2
    assert result.final_verdict.verdict == "ACCEPT"
    assert result.final_verdict.resolution == "changed"


def test_post_find_artefact_unchanged_with_no_change_proof_converges(
    tmp_path: Path,
) -> None:
    """Scenario 3: FIND → ACCEPT with unchanged artefact and
    ``RESOLUTION: no-change-proof`` converges.

    The Auditor's evidence is the no-change proof — for example,
    "the FIND was a false positive; the artefact already satisfies
    the AC because X". The runtime hashes the artefact and sees no
    change, but the resolution carries the explicit no-change
    proof so convergence is allowed.
    """

    artefact = tmp_path / "working.md"
    artefact.write_text("initial artefact, already correct\n", encoding="utf-8")

    def _doer(ctx: DoerRoundContext) -> DoerOutcome:
        # Doer does NOT change the artefact — the Auditor's
        # no-change-proof is the load-bearing evidence.
        return DoerOutcome(
            artefact_paths={"working": artefact},
            note=f"round {ctx.round_index} (no change)",
        )

    def _auditor(ctx: AuditorRoundContext) -> Verdict:
        if ctx.round_index == 0:
            return Verdict.find(
                reasons=("round-0 finding (later judged a false positive)",),
                evidence=("the initial claim against working.md",),
            )
        # Round 1: artefact unchanged; ACCEPT with the no-change proof.
        return Verdict.accept(
            reasons=("no-change proof: working.md already satisfies AC",),
            evidence=(
                "the working.md body already meets every AC line item; "
                "the round-0 finding was a misread",
            ),
            resolution="no-change-proof",
        )

    result = iterate_until_converged(
        bead_id="example-015-r1-ncp",
        doer_session_dir=tmp_path,
        auditor_session_dir=tmp_path,
        artefact_paths={"working": artefact},
        doer_runner=_doer,
        auditor_runner=_auditor,
        max_rounds=3,
    )
    assert result.converged is True
    assert result.rounds == 2
    assert result.final_verdict.verdict == "ACCEPT"
    assert result.final_verdict.resolution == "no-change-proof"


def test_post_find_artefact_unchanged_without_no_change_proof_blocks(
    tmp_path: Path,
) -> None:
    """Scenario 4: FIND → ACCEPT with unchanged artefact and no
    ``no-change-proof`` is downgraded to FIND.

    The runtime refuses to "rubber-stamp" a post-FIND acceptance
    when the Auditor supplies evidence but does not justify
    leaving the artefact unchanged. The ACCEPT is replaced with
    a FIND whose reason explains the downgrade, and the loop
    records the failed round. ``max_rounds=3`` ensures the loop
    exhausts before a third ACCEPT, so the final state is
    ``converged=False`` and the Bead stays open.
    """

    artefact = tmp_path / "working.md"
    artefact.write_text("initial\n", encoding="utf-8")

    def _doer(ctx: DoerRoundContext) -> DoerOutcome:
        # Doer does not change the artefact; the only path to
        # convergence is `no-change-proof`, which the Auditor
        # never provides.
        return DoerOutcome(
            artefact_paths={"working": artefact},
            note="no change",
        )

    def _auditor(ctx: AuditorRoundContext) -> Verdict:
        if ctx.round_index == 0:
            return Verdict.find(
                reasons=("round-0 finding",),
                evidence=("the artefact needs a fix",),
            )
        # Round 1 + 2: ACCEPT with evidence but no `no-change-proof`.
        return Verdict.accept(
            reasons=("trying to accept without changing the artefact",),
            evidence=("the artefact content (unchanged)",),
        )

    result = iterate_until_converged(
        bead_id="example-015-r1-blocked",
        doer_session_dir=tmp_path,
        auditor_session_dir=tmp_path,
        artefact_paths={"working": artefact},
        doer_runner=_doer,
        auditor_runner=_auditor,
        max_rounds=3,
    )
    assert result.converged is False
    assert result.final_verdict.verdict == "BLOCKED"
    # The transcript shows the round-0 FIND and the round-1 / 2
    # downgrades to FIND with the explicit reason.
    downgraded_reasons = [
        r
        for v in result.transcript
        for r in v.reasons
        if "no-change-proof" in r
    ]
    assert len(downgraded_reasons) >= 1, result.transcript


def test_post_find_artefact_changed_with_no_change_proof_still_converges(
    tmp_path: Path,
) -> None:
    """A contradictory ``no-change-proof`` on a **changed** artefact
    is treated permissively: the runtime accepts because the
    artefact hash differs from the prior round.

    The post-FIND check has two passes: ``changed`` (default) and
    ``no-change-proof``. An Auditor that returns ``no-change-proof``
    on a changed artefact is contradictory, but the change itself
    is the proof — refusing would be a false-negative. The
    runtime therefore allows the convergence; the Auditor's
    resolution is recorded verbatim on the final verdict.
    """

    artefact = tmp_path / "working.md"
    artefact.write_text("initial\n", encoding="utf-8")

    def _doer(ctx: DoerRoundContext) -> DoerOutcome:
        if ctx.round_index >= 1:
            artefact.write_text("revised\n", encoding="utf-8")
        return DoerOutcome(
            artefact_paths={"working": artefact},
            note=f"round {ctx.round_index}",
        )

    def _auditor(ctx: AuditorRoundContext) -> Verdict:
        if ctx.round_index == 0:
            return Verdict.find(reasons=("round-0 finding",))
        return Verdict.accept(
            reasons=("contradictory but accepted because the change is the proof",),
            evidence=("the changed artefact is itself the proof",),
            resolution="no-change-proof",
        )

    result = iterate_until_converged(
        bead_id="example-015-r1-contradictory",
        doer_session_dir=tmp_path,
        auditor_session_dir=tmp_path,
        artefact_paths={"working": artefact},
        doer_runner=_doer,
        auditor_runner=_auditor,
        max_rounds=3,
    )
    assert result.converged is True
    assert result.final_verdict.resolution == "no-change-proof"


# ---------------------------------------------------------------------------
# Scenario 6: multi-Auditor roster where one lacks evidence.
# ---------------------------------------------------------------------------


def test_combine_auditor_verdicts_all_accept_one_lacks_evidence_blocks() -> None:
    """Scenario 6: a pattern-(c) Auditor roster where every
    Auditor returned ``ACCEPT`` but one has empty evidence is
    downgraded to ``FIND``.

    The bridge is the canonical place that turns a per-Auditor
    evidence-less ``ACCEPT`` into a ``FIND``. The combine
    function then sees one ``FIND`` and the rest ``ACCEPT`` and
    combines them into a round-level ``FIND`` (example-014.1
    audit invariant: any ``FIND`` blocks the round).
    """

    accepts_with_evidence = [
        (
            "auditor-A",
            Verdict.accept(
                reasons=("A ok",),
                evidence=("A's evidence chain",),
            ),
        ),
        # Auditor-B's verdict here represents what the bridge
        # would have produced: an evidence-less ACCEPT is
        # downgraded to a FIND, not an ACCEPT.
        (
            "auditor-B",
            Verdict.find(
                reasons=(
                    "Auditor ACCEPT on round 0 without EVIDENCE block; "
                    "degraded to FIND (Charter §8; example-015.1).",
                ),
            ),
        ),
        (
            "auditor-C",
            Verdict.accept(
                reasons=("C ok",),
                evidence=("C's evidence chain",),
            ),
        ),
    ]
    combined = _combine_auditor_verdicts(
        accepts_with_evidence, round_index=0
    )
    assert combined.verdict == "FIND"
    # example-014.1: the FIND's reason is preserved on the
    # combined verdict.
    assert any(
        "auditor-B" in r and "WITHOUT" in r.upper() for r in combined.reasons
    ) or any(
        "auditor-B" in r and "without" in r for r in combined.reasons
    )


def test_combine_auditor_verdicts_all_accept_with_evidence_converges() -> None:
    """Positive control for scenario 6: every Auditor returns
    ``ACCEPT`` with evidence; the round combines to ``ACCEPT``
    with the merged evidence chain."""

    accepts_with_evidence = [
        (
            "auditor-A",
            Verdict.accept(
                reasons=("A ok",),
                evidence=("A's evidence",),
                resolution="changed",
            ),
        ),
        (
            "auditor-B",
            Verdict.accept(
                reasons=("B ok",),
                evidence=("B's evidence",),
                resolution="changed",
            ),
        ),
    ]
    combined = _combine_auditor_verdicts(
        accepts_with_evidence, round_index=0
    )
    assert combined.verdict == "ACCEPT"
    # example-015.1: every Auditor's evidence is preserved on the
    # combined verdict (audit trail).
    assert "A's evidence" in combined.evidence
    assert "B's evidence" in combined.evidence


# ---------------------------------------------------------------------------
# End-to-end: drive_bead scenario 1 (no EVIDENCE block) does not converge
# ---------------------------------------------------------------------------


def test_drive_bead_auditor_without_evidence_block_does_not_converge(
    fake_bd_dir: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """End-to-end scenario 1: an Auditor that reports ``ACCEPT`` via
    the side-channel ``next_state`` but does not embed the
    ``EVIDENCE`` block in ``result.md`` does not converge.

    The bridge downgrades the side-channel ``ACCEPT`` to
    ``FIND``; the loop records three rounds of ``FIND`` and
    the Bead stays open with the unresolved finding.
    """

    cwd = fake_bd_dir
    bead_id = "sample-no-evidence"
    _populate_pattern_b_record(cwd)
    _wire_bead_record(monkeypatch, cwd, bead_id)

    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    def _doer_runner(brief: SessionBrief) -> SessionOutcome:
        return SessionOutcome(
            working_text=f"# Doer {brief.session_name}\n- produced working.md\n",
            result_text="# Doer result\n- ok\n",
            verification="doer artefact recorded",
            next_state="await auditor review",
        )

    def _auditor_runner(brief: SessionBrief) -> SessionOutcome:
        # Side-channel ACCEPT (the simple "ACCEPT" verdict) but
        # no EVIDENCE block in result.md. The bridge must
        # downgrade.
        return _auditor_outcome_no_evidence(brief.bead_id, brief.session_name)

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
    assert result.convergence.converged is False
    assert result.closed is False
    # The transcript carries the downgraded FIND.
    transcript = (cwd / ".mba-work" / bead_id / "final" / "convergence.md")
    assert transcript.exists()
    body = transcript.read_text(encoding="utf-8")
    assert "WITHOUT" in body.upper() or "without" in body


# ---------------------------------------------------------------------------
# End-to-end: drive_bead post-FIND no-change-proof converges
# ---------------------------------------------------------------------------


def test_drive_bead_post_find_no_change_proof_converges(
    fake_bd_dir: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """End-to-end scenario 3: post-FIND ACCEPT with
    ``RESOLUTION: no-change-proof`` converges when the Doer
    leaves the artefact unchanged and the Auditor's
    ``result.md`` carries the explicit EVIDENCE block."""

    cwd = fake_bd_dir
    bead_id = "sample-no-change-proof"
    _populate_pattern_b_record(cwd)
    _wire_bead_record(monkeypatch, cwd, bead_id)

    monkeypatch.setenv("BD_COMMENT_LOG", str(tmp_path / "comments.log"))
    monkeypatch.setenv("BD_CLOSED_BEADS", str(tmp_path / "closed.log"))

    def _doer_runner(brief: SessionBrief) -> SessionOutcome:
        return SessionOutcome(
            working_text=f"# Doer {brief.session_name}\n- produced working.md\n",
            result_text="# Doer result\n- the artefact already satisfies AC\n",
            verification="doer artefact recorded",
            next_state="await auditor review",
        )

    def _auditor_runner(brief: SessionBrief) -> SessionOutcome:
        round_index = brief.round_index
        if round_index == 0:
            return SessionOutcome(
                working_text="# Auditor round 0 finding (later judged false positive)",
                result_text="# Auditor result round 0\n- FIND\n",
                next_state="FIND",
                verification="round-0 finding",
            )
        # Round 1: no-change-proof.
        return SessionOutcome(
            working_text="# Auditor round 1\n- the artefact already satisfies AC",
            result_text=(
                "# Auditor result round 1\n"
                "\n"
                "VERDICT: ACCEPT\n"
                "RESOLUTION: no-change-proof\n"
                "EVIDENCE:\n"
                "- the working.md body already meets every AC line item\n"
                "- the round-0 finding was a misread\n"
            ),
            next_state="ACCEPT",
            verification="Auditor: no-change-proof",
        )

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
    assert result.convergence.final_verdict.resolution == "no-change-proof"
    assert result.closed is True
