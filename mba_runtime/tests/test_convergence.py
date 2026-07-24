"""Tests for ``mba_runtime.convergence``."""

from __future__ import annotations

from pathlib import Path

import pytest

from mba_runtime.convergence import (
    AuditorRoundContext,
    ConvergenceError,
    DoerOutcome,
    DoerRoundContext,
    Verdict,
    iterate_until_converged,
)


def test_convergence_accepts_on_first_round(tmp_path: Path) -> None:
    artefact = tmp_path / "working.md"
    artefact.write_text("doer wrote this\n", encoding="utf-8")
    transcript_path = tmp_path / "convergence.md"

    def _doer(ctx: DoerRoundContext) -> DoerOutcome:
        artefact.write_text(f"doer round {ctx.round_index}\n", encoding="utf-8")
        return DoerOutcome(
            artefact_paths=dict(ctx.artefact_paths),
            note="artefact written",
        )

    def _auditor(ctx: AuditorRoundContext) -> Verdict:
        return Verdict.accept(
            reasons=("looks good",),
            evidence=("cross-checked working.md against requirement",),
        )

    result = iterate_until_converged(
        bead_id="sample",
        doer_session_dir=tmp_path,
        auditor_session_dir=tmp_path,
        artefact_paths={"working": artefact},
        doer_runner=_doer,
        auditor_runner=_auditor,
        max_rounds=3,
        transcript_path=transcript_path,
    )
    assert result.converged is True
    assert result.rounds == 1
    assert result.final_verdict.verdict == "ACCEPT"
    assert transcript_path.exists()


def test_empty_evidence_accept_at_constructor_raises() -> None:
    """An ``ACCEPT`` with no reasons is not convergence (Charter §8).

    The constructor refuses so a misbehaving runner cannot hand a bare
    ACCEPT to the loop. example-015.1 evidence-required: the constructor
    also refuses when ``reasons`` is non-empty but ``evidence`` is
    empty.
    """

    with pytest.raises(ConvergenceError, match="non-empty reasons"):
        Verdict.accept(reasons=())
    with pytest.raises(ConvergenceError, match="Auditor-originated evidence"):
        Verdict.accept(reasons=("ok",), evidence=())


def test_empty_evidence_accept_in_loop_raises(tmp_path: Path) -> None:
    """The loop itself refuses a round-1 ACCEPT with empty evidence.

    Same rule (Charter §8 "accepted proof required" + example-015.1
    evidence-required) but enforced at the loop boundary in case the
    verdict was built outside :meth:`Verdict.accept`. Bypassing the
    constructor does not let a misbehaving runner slip through.
    """

    def _doer(ctx: DoerRoundContext) -> DoerOutcome:
        return DoerOutcome(
            artefact_paths=dict(ctx.artefact_paths),
            note="x",
        )

    def _auditor(ctx: AuditorRoundContext) -> Verdict:
        # Bypass :meth:`Verdict.accept` to simulate a misbehaving runner.
        return Verdict(verdict="ACCEPT", reasons=("ok",), evidence=())

    with pytest.raises(ConvergenceError, match="empty evidence"):
        iterate_until_converged(
            bead_id="sample",
            doer_session_dir=tmp_path,
            auditor_session_dir=tmp_path,
            artefact_paths={},
            doer_runner=_doer,
            auditor_runner=_auditor,
            max_rounds=3,
        )


def test_convergence_loops_until_fix(tmp_path: Path) -> None:
    artefact = tmp_path / "working.md"
    artefact.write_text("initial\n", encoding="utf-8")

    def _doer(ctx: DoerRoundContext) -> DoerOutcome:
        artefact.write_text(f"round {ctx.round_index}\n", encoding="utf-8")
        return DoerOutcome(
            artefact_paths=dict(ctx.artefact_paths),
            note=f"round {ctx.round_index}",
        )

    verdicts = [
        Verdict.find(reasons=("first round finding",)),
        Verdict.find(reasons=("second round finding",)),
        Verdict.accept(
            reasons=("third round ok",),
            evidence=("the fix is in working.md",),
            resolution="changed",
        ),
    ]

    def _auditor(ctx: AuditorRoundContext) -> Verdict:
        return verdicts[ctx.round_index]

    result = iterate_until_converged(
        bead_id="sample",
        doer_session_dir=tmp_path,
        auditor_session_dir=tmp_path,
        artefact_paths={"working": artefact},
        doer_runner=_doer,
        auditor_runner=_auditor,
        max_rounds=5,
    )
    assert result.converged is True
    assert result.rounds == 3
    assert result.final_verdict.verdict == "ACCEPT"
    assert artefact.read_text(encoding="utf-8") == "round 2\n"


def test_convergence_blocks_on_max_rounds_when_not_converged(
    tmp_path: Path,
) -> None:
    artefact = tmp_path / "working.md"
    artefact.write_text("initial\n", encoding="utf-8")

    def _doer(ctx: DoerRoundContext) -> DoerOutcome:
        artefact.write_text(f"rev {ctx.round_index}\n", encoding="utf-8")
        return DoerOutcome(
            artefact_paths=dict(ctx.artefact_paths),
            note="rev",
        )

    def _auditor(ctx: AuditorRoundContext) -> Verdict:
        return Verdict.find(reasons=(f"finding round {ctx.round_index}",))

    result = iterate_until_converged(
        bead_id="sample",
        doer_session_dir=tmp_path,
        auditor_session_dir=tmp_path,
        artefact_paths={"working": artefact},
        doer_runner=_doer,
        auditor_runner=_auditor,
        max_rounds=3,
    )
    assert result.converged is False
    assert result.rounds == 3
    assert result.final_verdict.verdict == "BLOCKED"


def test_convergence_blocks_early_on_blocked_verdict(tmp_path: Path) -> None:
    artefact = tmp_path / "working.md"
    artefact.write_text("initial\n", encoding="utf-8")

    def _doer(ctx: DoerRoundContext) -> DoerOutcome:
        return DoerOutcome(
            artefact_paths=dict(ctx.artefact_paths),
            note="ignore",
        )

    def _auditor(ctx: AuditorRoundContext) -> Verdict:
        return Verdict.blocked(reasons=("categorical rejection",))

    result = iterate_until_converged(
        bead_id="sample",
        doer_session_dir=tmp_path,
        auditor_session_dir=tmp_path,
        artefact_paths={"working": artefact},
        doer_runner=_doer,
        auditor_runner=_auditor,
        max_rounds=3,
    )
    assert result.converged is False
    assert result.rounds == 1
    assert result.final_verdict.verdict == "BLOCKED"


def test_max_rounds_must_be_positive(tmp_path: Path) -> None:
    def _noop_doer(ctx: DoerRoundContext) -> DoerOutcome:
        return DoerOutcome(artefact_paths={}, note="x")

    def _noop_auditor(ctx: AuditorRoundContext) -> Verdict:
        return Verdict.accept(reasons=("ok",))

    with pytest.raises(ValueError):
        iterate_until_converged(
            bead_id="sample",
            doer_session_dir=tmp_path,
            auditor_session_dir=tmp_path,
            artefact_paths={},
            doer_runner=_noop_doer,
            auditor_runner=_noop_auditor,
            max_rounds=0,
        )


def test_unknown_auditor_verdict_is_refused(tmp_path: Path) -> None:
    def _noop_doer(ctx: DoerRoundContext) -> DoerOutcome:
        return DoerOutcome(artefact_paths=dict(ctx.artefact_paths), note="x")

    def _bad_auditor(ctx: AuditorRoundContext) -> Verdict:
        return Verdict(verdict="MAYBE", reasons=("unsupported",))

    with pytest.raises(ValueError, match="unexpected verdict"):
        iterate_until_converged(
            bead_id="sample",
            doer_session_dir=tmp_path,
            auditor_session_dir=tmp_path,
            artefact_paths={},
            doer_runner=_noop_doer,
            auditor_runner=_bad_auditor,
            max_rounds=3,
        )


# ---------------------------------------------------------------------------
# example-014.1 acceptance: any Auditor FIND/BLOCKED prevents convergence
# ---------------------------------------------------------------------------


def test_combine_auditor_verdicts_blocks_when_any_blocks() -> None:
    """A single ``BLOCKED`` Auditor blocks the round even when another
    accepts (Charter §8 + example-014.1)."""

    from mba_runtime.lifecycle import _combine_auditor_verdicts

    verdicts = [
        (
            "auditor-A",
            Verdict.accept(
                reasons=("auditor A accepted",),
                evidence=("A's evidence chain",),
            ),
        ),
        (
            "auditor-B",
            Verdict.blocked(reasons=("auditor B rejected",)),
        ),
    ]
    combined = _combine_auditor_verdicts(verdicts, round_index=0)
    assert combined.verdict == "BLOCKED"
    assert any("auditor B" in r for r in combined.reasons)


def test_combine_auditor_verdicts_finds_when_any_finds() -> None:
    """A single ``FIND`` Auditor finds the round even when another accepts."""

    from mba_runtime.lifecycle import _combine_auditor_verdicts

    verdicts = [
        (
            "auditor-A",
            Verdict.accept(
                reasons=("auditor A accepted",),
                evidence=("A's evidence chain",),
            ),
        ),
        (
            "auditor-B",
            Verdict.find(reasons=("auditor B found a bug",)),
        ),
    ]
    combined = _combine_auditor_verdicts(verdicts, round_index=1)
    assert combined.verdict == "FIND"
    assert any("auditor B" in r for r in combined.reasons)


def test_combine_auditor_verdicts_accepts_only_when_every_auditor_accepts() -> None:
    """Convergence requires **every** Auditor to accept (example-015.1
    evidence-required: the combined ACCEPT carries the merged
    evidence chain).
    """

    from mba_runtime.lifecycle import _combine_auditor_verdicts

    verdicts = [
        (
            "auditor-A",
            Verdict.accept(
                reasons=("A ok",),
                evidence=("A's evidence chain",),
            ),
        ),
        (
            "auditor-B",
            Verdict.accept(
                reasons=("B ok",),
                evidence=("B's evidence chain",),
            ),
        ),
    ]
    combined = _combine_auditor_verdicts(verdicts, round_index=0)
    assert combined.verdict == "ACCEPT"
    assert all(len(v) for v in combined.reasons)
    # example-015.1: combined ACCEPT carries every Auditor's evidence.
    assert "A's evidence chain" in combined.evidence
    assert "B's evidence chain" in combined.evidence


def test_combine_auditor_verdicts_blocks_take_precedence_over_finds() -> None:
    """``BLOCKED`` is the highest-priority non-acceptance."""

    from mba_runtime.lifecycle import _combine_auditor_verdicts

    verdicts = [
        ("auditor-A", Verdict.find(reasons=("finding",))),
        ("auditor-B", Verdict.blocked(reasons=("blocked",))),
    ]
    combined = _combine_auditor_verdicts(verdicts, round_index=0)
    assert combined.verdict == "BLOCKED"


def test_combine_auditor_verdicts_preserves_no_change_proof() -> None:
    """example-015.1: any Auditor's ``no-change-proof`` survives the merge."""

    from mba_runtime.lifecycle import _combine_auditor_verdicts

    verdicts = [
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
                resolution="no-change-proof",
            ),
        ),
    ]
    combined = _combine_auditor_verdicts(verdicts, round_index=0)
    assert combined.resolution == "no-change-proof"
