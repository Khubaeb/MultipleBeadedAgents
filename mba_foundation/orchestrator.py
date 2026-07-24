"""§7 dynamic-lifecycle Orchestrator behaviour.

Acceptance row coverage (Foundation AC #11):

* §7 lifecycle ownership (F7): the classify-atomic-vs-staged and
  select-applicable-stages Orchestrator behaviour is installed by this
  stage as a rule-driven component (not omitted, not deferred to a
  later stage). A test drives an atomic request and a staged request
  through the installed behaviour and asserts each path takes the
  correct branch from ``docs/mba/charter.md`` §7.

This module is **rule-driven** — the policy lives in Python data and
the orchestration is the single ``drive_lifecycle`` entry point. The
Selection child Bead (``example-004.1`` in this Project's selection) does
not need to install its own copy; it inherits behaviour from here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Sequence


# ---------------------------------------------------------------------------
# Classification policy — verbatim from docs/mba/charter.md §6 + §7.
# ---------------------------------------------------------------------------

# Word-count thresholds are anchored to the spec narrative: atomic work
# has a single independently closable result; staged work spans more
# than one stage. The thresholds are intentionally generous so we err
# on the side of "staged" when in doubt — the Selection child reduces
# to atomic on closer inspection.
_ATOMIC_MAX_WORDS: int = 12

_ATOMIC_KEYWORDS: tuple[str, ...] = (
    "fix typo",
    "update readme",
    "rename",
    "tweak",
    "single edit",
    "one-line",
    "one-liner",
)

# Signals that the request is naturally staged (multi-stage, multi-Bead).
_STAGED_KEYWORDS: tuple[str, ...] = (
    "implement",
    "build",
    "design",
    "refactor",
    "introduce",
    "add stage",
    "set up pipeline",
    "oauth",
    "integration",
    "rollout",
    "roll out",
    "migration",
    "migrate",
    "epic",
    "across the project",
    "new module",
    "from scratch",
    "complete mba",
)

# Stage set per docs/mba/charter.md §6 + §7. Stages are dynamic: Selection
# decides which apply; we ship the canonical lifecycle so the rule-
# driven component has a defined vocabulary.
#
# Naming (example-014.1 audit): the user-facing lifecycle vocabulary is
# the general MBA one — Understanding → Selection → **Plan → Build →
# Verify → Deliver** — and never this project's internal build order.
# The bare names below are the public labels a user (or a worker
# prompt) ever sees. Old repo-build names are kept as deprecated
# aliases with a clear "renamed" pointer so existing citations still
# resolve, but the canonical `STAGE_*` constants point at the
# general vocabulary.
STAGE_UNDERSTANDING: str = "understanding"
STAGE_SELECTION: str = "selection"
STAGE_PLAN: str = "plan"
STAGE_BUILD: str = "build"
STAGE_VERIFY: str = "verify"
STAGE_DELIVER: str = "deliver"

# Deprecated repo-build aliases — kept so existing citations resolve.
# Prefer the canonical ``STAGE_PLAN`` / ``STAGE_BUILD`` / ``STAGE_VERIFY``
# / ``STAGE_DELIVER`` names in new code. See Charter §7 for the
# user-facing lifecycle vocabulary.
STAGE_FOUNDATION: str = STAGE_PLAN
STAGE_PRIMITIVES: str = STAGE_BUILD
STAGE_RUNTIME: str = STAGE_VERIFY
STAGE_ACCEPTANCE: str = STAGE_DELIVER

# Understanding's stage taxonomy (executable Beads).
EXECUTABLE_STAGES: tuple[str, ...] = (
    STAGE_PLAN,
    STAGE_BUILD,
    STAGE_VERIFY,
    STAGE_DELIVER,
)


class Branch(str, Enum):
    """§7 front-half branches.

    ``ATOMIC`` ⇢ "produce + audit in the same Task" (Charter §7).
    ``STAGED`` ⇢ "Select applicable stages" (Charter §7).
    """

    ATOMIC = "atomic"
    STAGED = "staged"


@dataclass(frozen=True)
class Classification:
    """Outcome of ``classify_request``."""

    branch: Branch
    rationale: str
    matched_signal: str | None        # the keyword/rule that determined the branch


@dataclass(frozen=True)
class StageSelection:
    """Outcome of ``select_stages`` for a staged request."""

    stages: tuple[str, ...]
    selection_rationale: str
    required: bool = True             # False ⇢ optional runtime stages.


@dataclass(frozen=True)
class LifecycleOutcome:
    """Final outcome of ``drive_lifecycle``."""

    classification: Classification
    selection: StageSelection | None      # Only set for STAGED.
    atomic_path: tuple[str, ...] | None   # "produce + audit in the same Task"

    @property
    def branch(self) -> Branch:
        return self.classification.branch


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def classify_request(request: str) -> Classification:
    """Classify a user request as ATOMIC or STAGED per Charter §6 + §7.

    The rule is data-driven:

    * Staged keywords ALWAYS promote. (User intent clearly requires
      more than one independently closable result.)
    * Atomic keywords with a short request (≤ ``_ATOMIC_MAX_WORDS`` words)
      keep the request atomic.
    * Otherwise default to ``STAGED``. (Selection can reduce; the
      reverse is not safe.)
    """

    text = request.strip().lower()

    for kw in _STAGED_KEYWORDS:
        if kw in text:
            return Classification(
                branch=Branch.STAGED,
                rationale=(
                    f"matched staged keyword {kw!r}; request needs more than "
                    f"one independently closable result per Charter §6."
                ),
                matched_signal=kw,
            )

    for kw in _ATOMIC_KEYWORDS:
        if kw in text:
            return Classification(
                branch=Branch.ATOMIC,
                rationale=(
                    f"matched atomic keyword {kw!r}; request maps to a "
                    f"single independently closable Task per Charter §6."
                ),
                matched_signal=kw,
            )

    if _word_count(text) <= _ATOMIC_MAX_WORDS:
        return Classification(
            branch=Branch.ATOMIC,
            rationale=(
                f"short request ({_word_count(text)} words ≤ "
                f"{_ATOMIC_MAX_WORDS}); one independently closable Task."
            ),
            matched_signal="length",
        )

    return Classification(
        branch=Branch.STAGED,
        rationale=(
            "longer request with no atomic keyword; default to STAGED so "
            "the Selection Bead can prune to a sub-Bead if appropriate."
        ),
        matched_signal="length",
    )


# ---------------------------------------------------------------------------
# Stage selection
# ---------------------------------------------------------------------------


# Default canonical stage set for a staged request. Selection can prune
# via ``required=False`` for optional stages. This list is the one the
# §7 lifecycle ("Understanding → Selection → Plan → Build → Verify →
# Deliver") reads when the user has not pre-selected stages.
_DEFAULT_STAGED_STAGES: tuple[str, ...] = (
    STAGE_UNDERSTANDING,
    STAGE_SELECTION,
    STAGE_PLAN,
    STAGE_BUILD,
    STAGE_VERIFY,
    STAGE_DELIVER,
)


def select_stages(
    classification: Classification,
    *,
    requested: Sequence[str] | None = None,
) -> StageSelection | None:
    """Choose the applicable stages for a given classification.

    * ATOMIC → ``None`` (Selection is not invoked).
    * STAGED → the canonical §7 sequence, intersected with
      ``requested`` if the user named stages explicitly.
    """

    if classification.branch is Branch.ATOMIC:
        return None

    if requested is None:
        return StageSelection(
            stages=_DEFAULT_STAGED_STAGES,
            selection_rationale=(
                "canonical §7 sequence: Understanding → Selection → "
                "Plan → Build → Verify → Deliver."
            ),
        )

    requested_set = tuple(s.lower() for s in requested)
    kept = tuple(s for s in _DEFAULT_STAGED_STAGES if s in requested_set)
    if not kept:
        # The user named at least one stage but none matched the canonical
        # vocabulary. That is itself a signal: fall back to Understanding
        # + Selection so a real Selection child can re-pick.
        kept = (STAGE_UNDERSTANDING, STAGE_SELECTION)
        rationale = (
            "no canonical stages requested; defaulting to Understanding + "
            "Selection so a real Selection child re-picks."
        )
    else:
        rationale = (
            f"Selection honoured the user-requested stages: "
            f"{', '.join(kept)}."
        )
    return StageSelection(stages=kept, selection_rationale=rationale)


# ---------------------------------------------------------------------------
# Full lifecycle driver
# ---------------------------------------------------------------------------


def drive_lifecycle(
    request: str,
    *,
    requested_stages: Sequence[str] | None = None,
    producer: Callable[[str], str] | None = None,
    auditor: Callable[[str, str], str] | None = None,
) -> LifecycleOutcome:
    """Drive the §7 front-half: classify → select (when staged) → produce+audit.

    The atomic path runs ``producer`` and ``auditor`` against the same
    request, both inside the same Task (per Charter §7: "produce + audit
    in the same Task"). The staged path returns the Selection outcome
    and the actual child Bead creation is the Selection child's
    responsibility, not Foundation's.
    """

    classification = classify_request(request)

    if classification.branch is Branch.ATOMIC:
        atomic_path: list[str] = []
        if producer is None or auditor is None:
            atomic_path.append("classify→ATOMIC")
            atomic_path.append("produce+audit in the same Task")
            return LifecycleOutcome(
                classification=classification,
                selection=None,
                atomic_path=tuple(atomic_path),
            )

        artifact = producer(request)
        atomic_path.append("classify→ATOMIC")
        atomic_path.append(f"produce({len(artifact)} chars)")
        finding = auditor(request, artifact)
        atomic_path.append(f"audit(verdict={finding!r})")
        return LifecycleOutcome(
            classification=classification,
            selection=None,
            atomic_path=tuple(atomic_path),
        )

    selection = select_stages(classification, requested=requested_stages)
    return LifecycleOutcome(
        classification=classification,
        selection=selection,
        atomic_path=None,
    )


# ---------------------------------------------------------------------------
# Verifier — used by Foundation tests and any later Selection child.
# ---------------------------------------------------------------------------


def assert_branch(outcome: LifecycleOutcome, *, expected: Branch) -> None:
    """Raise ``AssertionError`` if the lifecycle did not take the expected branch."""

    if outcome.branch is not expected:
        raise AssertionError(
            f"expected branch {expected!r}, got {outcome.branch!r}; "
            f"rationale: {outcome.classification.rationale}"
        )
