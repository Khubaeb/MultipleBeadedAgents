"""example-017.1 hardening tests for ``mba_runtime.user_authority``.

Round-1 closed the narrow form (non-human actors refused). Round-2
closes the full forgeable-authority surface: an approved decision
must arrive via a verified channel (carrying a ``verified:``
attestation). A Doer that constructs
``AuthorityDecision.approved(actor="human:user")`` from its own
memory — even with a perfectly-formatted human-origin actor — is
refused by :func:`user_authority.gate` because the forged
attestation is missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mba_runtime.user_authority import (
    DEFAULT_NON_HUMAN_ACTORS,
    AuthorityDecision,
    UserAuthorityRequired,
    gate,
    is_human_origin,
    make_verified_decision,
)


# ---------------------------------------------------------------------------
# Actor / source validation (round 1 — unchanged)
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
        "human_0",           # wrong separator
        "user",
        "Engineer",          # role, not a person
        "minimax",           # AI handle
        "claude",
        "cli",
        "decision_fn_default",
        "DECISION_FN_DEFAULT",  # case-insensitive refusal set
        "cli ",
        " decision_fn_default",
        "decision_fn_default_x",
    ],
)
def test_is_human_origin_rejects_non_human_actors(actor: str) -> None:
    """Non-human actors are refused by the validator."""

    assert is_human_origin(actor) is False, (
        f"actor {actor!r} should not be human-origin"
    )


@pytest.mark.parametrize(
    "actor",
    [
        "human:user",
        "human:oncall",
        "human:engineer",
        "human:0",
        "  human:user  ",     # surrounding whitespace is fine
        "Human:user",         # case-insensitive prefix per the validator
        "HUMAN:user",         # case-insensitive prefix per the validator
    ],
)
def test_is_human_origin_accepts_well_formed_human_actors(actor: str) -> None:
    """Well-formed ``human:<identity>`` actors are accepted."""

    assert is_human_origin(actor) is True, (
        f"actor {actor!r} should be recognised human-origin"
    )


def test_default_non_human_actors_match_validator() -> None:
    """``DEFAULT_NON_HUMAN_ACTORS`` matches what the validator refuses."""

    for actor in DEFAULT_NON_HUMAN_ACTORS:
        assert is_human_origin(actor) is False, (
            f"default non-human actor {actor!r} should be refused"
        )


# ---------------------------------------------------------------------------
# Gate-level forge refusal — non-human actors (round 1 invariant)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "actor",
    [
        "cli",
        "decision_fn_default",
        "decision_fn_default_test",
        "Engineer",
        "minimax",
        "user",
        "human:",
        "human:   ",
        "",
        "human_user",
    ],
)
def test_gate_refuses_forged_approval_from_non_human_actor(
    actor: str,
) -> None:
    """An approved decision from a non-human actor is refused.

    Round-1 invariant: the actor must be human-origin. This
    test pins the case where the actor is the threat (with
    no attestation present).
    """

    def _fn(action: str) -> AuthorityDecision:
        return AuthorityDecision.approved(
            action=action,
            actor=actor,
            rationale="claiming non-human should suffice",
        )

    with pytest.raises(UserAuthorityRequired, match="forge"):
        gate("source_git_commit", decision_fn=_fn)


def test_gate_refuses_default_decision_fn_default_actor() -> None:
    """``decision_fn_default`` (the placeholder) is refused on approval."""

    def _fn(action: str) -> AuthorityDecision:
        return AuthorityDecision.approved(
            action=action,
            actor="decision_fn_default",
            rationale="the default trying to self-approve",
        )

    with pytest.raises(UserAuthorityRequired):
        gate("bd_dolt_push", decision_fn=_fn)


def test_gate_passes_through_refused_decisions() -> None:
    """A refused decision (any actor) is passed through unchanged.

    A refusal is never a forge — the gate returns it so the
    caller can record the refusal.
    """

    def _fn(action: str) -> AuthorityDecision:
        return AuthorityDecision.refused(
            action=action,
            actor="decision_fn_default",
            rationale="explicit refusal",
        )

    decision = gate("bd_dolt_pull", decision_fn=_fn)
    assert decision.approved is False
    assert decision.actor == "decision_fn_default"


def test_gate_forge_refusal_reason_mentions_actor() -> None:
    """The error message surfaces the actor that forged."""

    def _fn(action: str) -> AuthorityDecision:
        return AuthorityDecision.approved(
            action=action,
            actor="cli",
            rationale="i'm claiming cli is human",
        )

    with pytest.raises(UserAuthorityRequired) as excinfo:
        gate("source_git_commit", decision_fn=_fn)
    message = str(excinfo.value)
    assert "cli" in message
    assert "human-origin" in message.lower() or "forge" in message.lower()


def test_each_catalogue_action_is_forge_refused() -> None:
    """Every §11 action is forge-refused on non-human actors."""

    for action in sorted(
        (
            "source_git_commit",
            "source_git_push",
            "bd_dolt_push",
            "bd_dolt_pull",
            "deployment",
            "external_message",
            "credentials_or_spending",
            "destructive_change",
            "external_dispatch_unconfinement",
            "reusable_workflow_change",
        )
    ):
        def _fn(action: str = action) -> AuthorityDecision:
            return AuthorityDecision.approved(
                action=action,
                actor="cli",
                rationale="trying every action",
            )

        with pytest.raises(UserAuthorityRequired):
            gate(action, decision_fn=_fn)


# ---------------------------------------------------------------------------
# Gate-level forge refusal — verification attestation (round 2 invariant)
# ---------------------------------------------------------------------------


def test_gate_accepts_verified_decision() -> None:
    """A verified approved decision passes the gate."""

    def _fn(action: str) -> AuthorityDecision:
        return make_verified_decision(
            action=action,
            actor="human:user",
            rationale="explicit user approval",
            channel="test_accepts_verified_decision",
        )

    decision = gate("source_git_commit", decision_fn=_fn)
    assert decision.approved is True
    assert decision.actor == "human:user"
    assert decision.attestation == "verified:test_accepts_verified_decision"


def test_gate_refuses_direct_approved_decision_without_attestation() -> None:
    """A human-format ``AuthorityDecision.approved(...)`` is a forge.

    The Auditor's round-2 probe (probe 3): the gate must refuse
    ``AuthorityDecision.approved(actor="human:fake", ...)``
    passed through any caller when the decision itself lacks a
    verified-channel attestation. The forge attempt looks
    perfectly human-origin — only the missing attestation
    surfaces the impersonation.
    """

    def _fn(action: str) -> AuthorityDecision:
        return AuthorityDecision.approved(
            action=action,
            actor="human:fake",
            rationale="forged approval from any caller",
        )

    with pytest.raises(UserAuthorityRequired, match="forge"):
        gate("source_git_push", decision_fn=_fn)


def test_gate_refuses_attestation_only_unverified() -> None:
    """An actor-check failure routes through the actor error.

    The gate applies two checks in order: actor first, then
    attestation. Either check triggers a refusal. The actor
    error surfaces the bad actor; the attestation error
    surfaces the missing provenance.
    """

    # Non-human actor: failure is the actor check (still raised).
    def _non_human(action: str) -> AuthorityDecision:
        return AuthorityDecision.approved(
            action=action, actor="cli", rationale="non-human forge"
        )

    with pytest.raises(UserAuthorityRequired):
        gate("source_git_commit", decision_fn=_non_human)

    # Human-format actor, no attestation: failure is the
    # attestation check.
    def _no_attest(action: str) -> AuthorityDecision:
        return AuthorityDecision.approved(
            action=action, actor="human:user", rationale="missing attest"
        )

    with pytest.raises(UserAuthorityRequired, match="attestation"):
        gate("source_git_commit", decision_fn=_no_attest)


def test_gate_refuses_unverified_attestation_string() -> None:
    """An attestation that doesn't start with ``verified:`` is refused."""

    def _fn(action: str) -> AuthorityDecision:
        return AuthorityDecision(
            action=action,
            actor="human:user",
            rationale="attempt to bypass with prefix-attest",
            approved=True,
            recorded_at="2026-07-21T00:00:00Z",
            attestation="unverified:caller",
        )

    with pytest.raises(UserAuthorityRequired, match="verified"):
        gate("source_git_commit", decision_fn=_fn)


def test_make_verified_decision_rejects_empty_channel_token() -> None:
    """The channel token must be non-empty and free of ``:``."""

    with pytest.raises(ValueError):
        make_verified_decision(
            action="bd_dolt_push",
            actor="human:user",
            rationale="oops",
            channel="",
        )
    with pytest.raises(ValueError):
        make_verified_decision(
            action="bd_dolt_push",
            actor="human:user",
            rationale="oops",
            channel="bad:token",
        )


def test_each_catalogue_action_rejects_unverified_approval() -> None:
    """Every §11 action refuses an unverified approved decision.

    Catologue-wide invariant: an approved decision with a
    human-format actor but no verified attestation is refused
    on every §11 action.
    """

    for action in sorted(
        (
            "source_git_commit",
            "source_git_push",
            "bd_dolt_push",
            "bd_dolt_pull",
            "deployment",
            "external_message",
            "credentials_or_spending",
            "destructive_change",
            "external_dispatch_unconfinement",
            "reusable_workflow_change",
        )
    ):
        def _fn(action: str = action) -> AuthorityDecision:
            return AuthorityDecision.approved(
                action=action,
                actor="human:fake",
                rationale="forged for every action",
            )

        with pytest.raises(UserAuthorityRequired, match="forge"):
            gate(action, decision_fn=_fn)


# ---------------------------------------------------------------------------
# Integration with the file-backed decision-fn builder
# ---------------------------------------------------------------------------


def test_decision_log_with_non_human_actor_is_refused(tmp_path: Path) -> None:
    """A real persisted decision with a non-human actor is refused on load."""

    from mba_runtime.user_authority import decisions_to_file_decision_fn

    path = tmp_path / "decisions.jsonl"
    fn = decisions_to_file_decision_fn(
        path,
        auto={
            "source_git_push": AuthorityDecision.approved(
                action="source_git_push",
                actor="cli",
                rationale="trying to forge via CLI actor",
            )
        },
    )
    with pytest.raises(UserAuthorityRequired, match="forge"):
        gate("source_git_push", decision_fn=fn)


def test_decision_log_with_human_actor_is_accepted(tmp_path: Path) -> None:
    """A real persisted human-origin decision is accepted end-to-end."""

    from mba_runtime.user_authority import decisions_to_file_decision_fn

    path = tmp_path / "decisions.jsonl"
    fn = decisions_to_file_decision_fn(
        path,
        auto={
            "bd_dolt_push": AuthorityDecision.approved(
                action="bd_dolt_push",
                actor="human:oncall",
                rationale="dolt push approved",
            )
        },
    )
    decision = gate("bd_dolt_push", decision_fn=fn)
    assert decision.approved is True
    assert decision.actor == "human:oncall"


def test_decision_log_verified_channel_stamps_attestation(
    tmp_path: Path,
) -> None:
    """``decisions_to_file_decision_fn`` stamps a verified attestation.

    A direct ``AuthorityDecision.approved`` inside ``auto=``
    is rewritten with ``attestation="verified:test_file_decision_fn"``
    when it reaches the gate, so the round-2 invariant holds
    without the call site having to know the helper exists.
    """

    from mba_runtime.user_authority import decisions_to_file_decision_fn

    path = tmp_path / "decisions.jsonl"
    fn = decisions_to_file_decision_fn(
        path,
        auto={
            "bd_dolt_push": AuthorityDecision.approved(
                action="bd_dolt_push",
                actor="human:oncall",
                rationale="approved via auto=",
            )
        },
    )
    decision = gate("bd_dolt_push", decision_fn=fn)
    assert decision.approved is True
    assert decision.actor == "human:oncall"
    assert decision.attestation == "verified:test_file_decision_fn"
