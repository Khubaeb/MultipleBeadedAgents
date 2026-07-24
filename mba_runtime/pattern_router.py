"""Pattern router.

The four canonical Understanding §4 patterns live here. The router
takes a :class:`TeamConfig` and produces a :class:`SessionPlan`
describing the per-session AI / hat / role it must stand up.

Crucially the router is the *label-to-topology* mapper; the topology
itself is verified, not just the label string. The four shapes —

* **(a) one AI both** — same AI for Doer and Auditor, in distinct
  sessions with opposing hats; the single AI has both ``doer`` and
  ``auditor`` capabilities.
* **(b) distinct AIs** — one AI per responsibility; the two AI ids
  differ.
* **(c) several sessions for one responsibility on one artefact** —
  the multi-session side has ``session_count > 1`` and the planner
  carries a ``combine_before_audit=True`` marker; the audit side may
  have one session.
* **(d) separated Doer/Auditor sessions even with one AI** — same AI
  id for both sides, in distinct sessions with opposing hats; the AI
  has both capabilities; the shape is recorded as a fresh-session
  guarantee but with the single-AI hat-pair flag for completeness.

The runtime refuses to proceed if the topology does not match the
declared pattern (e.g. a team labelled ``pattern=b`` with the same AI
on both sides fails the distinctness check).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .ai_resources import (
    AIResourceError,
    AIResourceRecord,
    TeamConfig,
)
from .constants import (
    DEFAULT_AUDITOR_HAT,
    DEFAULT_DOER_HAT,
    PATTERN_A,
    PATTERN_B,
    PATTERN_C,
    PATTERN_D,
    PATTERN_LABELS,
    RESP_AUDITOR,
    RESP_DOER,
)


class PatternError(AIResourceError):
    """Raised when a session plan does not match its declared pattern."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionSpec:
    """One concrete session: which AI, which hat, which responsibility.

    ``session_index`` is the position within the responsibility (e.g.
    ``0`` and ``1`` for two Doer sessions in pattern (c)). The runtime
    uses it to derive a unique ``.mba-work/<bead>/<session>/`` dirname
    that does not collide when two sessions share a hat.
    """

    responsibility: str                   # Doer / Auditor
    ai_id: str
    ai_label: str
    hat: str
    pattern: str                          # a / b / c / d
    session_index: int = 0
    use_readonly: bool = False
    isolation_reason: str = ""            # populated when use_readonly=True
    combine_before_audit: bool = False    # set for pattern (c) Doer sessions

    def session_name(self, bead_id: str) -> str:
        """Derive the §10 directory name for this session.

        The shape is ``<bead_id>-<hat-slug>-<idx>`` so two sessions
        sharing a hat (pattern c) get unique paths. The Primitives'
        ``ensure_layout`` keeps existing directories on second
        invocation; names are stable.
        """

        index_part = f"-{self.session_index}" if self.session_index else ""
        return f"{bead_id}-{_slugify(self.hat)}{index_part}"


@dataclass(frozen=True)
class SessionPlan:
    """Per-Bead session roster produced by the router."""

    bead_id: str
    pattern: str
    doer_sessions: tuple[SessionSpec, ...]
    auditor_sessions: tuple[SessionSpec, ...]

    def all_sessions(self) -> tuple[SessionSpec, ...]:
        return (*self.doer_sessions, *self.auditor_sessions)

    def sessions_for(self, responsibility: str) -> tuple[SessionSpec, ...]:
        if responsibility == RESP_DOER:
            return self.doer_sessions
        if responsibility == RESP_AUDITOR:
            return self.auditor_sessions
        raise PatternError(f"unknown responsibility {responsibility!r}")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    out: list[str] = []
    for char in text.lower():
        if char.isalnum() or char in "-_":
            out.append(char)
        elif char.isspace():
            out.append("-")
    slug = "".join(out).strip("-")
    return slug or "session"


def _verify_capabilities(record: AIResourceRecord, team: TeamConfig) -> None:
    doer_res = record.resource_by_id(team.doer.ai)
    auditor_res = record.resource_by_id(team.auditor.ai)
    if not doer_res.can_doer:
        raise PatternError(
            f"AI {doer_res.id!r} is configured as Doer but lacks the "
            f"'doer' capability; record.capabilities={doer_res.capabilities}"
        )
    if not auditor_res.can_auditor:
        raise PatternError(
            f"AI {auditor_res.id!r} is configured as Auditor but lacks "
            f"the 'auditor' capability; record.capabilities={auditor_res.capabilities}"
        )


def _enforce_pattern_topology(team: TeamConfig) -> None:
    """Verify the AI/session-count topology matches the declared pattern.

    The label-vs-shape check is the whole point of pattern enforcement:
    a team labelled ``pattern=b`` with the same AI on both sides is a
    single-AI-both (pattern a), not distinct AIs. The label would
    silently mis-state the topology, so we refuse.
    """

    if team.pattern not in PATTERN_LABELS:
        raise PatternError(
            f"unknown pattern {team.pattern!r}; expected one of {PATTERN_LABELS}"
        )
    if team.pattern == PATTERN_B:
        if team.doer.ai == team.auditor.ai:
            raise PatternError(
                f"team labelled pattern=b (distinct AIs) but Doer and "
                f"Auditor are both {team.doer.ai!r}; this is pattern=a "
                f"(one AI both), not pattern=b. Fix the pattern label "
                f"or pick a different auditor AI."
            )
    if team.pattern == PATTERN_A:
        if team.doer.ai != team.auditor.ai:
            raise PatternError(
                f"team labelled pattern=a (one AI both) but Doer is "
                f"{team.doer.ai!r} and Auditor is {team.auditor.ai!r}; "
                f"this is pattern=b (distinct AIs), not pattern=a."
            )
        if team.doer.hat == team.auditor.hat:
            raise PatternError(
                f"pattern=a requires opposing hats for the two "
                f"sessions; both halves wear the hat {team.doer.hat!r}."
            )
    if team.pattern == PATTERN_C:
        if team.doer.session_count < 2 and team.auditor.session_count < 2:
            raise PatternError(
                f"pattern=c requires multiple sessions for at least one "
                f"responsibility on a single artefact; got "
                f"Doer.session_count={team.doer.session_count}, "
                f"Auditor.session_count={team.auditor.session_count}."
            )
    if team.pattern == PATTERN_D:
        if team.doer.ai != team.auditor.ai:
            raise PatternError(
                f"pattern=d is the single-AI variant; both "
                f"responsibilities must use the same AI. Got "
                f"Doer={team.doer.ai!r}, Auditor={team.auditor.ai!r}."
            )
        if team.doer.hat == team.auditor.hat:
            raise PatternError(
                f"pattern=d requires opposing hats for the two "
                f"sessions even with one AI; both halves wear the hat "
                f"{team.doer.hat!r}."
            )


def route(
    record: AIResourceRecord,
    *,
    bead_id: str,
    team: TeamConfig,
    default_doer_hat: str = DEFAULT_DOER_HAT,
    default_auditor_hat: str = DEFAULT_AUDITOR_HAT,
) -> SessionPlan:
    """Map a team config to a per-session roster.

    The verifier runs **before** any session is created so a misconfigured
    pattern fails fast with a refusal-grade error.
    """

    _verify_capabilities(record, team)
    _enforce_pattern_topology(team)

    doer_resource = record.resource_by_id(team.doer.ai)
    auditor_resource = record.resource_by_id(team.auditor.ai)

    doer_hat = team.doer.hat or default_doer_hat
    auditor_hat = team.auditor.hat or default_auditor_hat

    doer_sessions: list[SessionSpec] = []
    for index in range(team.doer.session_count):
        doer_sessions.append(
            SessionSpec(
                responsibility=RESP_DOER,
                ai_id=doer_resource.id,
                ai_label=doer_resource.label,
                hat=doer_hat,
                pattern=team.pattern,
                session_index=index,
                combine_before_audit=(team.pattern == PATTERN_C),
            )
        )

    auditor_sessions: list[SessionSpec] = []
    for index in range(team.auditor.session_count):
        auditor_sessions.append(
            SessionSpec(
                responsibility=RESP_AUDITOR,
                ai_id=auditor_resource.id,
                ai_label=auditor_resource.label,
                hat=auditor_hat,
                pattern=team.pattern,
                session_index=index,
            )
        )

    return SessionPlan(
        bead_id=bead_id,
        pattern=team.pattern,
        doer_sessions=tuple(doer_sessions),
        auditor_sessions=tuple(auditor_sessions),
    )


def is_pattern_a(plan: SessionPlan, record: AIResourceRecord) -> bool:
    return plan.pattern == PATTERN_A


def is_pattern_b(plan: SessionPlan) -> bool:
    return plan.pattern == PATTERN_B


def is_pattern_c(plan: SessionPlan) -> bool:
    return plan.pattern == PATTERN_C


def is_pattern_d(plan: SessionPlan) -> bool:
    return plan.pattern == PATTERN_D
