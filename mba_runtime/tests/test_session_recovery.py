"""Tests for ``mba_runtime.session_recovery``.

Covers the converged continuity requirements (example-010.1 P4/P6) and the
User's session-protection requirement:

* resume-first for exact MBA-owned sessions;
* strict protection of the User's own (foreign) sessions;
* ambiguity → ASK_USER (never a silent probe/resume/kill);
* ordered suitable-resource fallback with recorded substitutions
  (no silent model/effort downgrade);
* bounded retry / probe terminals when everything is unavailable;
* the runtime-only-writer audit trail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mba_runtime import ai_resources
from mba_runtime.ai_resources import (
    AIResource,
    AIResourceRecord,
    ResponsibilityConfig,
    TeamConfig,
)
from mba_runtime import session_recovery as sr


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _want(ai_id: str = "minimax", responsibility: str = "Doer") -> sr.SessionIdentity:
    return sr.SessionIdentity(
        bead_id="example-011.1",
        responsibility=responsibility,
        organisational_role="Implementation Lead",
        stage="Verify",
        ai_id=ai_id,
        effort="xHigh",
    )


def _mba_session(identity: sr.SessionIdentity, *, sid: str, status: str = "paused") -> sr.RecordedSession:
    return sr.RecordedSession(
        identity=identity, session_id=sid, owner=sr.OWNER_MBA, status=status
    )


def _user_session(identity: sr.SessionIdentity, *, sid: str, tool: str = "claude") -> sr.RecordedSession:
    # A foreign session the User runs for their own purposes.
    return sr.RecordedSession(
        identity=identity, session_id=sid, owner="user", status="in_progress", tool=tool
    )


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_identity_matches_requires_all_six_fields() -> None:
    a = _want()
    assert a.matches(_want())
    from dataclasses import replace

    for field in sr._IDENTITY_FIELDS:
        b = replace(a, **{field: "different"})
        assert not a.matches(b), field


# ---------------------------------------------------------------------------
# classify_recovery: resume-first / relaunch / ask-user / protection
# ---------------------------------------------------------------------------


def test_exact_mba_owned_resumable_session_resumes() -> None:
    want = _want()
    candidates = [_mba_session(want, sid="s1")]
    decision = sr.classify_recovery(want, candidates)
    assert decision.action == sr.RESUME
    assert decision.session_id == "s1"


def test_no_candidates_relaunches_same_ai() -> None:
    want = _want()
    decision = sr.classify_recovery(want, [])
    assert decision.action == sr.RELAUNCH
    assert decision.ai_id == "minimax"


def test_user_session_is_protected_never_touched() -> None:
    # A foreign session that even coincidentally carries the same
    # identity must never be resumed — ownership gates it out — and it
    # is reported as protected.
    want = _want()
    foreign = _user_session(want, sid="user-claude-1")
    decision = sr.classify_recovery(want, [foreign])
    assert decision.action == sr.RELAUNCH  # never RESUME a foreign session
    assert "user-claude-1" in decision.protected_session_ids
    assert decision.session_id is None


def test_multiple_exact_mba_matches_ask_user() -> None:
    want = _want()
    candidates = [_mba_session(want, sid="s1"), _mba_session(want, sid="s2")]
    decision = sr.classify_recovery(want, candidates)
    assert decision.action == sr.ASK_USER
    assert set(decision.ask_user_candidates) == {"s1", "s2"}


def test_mba_near_match_same_bead_asks_user() -> None:
    from dataclasses import replace

    want = _want()
    # Same Bead, different organisational role — a near-match we must
    # never silently resume or kill.
    near = _mba_session(replace(want, organisational_role="Reviewer"), sid="near-1")
    decision = sr.classify_recovery(want, [near])
    assert decision.action == sr.ASK_USER
    assert decision.ask_user_candidates == ("near-1",)


def test_completed_exact_session_is_not_resumed() -> None:
    # A completed (terminal) session is not resumable; classify never
    # resumes it (the caller must not re-dispatch a finished round).
    want = _want()
    done = _mba_session(want, sid="done-1", status="completed")
    decision = sr.classify_recovery(want, [done])
    assert decision.action != sr.RESUME


def test_mba_session_on_different_bead_is_ignored() -> None:
    from dataclasses import replace

    want = _want()
    other = _mba_session(replace(want, bead_id="example-099.1"), sid="other-1")
    decision = sr.classify_recovery(want, [other])
    # A different Bead is neither an exact match nor a same-Bead
    # near-match → relaunch, not ask-user.
    assert decision.action == sr.RELAUNCH


# ---------------------------------------------------------------------------
# suitable_resources: ordered fallback
# ---------------------------------------------------------------------------


def test_suitable_resources_orders_team_ai_first() -> None:
    record = ai_resources.default_record()
    team = ai_resources.team_config(record)
    assert sr.suitable_resources(record, team, "Doer") == ("minimax", "claude")
    assert sr.suitable_resources(record, team, "Auditor") == ("claude", "minimax")


def test_suitable_resources_excludes_incapable_resource() -> None:
    record = AIResourceRecord(
        schema=1,
        note="test",
        resources=(
            AIResource("minimax", "MiniMax", ("doer", "auditor"), "fresh_per_session"),
            AIResource("claude", "Claude", ("doer", "auditor"), "fresh_per_session"),
            AIResource("doer-only", "DoerOnly", ("doer",), "fresh_per_session"),
        ),
        teams={
            "default": TeamConfig(
                name="default",
                doer=ResponsibilityConfig(ai="minimax", hat="Engineer", session_count=1),
                auditor=ResponsibilityConfig(ai="claude", hat="Workflow Auditor", session_count=1),
                pattern="b",
            )
        },
    )
    team = record.teams["default"]
    assert "doer-only" in sr.suitable_resources(record, team, "Doer")
    assert "doer-only" not in sr.suitable_resources(record, team, "Auditor")


def test_suitable_resources_bad_responsibility_raises() -> None:
    record = ai_resources.default_record()
    team = ai_resources.team_config(record)
    with pytest.raises(sr.SessionRecoveryError):
        sr.suitable_resources(record, team, "Orchestrator")


def test_next_available_skips_unavailable() -> None:
    order = ("minimax", "claude")
    assert sr.next_available(order, {"minimax"}) == "claude"
    assert sr.next_available(order, {"minimax", "claude"}) is None


# ---------------------------------------------------------------------------
# record_substitution: no silent downgrade
# ---------------------------------------------------------------------------


def test_substitution_requires_reason_and_change() -> None:
    with pytest.raises(sr.SessionRecoveryError):
        sr.record_substitution("minimax", "claude", reason="   ")
    with pytest.raises(sr.SessionRecoveryError):
        sr.record_substitution("minimax", "minimax", reason="same")
    sub = sr.record_substitution("minimax", "claude", reason="minimax rate-limited")
    assert sub.prior_ai_id == "minimax"
    assert sub.new_ai_id == "claude"
    assert sub.reason


# ---------------------------------------------------------------------------
# plan_all_unavailable: bounded retry / probe terminals
# ---------------------------------------------------------------------------


def test_all_unavailable_known_reset_schedules_bounded_retry() -> None:
    plan = sr.plan_all_unavailable(
        reset_times={"minimax": 100.0, "claude": 200.0},
        now=50.0,
        attempts_so_far=0,
        backoff_seconds=5.0,
    )
    assert plan.action == sr.RETRY_SCHEDULED
    assert plan.next_attempt_at == 205.0  # max(reset) + backoff
    assert plan.attempts_used == 1


def test_all_unavailable_known_reset_terminal_after_budget() -> None:
    plan = sr.plan_all_unavailable(
        reset_times={"minimax": 100.0},
        now=50.0,
        attempts_so_far=sr.MAX_SCHEDULED_RETRIES,
    )
    assert plan.action == sr.BLOCKED_PROVIDER_UNAVAILABLE


def test_all_unavailable_unknown_reset_probes_are_bounded() -> None:
    plan = sr.plan_all_unavailable(
        reset_times={"minimax": None, "claude": 100.0},
        now=1000.0,
        probes_so_far=0,
        probe_interval_seconds=999999.0,  # requested larger than the cap
    )
    assert plan.action == sr.PROBE_SCHEDULED
    # Interval is capped at PROBE_INTERVAL_MAX_SECONDS.
    assert plan.next_attempt_at == 1000.0 + sr.PROBE_INTERVAL_MAX_SECONDS
    assert plan.probes_used == 1


def test_all_unavailable_unknown_reset_terminal_after_probe_budget() -> None:
    plan = sr.plan_all_unavailable(
        reset_times={"minimax": None},
        now=1000.0,
        probes_so_far=sr.MAX_PROBES_WHEN_RESET_UNKNOWN,
    )
    assert plan.action == sr.PAUSED_NO_RESET_TIME


def test_all_unavailable_requires_entries() -> None:
    with pytest.raises(sr.SessionRecoveryError):
        sr.plan_all_unavailable(reset_times={}, now=0.0)


# ---------------------------------------------------------------------------
# plan_recovery: the whole pipeline in order
# ---------------------------------------------------------------------------


def test_pipeline_resume_first_beats_fallback() -> None:
    want = _want()
    # The wanted AI is unavailable, but an exact resumable MBA session
    # exists → resume-first takes priority over any fallback.
    decision = sr.plan_recovery(
        want=want,
        candidates=[_mba_session(want, sid="s1")],
        order=("minimax", "claude"),
        unavailable={"minimax"},
    )
    assert decision.action == sr.RESUME
    assert decision.session_id == "s1"


def test_pipeline_relaunch_when_ai_available() -> None:
    want = _want()
    decision = sr.plan_recovery(
        want=want,
        candidates=[],
        order=("minimax", "claude"),
        unavailable=set(),
    )
    assert decision.action == sr.RELAUNCH
    assert decision.ai_id == "minimax"


def test_pipeline_fallback_records_substitution() -> None:
    want = _want()
    decision = sr.plan_recovery(
        want=want,
        candidates=[],
        order=("minimax", "claude"),
        unavailable={"minimax"},
    )
    assert decision.action == sr.FALLBACK
    assert decision.ai_id == "claude"
    assert decision.identity.ai_id == "claude"
    # The other five identity fields are preserved across the switch.
    assert decision.identity.effort == "xHigh"
    assert decision.identity.stage == "Verify"
    assert "fallback" in decision.reason.lower()


def test_pipeline_all_unavailable_schedules_retry() -> None:
    want = _want()
    decision = sr.plan_recovery(
        want=want,
        candidates=[],
        order=("minimax", "claude"),
        unavailable={"minimax", "claude"},
        reset_times={"minimax": 100.0, "claude": 100.0},
        now=10.0,
    )
    assert decision.action == sr.RETRY_SCHEDULED
    assert decision.next_attempt_at == 100.0


def test_pipeline_empty_suitable_resources_refuses_loudly() -> None:
    """An empty suitable-resource list with an unavailable wanted AI
    must surface a loud refusal — never a silent ``RELAUNCH`` or a fake
    ``FALLBACK`` to ``None``.

    Without this test the empty ``order=()`` path is reachable only
    through the all-unavailable branch, where ``plan_all_unavailable``
    explicitly refuses ``reset_times={}`` as unusable input. The
    example-011.1 audit flagged this behaviour as safe-but-unpinned; this
    test pins it.
    """

    want = _want()
    with pytest.raises(sr.SessionRecoveryError):
        sr.plan_recovery(
            want=want,
            candidates=[],
            order=(),
            unavailable={want.ai_id},
        )


def test_pipeline_ask_user_short_circuits_before_fallback() -> None:
    from dataclasses import replace

    want = _want()
    near = _mba_session(replace(want, organisational_role="Reviewer"), sid="near-1")
    decision = sr.plan_recovery(
        want=want,
        candidates=[near],
        order=("minimax", "claude"),
        unavailable={"minimax"},  # would otherwise trigger fallback
    )
    assert decision.action == sr.ASK_USER


def test_pipeline_propagates_protected_ids() -> None:
    want = _want()
    foreign = _user_session(_want(ai_id="claude"), sid="user-1")
    decision = sr.plan_recovery(
        want=want,
        candidates=[foreign],
        order=("minimax", "claude"),
        unavailable=set(),
    )
    assert "user-1" in decision.protected_session_ids


# ---------------------------------------------------------------------------
# AuditTrail: runtime-only-writer append log
# ---------------------------------------------------------------------------


def test_audit_trail_appends_and_reads(tmp_path: Path) -> None:
    trail = sr.AuditTrail(tmp_path / "sub" / "audit-trail.jsonl")
    assert trail.read_rows() == []
    decision = sr.classify_recovery(_want(), [])
    trail.append_decision(decision)
    trail.append({"note": "second row"})
    rows = trail.read_rows()
    assert len(rows) == 2
    assert rows[0]["action"] == sr.RELAUNCH
    assert rows[1]["note"] == "second row"
