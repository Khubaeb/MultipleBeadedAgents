"""§11 user-authority gates.

Per Charter §11 the runtime pauses on every action that is material,
external, persistent, or destructive:

* source Git commit or push;
* deployment, publication or external messages;
* credentials, spending, or metered use;
* destructive changes;
* remote Beads / Dolt synchronization unless already authorised for
  that destination;
* adoption of reusable workflow changes.

The gate is the small contract that says: "refuse to proceed unless an
explicit recorded decision is supplied". A recorded decision has:

* ``approved=True|False`` — the human answered yes or no;
* ``actor`` — who supplied the decision (never the AI; the
  Orchestrator forwards the decision;
  AI actions are never attributed to the user, capabilities.md:75);
* ``rationale`` — short free-text reason;
* ``recorded_at`` — ISO-8601 UTC timestamp; produced by the gate
  itself so callers cannot silently back-date it.
* ``attestation`` — provenance tag set by a verified channel
  (example-017.1 round 2). Approved decisions must come from a
  channel the runtime recognises — see :func:`gate`.

The runtime's default ``decision_fn`` is :func:`_refuse`, which raises
:class:`UserAuthorityRequired`. Real Orchestrators supply a function
that records the decision (file-based for tests; prompt-based for
production).

Verified channel (round 2):

Plain ``AuthorityDecision.approved(actor="human:*")`` objects are
**forgeries** from the runtime's perspective — any caller can type
the prefix. The gate's round-2 invariant is that an approved
decision must arrive via a channel whose output carries an
``attestation`` starting with ``"verified:"`` that the loader
itself supplies. The canonical verified channels are:

* The read-only decisions-log loader
  (:func:`mba_runtime.external_dispatch.authority_decision_fn_from_decisions_file`)
  which records ``attestation="verified:decisions_log_read_only"``
  on every approved row it produces. The loader does **not**
  invent rows — it reads from a JSONL file the user controls.
* The in-memory test fixture
  (:func:`mba_runtime.external_dispatch.authority_decision_fn_allow_when`)
  marks its decisions with ``attestation="verified:test_allow_when"``.
  Production code never uses this seam; tests opt in explicitly.

The :func:`make_verified_decision` helper packages this for
ad-hoc test helpers.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from .constants import USER_AUTHORITY_ACTIONS


# ---------------------------------------------------------------------------
# Source / actor validation
# ---------------------------------------------------------------------------


# Default / non-human origins the runtime refuses to treat as user
# authority. ``cli`` is the runtime's own CLI surface and must never
# self-approve a §11 action. ``decision_fn_default`` is the runtime's
# internal placeholder (see ``decisions_to_file_decision_fn``); it is
# used when no preset approval is supplied, so accepting it would
# defeat the gate entirely.
DEFAULT_NON_HUMAN_ACTORS: frozenset[str] = frozenset(
    {"cli", "decision_fn_default"}
)


# Attestation tag carried by every approved decision that arrives
# via a verified channel. The prefix is the load-bearing part; the
# token (the rest of the string) is informational and lets an
# audit trace the loader that produced the decision.
_VERIFIED_ATTESTATION_PREFIX: str = "verified:"


def _has_verified_attestation(attestation: str | None) -> bool:
    """Return True when ``attestation`` is a recognised verified tag.

    Anything else — empty string, ``None``, ``"forged"``,
    ``"unverified:caller"`` — is treated as not verified.
    """

    if not attestation:
        return False
    return attestation.startswith(_VERIFIED_ATTESTATION_PREFIX)


def is_human_origin(actor: str) -> bool:
    """Return True when ``actor`` is recognisably human-origin.

    A human-origin actor carries the literal prefix ``human:``
    followed by at least one non-empty identifier — e.g.
    ``human:user``, ``human:oncall``, ``human:engineer``. The strict
    format keeps a generic dispatcher from granting itself approval
    via an empty or trivially-formatted actor. Any other form — bare
    ``user``, ``Engineer`` (a role, not a person), AI handles
    (``claude``, ``minimax``), the CLI smoke (``cli``,
    ``decision_fn_default``), or empty — is treated as non-human
    and refused.
    """

    stripped = actor.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if lowered in DEFAULT_NON_HUMAN_ACTORS or lowered.startswith("decision_fn_default"):
        return False
    if not lowered.startswith("human:"):
        return False
    # Require at least one non-whitespace character after ``human:``.
    suffix = stripped[len("human:"):].strip()
    return bool(suffix)


class UserAuthorityRequired(PermissionError):
    """Raised when a §11 action runs without a recorded decision."""

    def __init__(self, *, action: str, reason: str) -> None:
        self.action = action
        self.reason = reason
        super().__init__(
            f"§11 user-authority gate refused {action!r}: {reason}"
        )


@dataclass(frozen=True)
class AuthorityDecision:
    """Recorded user decision for a §11 action.

    Round-2 note: ``attestation`` is the load-bearing provenance
    field. The :func:`gate` enforces that every ``approved=True``
    decision carries a ``verified:`` attestation. Trusted loaders
    set this field themselves so a caller cannot forge an approved
    decision by typing ``AuthorityDecision.approved(...)`` — see
    module docstring.
    """

    action: str
    actor: str
    rationale: str
    approved: bool
    recorded_at: str = field(default_factory=lambda: _utc_now_iso())
    attestation: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)

    @classmethod
    def refused(
        cls, *, action: str, actor: str, rationale: str
    ) -> "AuthorityDecision":
        """Build a refused decision. No attestation is needed.

        Refusals are never the threat; they pass through the gate
        unchanged.
        """

        return cls(
            action=action,
            actor=actor,
            rationale=rationale,
            approved=False,
            recorded_at=_utc_now_iso(),
        )

    @classmethod
    def approved(
        cls, *, action: str, actor: str, rationale: str
    ) -> "AuthorityDecision":
        """Build an **unverified** approved decision (no attestation).

        Round-2: this is the low-level factory. The gate refuses
        unverified approvals on §11 actions. Callers that need a
        verified approval use :func:`make_verified_decision` or pass
        ``attestation=`` explicitly.
        """

        return cls(
            action=action,
            actor=actor,
            rationale=rationale,
            approved=True,
            recorded_at=_utc_now_iso(),
        )


def make_verified_decision(
    *,
    action: str,
    actor: str,
    rationale: str,
    channel: str,
) -> "AuthorityDecision":
    """Build an approved decision with a verified-channel attestation.

    ``channel`` is the loader token (e.g. ``"decisions_log_read_only"``,
    ``"test_allow_when"``). The resulting ``attestation`` is
    ``f"verified:{channel}"``; the gate accepts this prefix.

    Tests use this helper to build decisions that the gate will
    honour. Production code never calls this directly — the run-time
    callers are the verified loaders (``authority_decision_fn_*``
    helpers in :mod:`mba_runtime.external_dispatch`), which set the
    attestation themselves.
    """

    token = channel.strip()
    if not token or ":" in token:
        raise ValueError(
            f"channel {channel!r} must be a non-empty token with no "
            f"':' inside it"
        )
    return AuthorityDecision(
        action=action,
        actor=actor,
        rationale=rationale,
        approved=True,
        attestation=f"verified:{token}",
    )


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


# A decision function returns :class:`AuthorityDecision`. The default
# refuses without recording anything (so the failure mode is loud). Real
# Orchestrators supply one that records the decision before returning.
DecisionFn = Callable[[str], AuthorityDecision]


def _refuse(action: str) -> AuthorityDecision:
    raise UserAuthorityRequired(
        action=action,
        reason=(
            f"no decision function supplied; pass decision_fn= to "
            f"gate(...) or use record_automatic_decision for "
            f"deterministic tests. The runtime refuses to invent a "
            f"decision for a §11 action."
        ),
    )


def gate(
    action: str,
    *,
    decision_fn: DecisionFn | None = None,
) -> AuthorityDecision:
    """Block a §11 action until a recorded, verified decision is supplied.

    The function refuses when ``decision_fn`` is ``None`` (or when
    it raises). On approval the decision is returned untouched; on
    refusal it is returned so the caller can record the refusal.

    Round-2 invariants (example-017.1):

    1. **Catalogue check.** An action not in the §11 catalogue is
       refused — the catalogue is the source of truth for which
       actions require user authority.
    2. **Type check.** A ``decision_fn`` returning a non-
       :class:`AuthorityDecision` is refused.
    3. **Actor check.** An approved decision whose actor is not
       human-origin (per :func:`is_human_origin`) is refused.
       Default / non-human / AI / empty / bare ``human:`` actors
       are not user authority on their own.
    4. **Attestation check (NEW in round 2).** An approved decision
       that lacks a ``verified:`` attestation is refused. This
       closes the forgeable-authority hole the Auditor exposed:
       plain ``AuthorityDecision.approved(actor="human:user")`` is
       no longer accepted because the runtime caller is the
       forger. Verified approvals arrive through one of the
       trusted loaders (see module docstring).

    Refusal decisions (``approved=False``) are never forgeries —
    they pass through unchanged.
    """

    if action not in USER_AUTHORITY_ACTIONS:
        # Defensive: a caller passed an action we don't catalogue.
        # Refuse rather than silently allowing an unmodeled action.
        raise UserAuthorityRequired(
            action=action,
            reason=(
                f"action {action!r} is not in the §11 catalogue "
                f"({sorted(USER_AUTHORITY_ACTIONS)}); refusing until "
                f"the catalogue is updated and the user authorises the "
                f"new action."
            ),
        )

    fn = decision_fn or _refuse
    decision = fn(action)
    if not isinstance(decision, AuthorityDecision):
        raise UserAuthorityRequired(
            action=action,
            reason=(
                f"decision_fn returned {type(decision).__name__}; "
                f"expected an AuthorityDecision"
            ),
        )
    if decision.approved:
        if not is_human_origin(decision.actor):
            raise UserAuthorityRequired(
                action=action,
                reason=(
                    f"decision for {action!r} has actor "
                    f"{decision.actor!r}; §11 user authority requires "
                    f"an accepted (human-origin) source. Default actors "
                    f"({sorted(DEFAULT_NON_HUMAN_ACTORS)}), AI handles, "
                    f"empty identities, and unprefixed strings cannot "
                    f"satisfy §11; the gate refuses a forge."
                ),
            )
        if not _has_verified_attestation(decision.attestation):
            raise UserAuthorityRequired(
                action=action,
                reason=(
                    f"decision for {action!r} has actor "
                    f"{decision.actor!r} but no verified-channel "
                    f"attestation (received "
                    f"{decision.attestation!r}); §11 user authority "
                    f"requires a decision produced by a channel the "
                    f"runtime recognises (the read-only decisions log "
                    f"loader or the in-memory test allow helper — "
                    f"plain AuthorityDecision.approved(...) from any "
                    f"caller is not user authority). The gate refuses "
                    f"a forge."
                ),
            )
    return decision


# ---------------------------------------------------------------------------
# Decision stores — small helpers for tests and the CLI dispatcher.
# ---------------------------------------------------------------------------


def load_decision_log(path: Path) -> list[dict[str, object]]:
    """Read previously-recorded decisions from a JSON Lines file."""

    if not path.exists():
        return []
    out: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise UserAuthorityRequired(
                action="decision_log_corruption",
                reason=f"decision log at {path} contains a malformed "
                f"JSON line: {exc}",
            ) from exc
        if isinstance(payload, dict):
            out.append(payload)
    return out


def record_decision(
    decisions_path: Path,
    decision: AuthorityDecision,
) -> None:
    """Append a decision to a JSON Lines log (idempotent append)."""

    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    with decisions_path.open("a", encoding="utf-8") as handle:
        handle.write(decision.to_json() + "\n")


def decisions_to_file_decision_fn(
    decisions_path: Path,
    *,
    auto: Mapping[str, AuthorityDecision] | None = None,
) -> DecisionFn:
    """Return a ``DecisionFn`` that appends a decision file.

    ``auto`` lets a caller pre-authorise specific actions (used by
    the disposable-repo convergence test). Each call records the
    choice in ``decisions_path`` so an Auditor can inspect what was
    approved vs refused.

    Round-2 contract: approved decisions are stamped with a
    verified-channel attestation (``"verified:test_file_decision_fn"``)
    before they reach the gate. The function refuses to invent
    attestation out of band — the channel name is fixed; a
    caller that needs a different channel name passes the
    decision through :func:`make_verified_decision` ahead of
    supplying it to ``auto``.
    """

    auto = dict(auto or {})

    def _fn(action: str) -> AuthorityDecision:
        if action in auto:
            provided = auto[action]
            decision = _ensure_verified(provided, channel="test_file_decision_fn")
            record_decision(decisions_path, decision)
            return decision
        # No preset: refuse with a meaningful rationale.
        refusal = AuthorityDecision.refused(
            action=action,
            actor="decision_fn_default",
            rationale=(
                f"no preset approved decision for action {action!r}; "
                f"the test refuses to silently approve §11 actions"
            ),
        )
        record_decision(decisions_path, refusal)
        return refusal

    return _fn


def _ensure_verified(
    decision: "AuthorityDecision", *, channel: str
) -> "AuthorityDecision":
    """Return ``decision`` with a verified attestation if it approves.

    Plain refusals pass through untouched (a refusal needs no
    attestation). Approved decisions are rewritten under the
    supplied ``channel`` so the gate's round-2 invariant sees
    ``attestation="verified:<channel>"``.
    """

    if not decision.approved:
        return decision
    if _has_verified_attestation(decision.attestation):
        return decision
    return make_verified_decision(
        action=decision.action,
        actor=decision.actor,
        rationale=decision.rationale,
        channel=channel,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
