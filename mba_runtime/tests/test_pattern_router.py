"""Tests for ``mba_runtime.pattern_router``."""

from __future__ import annotations

import pytest

from mba_runtime import ai_resources, pattern_router
from mba_runtime.ai_resources import (
    AIResource,
    AIResourceRecord,
    ResponsibilityConfig,
    TeamConfig,
)
from mba_runtime.pattern_router import PatternError


def _record_with_minimax_and_claude() -> AIResourceRecord:
    return AIResourceRecord(
        schema=1,
        note="fixture",
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
        teams={},
    )


def test_pattern_b_distinct_ais() -> None:
    record = _record_with_minimax_and_claude()
    team = TeamConfig(
        name="default",
        doer=ResponsibilityConfig(
            ai="minimax", hat="Engineer", session_count=1
        ),
        auditor=ResponsibilityConfig(
            ai="claude", hat="Workflow Auditor", session_count=1
        ),
        pattern="b",
    )
    plan = pattern_router.route(record, bead_id="sample", team=team)
    assert plan.pattern == "b"
    assert len(plan.doer_sessions) == 1
    assert len(plan.auditor_sessions) == 1
    assert plan.doer_sessions[0].hat == "Engineer"
    assert plan.auditor_sessions[0].hat == "Workflow Auditor"


def test_pattern_a_single_ai_opposing_hats() -> None:
    record = AIResourceRecord(
        schema=1,
        note="fixture",
        resources=(
            AIResource(
                id="minimax",
                label="MiniMax-M3",
                capabilities=("doer", "auditor"),
                session_lifetime="fresh_per_session",
            ),
        ),
        teams={},
    )
    team = TeamConfig(
        name="one-ai",
        doer=ResponsibilityConfig(
            ai="minimax", hat="Engineer", session_count=1
        ),
        auditor=ResponsibilityConfig(
            ai="minimax",
            hat="Workflow Auditor",
            session_count=1,
        ),
        pattern="a",
    )
    plan = pattern_router.route(record, bead_id="sample", team=team)
    assert plan.pattern == "a"
    assert plan.doer_sessions[0].ai_id == "minimax"
    assert plan.auditor_sessions[0].ai_id == "minimax"
    assert plan.doer_sessions[0].hat != plan.auditor_sessions[0].hat


def test_pattern_c_multi_session_combined_marker() -> None:
    record = _record_with_minimax_and_claude()
    team = TeamConfig(
        name="multi-doer",
        doer=ResponsibilityConfig(
            ai="minimax", hat="Engineer", session_count=2
        ),
        auditor=ResponsibilityConfig(
            ai="claude", hat="Workflow Auditor", session_count=1
        ),
        pattern="c",
    )
    plan = pattern_router.route(record, bead_id="sample", team=team)
    assert plan.pattern == "c"
    assert len(plan.doer_sessions) == 2
    assert all(spec.combine_before_audit for spec in plan.doer_sessions)
    # Auditor reviews the combined Doer artefact.
    assert not plan.auditor_sessions[0].combine_before_audit


def test_pattern_d_single_ai_with_fresh_sessions() -> None:
    record = AIResourceRecord(
        schema=1,
        note="fixture",
        resources=(
            AIResource(
                id="claude",
                label="Claude Opus 4.8",
                capabilities=("doer", "auditor"),
                session_lifetime="fresh_per_session",
            ),
        ),
        teams={},
    )
    team = TeamConfig(
        name="single-ai",
        doer=ResponsibilityConfig(
            ai="claude", hat="Engineer", session_count=1
        ),
        auditor=ResponsibilityConfig(
            ai="claude",
            hat="Workflow Auditor",
            session_count=1,
        ),
        pattern="d",
    )
    plan = pattern_router.route(record, bead_id="sample", team=team)
    assert plan.pattern == "d"


def test_pattern_b_rejects_same_ai_on_both_sides() -> None:
    record = AIResourceRecord(
        schema=1,
        note="fixture",
        resources=(
            AIResource(
                id="minimax",
                label="MiniMax-M3",
                capabilities=("doer", "auditor"),
                session_lifetime="fresh_per_session",
            ),
        ),
        teams={},
    )
    team = TeamConfig(
        name="bad",
        doer=ResponsibilityConfig(
            ai="minimax", hat="Engineer", session_count=1
        ),
        auditor=ResponsibilityConfig(
            ai="minimax", hat="Workflow Auditor", session_count=1
        ),
        pattern="b",
    )
    with pytest.raises(PatternError, match="pattern=b"):
        pattern_router.route(record, bead_id="sample", team=team)


def test_pattern_a_requires_opposing_hats() -> None:
    record = AIResourceRecord(
        schema=1,
        note="fixture",
        resources=(
            AIResource(
                id="minimax",
                label="MiniMax-M3",
                capabilities=("doer", "auditor"),
                session_lifetime="fresh_per_session",
            ),
        ),
        teams={},
    )
    team = TeamConfig(
        name="bad",
        doer=ResponsibilityConfig(
            ai="minimax", hat="Engineer", session_count=1
        ),
        auditor=ResponsibilityConfig(
            ai="minimax", hat="Engineer", session_count=1
        ),
        pattern="a",
    )
    with pytest.raises(PatternError, match="opposing hats"):
        pattern_router.route(record, bead_id="sample", team=team)


def test_router_refuses_unknown_pattern_label() -> None:
    record = _record_with_minimax_and_claude()
    team = TeamConfig(
        name="bogus",
        doer=ResponsibilityConfig(
            ai="minimax", hat="Engineer", session_count=1
        ),
        auditor=ResponsibilityConfig(
            ai="claude", hat="Workflow Auditor", session_count=1
        ),
        pattern="x",
    )
    with pytest.raises(PatternError, match="unknown pattern"):
        pattern_router.route(record, bead_id="sample", team=team)


def test_router_refuses_missing_doer_capability() -> None:
    record = AIResourceRecord(
        schema=1,
        note="fixture",
        resources=(
            AIResource(
                id="minimax",
                label="MiniMax-M3",
                capabilities=("auditor",),
                session_lifetime="fresh_per_session",
            ),
        ),
        teams={},
    )
    team = TeamConfig(
        name="bad",
        doer=ResponsibilityConfig(
            ai="minimax", hat="Engineer", session_count=1
        ),
        auditor=ResponsibilityConfig(
            ai="minimax", hat="Workflow Auditor", session_count=1
        ),
        pattern="d",
    )
    with pytest.raises(PatternError, match="doer"):
        pattern_router.route(record, bead_id="sample", team=team)


def test_session_name_is_stable_and_unique_across_sessions() -> None:
    record = _record_with_minimax_and_claude()
    team = TeamConfig(
        name="multi-doer",
        doer=ResponsibilityConfig(
            ai="minimax", hat="Engineer", session_count=2
        ),
        auditor=ResponsibilityConfig(
            ai="claude", hat="Workflow Auditor", session_count=1
        ),
        pattern="c",
    )
    plan = pattern_router.route(record, bead_id="sample", team=team)
    first = plan.doer_sessions[0].session_name("sample")
    second = plan.doer_sessions[1].session_name("sample")
    # Two sessions sharing a hat (pattern c) get unique path tails.
    assert first != second
    assert first.startswith("sample-")
    # The second session always carries the index suffix; the first
    # uses the bare slug so the directory name stays compact when
    # only one session wears that hat.
    assert "-1" in second
    auditor = plan.auditor_sessions[0].session_name("sample")
    assert auditor != first and auditor != second
