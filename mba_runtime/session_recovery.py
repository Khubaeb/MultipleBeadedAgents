"""Session continuity + AI-resource fallback recovery pipeline.

This module implements the converged example-010.1 "thin orchestrator"
continuity requirements plus the User's session-protection requirement.
It is a **pure, deterministic library** (no wall-clock, no subprocess,
no ``bd`` calls) so it is fully unit-testable; time is injected by the
caller (the Orchestrator supplies the User's system clock at the edge).

Recovery order for one required dispatch (a Doer or Auditor session on a
Bead round):

1. **RESUME** — an MBA-owned worker session was recorded for the *exact*
   identity ``(bead, responsibility, organisational_role, stage, ai_id,
   effort)`` and is in a resumable status. Resuming first avoids a
   duplicate worker and repeated completed work.
2. **RELAUNCH** — no resumable exact match exists → start a fresh MBA
   session on the *same* AI (same model + effort).
3. **FALLBACK** — the required AI is unavailable → walk the ordered list
   of other *suitable* resources (resources capable of the
   responsibility). Every substitution is **recorded**
   (``prior_ai_id`` / ``new_ai_id`` + reason); there is **no** code path
   that swaps the AI silently, so there is **no silent model/effort
   downgrade**.
4. **BOUNDED RETRY / PROBE** — every suitable resource is unavailable:
   * reset time known  → schedule ≤ ``MAX_SCHEDULED_RETRIES`` retries,
     then the terminal state ``BLOCKED_PROVIDER_UNAVAILABLE``;
   * reset time unknown → ≤ ``MAX_PROBES_WHEN_RESET_UNKNOWN`` probes,
     each ≤ ``PROBE_INTERVAL_MAX_SECONDS``, then the terminal state
     ``PAUSED_NO_RESET_TIME``.
   Neither terminal state auto-closes the Bead (Charter §8 / §11).

User-session protection (never disturb the User's own sessions):

* The User may run their own Claude / OpenCode sessions in this same
  repository for personal purposes. Those sessions are **never**
  ``owner == "mba"``, so they are never resume-eligible and are recorded
  as *protected* — never probed, resumed, interrupted, or killed.
* Only sessions explicitly recorded as MBA-owned **and** matching the
  exact identity on all six fields are eligible for RESUME.
* Ambiguity is always resolved to **ASK_USER**, never to a silent
  action:
  - more than one exact MBA-owned resumable match → ASK_USER;
  - an MBA-owned resumable session on the *same Bead* that matches only
    *some* identity fields → ASK_USER (never resume or kill a
    near-match).

Runtime-owned audit trail: :class:`AuditTrail` is the **runtime-only
writer** of an append-only JSONL recovery record (mirrors the
sole-writer discipline in :mod:`mba_runtime.external_dispatch`). No
worker session writes it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from .ai_resources import AIResourceError, AIResourceRecord, TeamConfig
from .constants import RESP_AUDITOR, RESP_DOER


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OWNER_MBA: str = "mba"

# Recovery decisions.
RESUME: str = "RESUME"
RELAUNCH: str = "RELAUNCH"
FALLBACK: str = "FALLBACK"
RETRY_SCHEDULED: str = "RETRY_SCHEDULED"
PROBE_SCHEDULED: str = "PROBE_SCHEDULED"
ASK_USER: str = "ASK_USER"

# Terminal states (neither auto-closes the Bead).
BLOCKED_PROVIDER_UNAVAILABLE: str = "BLOCKED_PROVIDER_UNAVAILABLE"
PAUSED_NO_RESET_TIME: str = "PAUSED_NO_RESET_TIME"

# The six identity fields that make an MBA worker session unambiguously
# the "same" session. Resume is allowed only on an exact match of all
# six (plus MBA ownership + a resumable status).
_IDENTITY_FIELDS: tuple[str, ...] = (
    "bead_id",
    "responsibility",
    "organisational_role",
    "stage",
    "ai_id",
    "effort",
)

RESUMABLE_STATUSES: frozenset[str] = frozenset(
    {"paused", "interrupted", "in_progress", "incomplete"}
)
TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "closed", "failed"})

# Bounds for the all-unavailable state machine (example-010.1 B2 / B3).
MAX_SCHEDULED_RETRIES: int = 3
MAX_PROBES_WHEN_RESET_UNKNOWN: int = 3
PROBE_INTERVAL_MAX_SECONDS: float = 3600.0


class SessionRecoveryError(ValueError):
    """Raised on an unusable recovery input (never a silent fallthrough)."""


# ---------------------------------------------------------------------------
# Identity + recorded sessions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionIdentity:
    """The full identity of one MBA-owned worker session.

    Two sessions are the "same" only when all six fields are equal. The
    ``stage`` and ``effort`` fields are part of the identity so a
    resume never crosses stages and a substitution never silently
    changes the effort tier.
    """

    bead_id: str
    responsibility: str            # RESP_DOER / RESP_AUDITOR
    organisational_role: str       # the hat, e.g. "Implementation Lead"
    stage: str
    ai_id: str
    effort: str

    def matches(self, other: "SessionIdentity") -> bool:
        return all(
            getattr(self, name) == getattr(other, name)
            for name in _IDENTITY_FIELDS
        )

    def as_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in _IDENTITY_FIELDS}


@dataclass(frozen=True)
class RecordedSession:
    """One observed session: MBA-owned or a foreign (User) session.

    ``owner == "mba"`` marks an MBA-launched worker session. Any other
    owner value (``"user"``, ``"claude"``, ``"opencode"``, ``""`` /
    unknown) is a foreign session that MBA must never disturb.
    """

    identity: SessionIdentity
    session_id: str
    owner: str
    status: str
    tool: str = ""

    @property
    def is_mba_owned(self) -> bool:
        return self.owner == OWNER_MBA

    @property
    def is_resumable(self) -> bool:
        return self.status in RESUMABLE_STATUSES


@dataclass(frozen=True)
class RecoveryDecision:
    """The single recovery action for one required dispatch."""

    action: str
    identity: SessionIdentity
    session_id: Optional[str] = None       # set for RESUME
    ai_id: Optional[str] = None            # target AI for RELAUNCH / FALLBACK
    next_attempt_at: Optional[float] = None  # set for RETRY / PROBE
    reason: str = ""
    protected_session_ids: tuple[str, ...] = ()   # foreign sessions left untouched
    ask_user_candidates: tuple[str, ...] = ()     # ambiguous MBA session ids

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "identity": self.identity.as_dict(),
            "session_id": self.session_id,
            "ai_id": self.ai_id,
            "next_attempt_at": self.next_attempt_at,
            "reason": self.reason,
            "protected_session_ids": list(self.protected_session_ids),
            "ask_user_candidates": list(self.ask_user_candidates),
        }


# ---------------------------------------------------------------------------
# Step 1-2: resume-first, else relaunch, else ask-user (session protection)
# ---------------------------------------------------------------------------


def protected_sessions(
    candidates: list[RecordedSession],
) -> tuple[RecordedSession, ...]:
    """Return the foreign (non-MBA) sessions that must be left untouched."""

    return tuple(c for c in candidates if not c.is_mba_owned)


def classify_recovery(
    want: SessionIdentity, candidates: list[RecordedSession]
) -> RecoveryDecision:
    """Decide RESUME / RELAUNCH / ASK_USER for the wanted identity.

    * Foreign (non-MBA) sessions are recorded as *protected* and never
      influence the decision beyond being reported in
      ``protected_session_ids`` — they are never probed, resumed,
      interrupted, or killed.
    * A **single** exact MBA-owned resumable match → RESUME.
    * More than one exact match, or an MBA-owned resumable near-match on
      the same Bead → ASK_USER (never a silent action).
    * Otherwise → RELAUNCH a fresh MBA session on the same AI.
    """

    protected = protected_sessions(candidates)
    protected_ids = tuple(c.session_id for c in protected)

    exact = [
        c
        for c in candidates
        if c.is_mba_owned and c.is_resumable and c.identity.matches(want)
    ]
    if len(exact) == 1:
        return RecoveryDecision(
            action=RESUME,
            identity=want,
            session_id=exact[0].session_id,
            ai_id=want.ai_id,
            reason="exact MBA-owned resumable session found; resume-first",
            protected_session_ids=protected_ids,
        )
    if len(exact) > 1:
        return RecoveryDecision(
            action=ASK_USER,
            identity=want,
            reason=(
                "multiple exact MBA-owned resumable sessions match this "
                "identity; ambiguous — asking the User rather than picking"
            ),
            protected_session_ids=protected_ids,
            ask_user_candidates=tuple(c.session_id for c in exact),
        )

    near = [
        c
        for c in candidates
        if c.is_mba_owned
        and c.is_resumable
        and c.identity.bead_id == want.bead_id
        and not c.identity.matches(want)
    ]
    if near:
        return RecoveryDecision(
            action=ASK_USER,
            identity=want,
            reason=(
                "an MBA-owned resumable session on the same Bead matches "
                "only some identity fields; ambiguous — asking the User, "
                "not resuming or killing a near-match"
            ),
            protected_session_ids=protected_ids,
            ask_user_candidates=tuple(c.session_id for c in near),
        )

    return RecoveryDecision(
        action=RELAUNCH,
        identity=want,
        ai_id=want.ai_id,
        reason="no resumable MBA-owned session; relaunch a fresh session on the same AI",
        protected_session_ids=protected_ids,
    )


# ---------------------------------------------------------------------------
# Step 3: ordered suitable-resource fallback (no silent downgrade)
# ---------------------------------------------------------------------------


def suitable_resources(
    record: AIResourceRecord, team: TeamConfig, responsibility: str
) -> tuple[str, ...]:
    """Ordered AI ids capable of ``responsibility`` — team's AI first.

    The team's configured AI for the responsibility leads (when it is
    capable), followed by every other catalogue resource that declares
    the capability, in catalogue order. Duplicates are removed.
    """

    if responsibility == RESP_DOER:
        primary, capability = team.doer.ai, "doer"
    elif responsibility == RESP_AUDITOR:
        primary, capability = team.auditor.ai, "auditor"
    else:
        raise SessionRecoveryError(
            f"responsibility must be {RESP_DOER!r} or {RESP_AUDITOR!r}; "
            f"got {responsibility!r}"
        )

    def _capable(ai_id: str) -> bool:
        try:
            res = record.resource_by_id(ai_id)
        except AIResourceError:
            return False
        return capability in res.capabilities

    order: list[str] = []
    if _capable(primary):
        order.append(primary)
    for res in record.resources:
        if res.id == primary:
            continue
        if capability in res.capabilities:
            order.append(res.id)
    return tuple(dict.fromkeys(order))


def next_available(
    order: tuple[str, ...], unavailable: frozenset[str] | set[str]
) -> Optional[str]:
    """First AI id in ``order`` that is not in ``unavailable`` (or None)."""

    for ai_id in order:
        if ai_id not in unavailable:
            return ai_id
    return None


@dataclass(frozen=True)
class Substitution:
    """A recorded AI substitution — the only way an AI ever changes."""

    prior_ai_id: str
    new_ai_id: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {
            "prior_ai_id": self.prior_ai_id,
            "new_ai_id": self.new_ai_id,
            "reason": self.reason,
        }


def record_substitution(
    prior_ai_id: str, new_ai_id: str, *, reason: str
) -> Substitution:
    """Build a :class:`Substitution`, refusing a silent or no-op swap.

    A substitution must carry a non-empty reason and must actually
    change the AI. There is no code path that swaps the AI without one
    of these records, so a model/effort downgrade can never be silent.
    """

    if not reason.strip():
        raise SessionRecoveryError(
            "a substitution must record a non-empty reason (no silent "
            "model/effort downgrade)"
        )
    if prior_ai_id == new_ai_id:
        raise SessionRecoveryError(
            f"substitution prior and new AI are both {prior_ai_id!r}; "
            f"that is not a substitution"
        )
    return Substitution(prior_ai_id=prior_ai_id, new_ai_id=new_ai_id, reason=reason)


# ---------------------------------------------------------------------------
# Step 4: bounded retry / probe when every suitable resource is unavailable
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AllUnavailablePlan:
    """The bounded schedule when every suitable resource is unavailable."""

    action: str                       # RETRY_SCHEDULED / PROBE_SCHEDULED / terminal
    next_attempt_at: Optional[float] = None
    attempts_used: int = 0
    probes_used: int = 0
    reason: str = ""


def plan_all_unavailable(
    *,
    reset_times: dict[str, Optional[float]],
    now: float,
    attempts_so_far: int = 0,
    probes_so_far: int = 0,
    backoff_seconds: float = 0.0,
    max_scheduled_retries: int = MAX_SCHEDULED_RETRIES,
    max_probes: int = MAX_PROBES_WHEN_RESET_UNKNOWN,
    probe_interval_seconds: float = PROBE_INTERVAL_MAX_SECONDS,
) -> AllUnavailablePlan:
    """Bounded schedule for the all-unavailable case.

    ``reset_times`` maps each unavailable resource id to its stated
    reset time (epoch seconds) or ``None`` when the provider states no
    reset time.

    * Any unknown reset time → the **probe** path: at most
      ``max_probes`` probes, each after ``min(probe_interval_seconds,
      PROBE_INTERVAL_MAX_SECONDS)``; exhaustion → ``PAUSED_NO_RESET_TIME``.
    * All reset times known → the **scheduled-retry** path: retry at
      ``max(reset_times) + backoff_seconds`` (retry once every provider
      is expected back, avoiding partial-availability thrash — the
      example-010.1 converged design), bounded by ``max_scheduled_retries``;
      exhaustion → ``BLOCKED_PROVIDER_UNAVAILABLE``.

    Neither terminal state closes the Bead.
    """

    if not reset_times:
        raise SessionRecoveryError(
            "plan_all_unavailable requires at least one unavailable "
            "resource with a reset entry"
        )

    any_unknown = any(v is None for v in reset_times.values())
    if any_unknown:
        if probes_so_far >= max_probes:
            return AllUnavailablePlan(
                action=PAUSED_NO_RESET_TIME,
                probes_used=probes_so_far,
                reason=(
                    f"no reset time known and probe budget "
                    f"({max_probes}) exhausted; paused for User decision "
                    f"(Bead stays open)"
                ),
            )
        interval = min(probe_interval_seconds, PROBE_INTERVAL_MAX_SECONDS)
        return AllUnavailablePlan(
            action=PROBE_SCHEDULED,
            next_attempt_at=now + interval,
            probes_used=probes_so_far + 1,
            reason=(
                f"reset time unknown; bounded probe "
                f"{probes_so_far + 1}/{max_probes} in {interval:.0f}s"
            ),
        )

    if attempts_so_far >= max_scheduled_retries:
        return AllUnavailablePlan(
            action=BLOCKED_PROVIDER_UNAVAILABLE,
            attempts_used=attempts_so_far,
            reason=(
                f"scheduled-retry budget ({max_scheduled_retries}) "
                f"exhausted; Bead blocked for User decision (stays open)"
            ),
        )
    known = [v for v in reset_times.values() if v is not None]
    next_at = max(known) + backoff_seconds
    return AllUnavailablePlan(
        action=RETRY_SCHEDULED,
        next_attempt_at=next_at,
        attempts_used=attempts_so_far + 1,
        reason=(
            f"all providers reset by {max(known):.0f}; scheduled retry "
            f"{attempts_so_far + 1}/{max_scheduled_retries}"
        ),
    )


# ---------------------------------------------------------------------------
# Whole pipeline (resume-first → relaunch → fallback → bounded retry)
# ---------------------------------------------------------------------------


def plan_recovery(
    *,
    want: SessionIdentity,
    candidates: list[RecordedSession],
    order: tuple[str, ...],
    unavailable: frozenset[str] | set[str],
    reset_times: Optional[dict[str, Optional[float]]] = None,
    now: float = 0.0,
    attempts_so_far: int = 0,
    probes_so_far: int = 0,
    backoff_seconds: float = 0.0,
) -> RecoveryDecision:
    """Single entry point: the whole recovery order in one call.

    ``order`` is the ordered suitable-resource list from
    :func:`suitable_resources`; ``unavailable`` is the set of AI ids
    currently rate-limited / unavailable; ``reset_times`` maps each
    unavailable id to its reset epoch (or ``None``).
    """

    base = classify_recovery(want, candidates)
    if base.action in (RESUME, ASK_USER):
        return base

    # base.action == RELAUNCH — relaunch only if the wanted AI is up.
    if want.ai_id not in unavailable:
        return base

    # Wanted AI is unavailable → ordered fallback to another suitable one.
    target = next_available(order, unavailable)
    if target is not None:
        sub = record_substitution(
            want.ai_id,
            target,
            reason=(
                f"{want.ai_id} unavailable; ordered fallback to suitable "
                f"resource {target} (recorded — no silent downgrade)"
            ),
        )
        return RecoveryDecision(
            action=FALLBACK,
            identity=replace(want, ai_id=target),
            ai_id=target,
            reason=sub.reason,
            protected_session_ids=base.protected_session_ids,
        )

    # Every suitable resource is unavailable → bounded retry / probe.
    plan = plan_all_unavailable(
        reset_times=reset_times or {ai: None for ai in order},
        now=now,
        attempts_so_far=attempts_so_far,
        probes_so_far=probes_so_far,
        backoff_seconds=backoff_seconds,
    )
    return RecoveryDecision(
        action=plan.action,
        identity=want,
        next_attempt_at=plan.next_attempt_at,
        reason=plan.reason,
        protected_session_ids=base.protected_session_ids,
    )


# ---------------------------------------------------------------------------
# Runtime-owned audit trail (runtime is the sole writer)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditTrail:
    """Append-only JSONL recovery audit trail.

    The **runtime is the sole writer** of this file (mirrors the
    sole-writer discipline in :mod:`mba_runtime.external_dispatch`): no
    worker session appends to it. Each row records one recovery
    decision / substitution / schedule so the User can inspect exactly
    what MBA did — and, crucially, which foreign sessions it left
    untouched.
    """

    path: Path

    def append(self, row: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    def append_decision(self, decision: RecoveryDecision) -> None:
        self.append(decision.as_dict())

    def read_rows(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, object]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows
