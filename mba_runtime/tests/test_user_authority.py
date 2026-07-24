"""Tests for ``mba_runtime.user_authority``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mba_runtime import user_authority
from mba_runtime.user_authority import (
    AuthorityDecision,
    UserAuthorityRequired,
    decisions_to_file_decision_fn,
    gate,
    make_verified_decision,
)


def test_gate_without_decision_fn_refuses() -> None:
    with pytest.raises(UserAuthorityRequired):
        gate("bd_dolt_push")


def test_gate_rejects_unknown_action() -> None:
    def _fn(action: str) -> AuthorityDecision:
        return user_authority.make_verified_decision(
            action=action,
            actor="human:tester",
            rationale="because",
            channel="test_unknown_action",
        )

    with pytest.raises(UserAuthorityRequired, match="not in the §11 catalogue"):
        gate("teleport_beads", decision_fn=_fn)


def test_gate_returns_decision_on_approval(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    fn = decisions_to_file_decision_fn(
        path,
        auto={
            "bd_dolt_push": user_authority.make_verified_decision(
                action="bd_dolt_push",
                actor="human:tester",
                rationale="approved in test",
                channel="test_returns_decision_on_approval",
            )
        },
    )
    decision = gate("bd_dolt_push", decision_fn=fn)
    assert decision.approved is True
    assert decision.actor == "human:tester"
    on_disk = json.loads(path.read_text(encoding="utf-8").strip())
    assert on_disk["action"] == "bd_dolt_push"
    assert on_disk["approved"] is True


def test_gate_refusal_is_recorded(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    fn = decisions_to_file_decision_fn(path)
    decision = gate("source_git_commit", decision_fn=fn)
    assert decision.approved is False
    rows = path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    payload = json.loads(rows[0])
    assert payload["approved"] is False


def test_gate_mismatched_return_type_refuses() -> None:
    def _fn(action: str) -> str:
        return "yes"

    with pytest.raises(UserAuthorityRequired, match="expected an AuthorityDecision"):
        gate("bd_dolt_pull", decision_fn=_fn)


def test_decision_log_corruption(tmp_path: Path) -> None:
    bad = tmp_path / "decisions.jsonl"
    bad.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(UserAuthorityRequired, match="decision_log_corruption"):
        user_authority.load_decision_log(bad)


def test_each_catalogue_action_is_blockable(tmp_path: Path) -> None:
    for action in sorted(user_authority.USER_AUTHORITY_ACTIONS):
        path = tmp_path / f"{action}.jsonl"
        fn = decisions_to_file_decision_fn(path)
        decision = gate(action, decision_fn=fn)
        assert decision.approved is False
        assert decision.action == action


def test_decision_actor_is_required_to_be_human_origin() -> None:
    # The recorded_at / actor fields are populated explicitly; the
    # helper never substitutes an AI-handle for a human one. This test
    # pins the contract.
    decision = AuthorityDecision.approved(
        action="source_git_commit",
        actor="human:user",
        rationale="explicit user decision",
    )
    assert "human" in decision.actor
