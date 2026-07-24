"""§7 lifecycle Orchestrator behaviour (AC #11).

A test drives an atomic request and a staged request through the
installed behaviour and asserts each path takes the correct branch
from ``docs/mba/charter.md`` §7.
"""

from __future__ import annotations

import pytest

from mba_foundation import orchestrator
from mba_foundation.orchestrator import (
    Branch,
    Classification,
    StageSelection,
    classify_request,
    select_stages,
    drive_lifecycle,
    assert_branch,
)


# ---------------------------------------------------------------------------
# AC #11 — branch tests
# ---------------------------------------------------------------------------


ATOMIC_REQUESTS = [
    "fix typo in README",            # atomic keyword
    "rename foo to bar",             # atomic keyword
    "one-line tweak",                # atomic keyword + short
    "update readme now",             # atomic + short
    "lint single edit",              # very short, defaults to atomic
]


STAGED_REQUESTS = [
    "implement OAuth2 across the project with several integrations",   # staged keyword
    "design new module end-to-end, including migrations",              # staged keywords
    "refactor the runtime and roll out server mode",                    # staged keywords
    "set up pipeline with observability and integration tests",         # staged + long
    "introduce adoption discover/preview/adopt/reject flows",          # staged keyword
]


@pytest.mark.parametrize("user_request", ATOMIC_REQUESTS)
def test_classify_request_atomic(user_request: str) -> None:
    classification = classify_request(user_request)
    assert classification.branch is Branch.ATOMIC, classification.rationale


@pytest.mark.parametrize("user_request", STAGED_REQUESTS)
def test_classify_request_staged(user_request: str) -> None:
    classification = classify_request(user_request)
    assert classification.branch is Branch.STAGED, classification.rationale


def test_atomic_path_runs_produce_then_audit() -> None:
    """Charter §7: 'atomic? yes → produce + audit in the same Task'."""

    def producer(request: str) -> str:
        return f"PRODUCED[{request}]"

    def auditor(request: str, artifact: str) -> str:
        assert artifact.startswith("PRODUCED[")
        return "ACCEPTED"

    outcome = drive_lifecycle("fix typo", producer=producer, auditor=auditor)
    assert_branch(outcome, expected=Branch.ATOMIC)
    assert outcome.selection is None
    assert outcome.atomic_path is not None
    assert "classify→ATOMIC" in outcome.atomic_path
    assert "produce(" in outcome.atomic_path[1]
    assert "audit(" in outcome.atomic_path[2]


def test_staged_path_returns_selection_without_running_lifecycle() -> None:
    """Charter §7: 'atomic? no → Select applicable stages'."""

    outcome = drive_lifecycle(
        "implement OAuth2 across the project with several integrations"
    )
    assert_branch(outcome, expected=Branch.STAGED)
    assert outcome.atomic_path is None
    assert outcome.selection is not None
    # Default staged → canonical §7 sequence (example-014.1: general MBA
    # lifecycle names, not repo-build stage names).
    canonical = ("understanding", "selection", "plan",
                 "build", "verify", "deliver")
    assert outcome.selection.stages == canonical


def test_staged_path_honours_user_requested_stages() -> None:
    outcome = drive_lifecycle(
        "implement OAuth2",
        requested_stages=("plan", "deliver"),
    )
    assert outcome.selection is not None
    assert outcome.selection.stages == ("plan", "deliver")


def test_staged_path_with_unknown_stages_falls_back() -> None:
    outcome = drive_lifecycle(
        "implement OAuth2",
        requested_stages=("unicorn-stage",),
    )
    assert outcome.selection is not None
    # Unknown stages fall back to Understanding + Selection.
    assert outcome.selection.stages == ("understanding", "selection")


def test_select_stages_for_atomic_returns_none() -> None:
    classification = classify_request("fix typo")
    assert classification.branch is Branch.ATOMIC
    assert select_stages(classification) is None


def test_classify_rationale_mentions_signal() -> None:
    classification = classify_request("implement OAuth2")
    assert classification.matched_signal == "implement"
    assert "Charter §6" in classification.rationale


def test_classify_long_request_defaults_to_staged() -> None:
    long_request = " ".join(["word"] * 30)
    classification = classify_request(long_request)
    assert classification.branch is Branch.STAGED


def test_classify_short_request_defaults_to_atomic() -> None:
    short_request = "tweak this"
    classification = classify_request(short_request)
    assert classification.branch is Branch.ATOMIC


def test_staged_rationale_explains_canonical_sequence() -> None:
    outcome = drive_lifecycle("implement OAuth2")
    assert outcome.selection is not None
    rationale = outcome.selection.selection_rationale
    assert "canonical §7 sequence" in rationale
    for stage in ("Plan", "Build", "Verify", "Deliver"):
        assert stage in rationale, (
            f"rationale should name general MBA stage {stage!r}; got {rationale!r}"
        )
    for old_label in ("Foundation", "Primitives", "Runtime", "Acceptance"):
        assert old_label not in rationale, (
            f"rationale must not contain old build-order label {old_label!r}; got {rationale!r}"
        )


def test_assert_branch_raises_on_mismatch() -> None:
    outcome = drive_lifecycle("fix typo")
    with pytest.raises(AssertionError):
        assert_branch(outcome, expected=Branch.STAGED)
