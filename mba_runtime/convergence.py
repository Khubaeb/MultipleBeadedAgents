"""§8 adversarial loop.

> Doer produces claim or result
>           |
> Auditor challenges with reasons and evidence
>           |
> Doer fixes the result or disproves the finding
>           |
> Auditor rechecks
>           |
> Convergence or recorded non-convergence
>
> Convergence requires a verified fix or accepted proof. Agreement,
> confidence, reputation, elapsed time or a fixed turn count is not
> convergence.
>
> Convergence is **evidence-required**, not reason-synthesized (example-015.1):
> the runtime cannot backfill the Auditor's evidence and an ``ACCEPT``
> without Auditor-originated evidence is not convergence. After a
> ``FIND``, an ``ACCEPT`` also requires either a changed artefact
> (``RESOLUTION: changed``) or an explicit ``no-change-proof`` from the
> Auditor. The runtime cannot prove the Auditor truly reasoned; it
> can only require the evidence structure.

The runtime expresses this loop as
:func:`iterate_until_converged`. Each call to a Doer or Auditor session
corresponds to one round; ``max_rounds`` is the configured limit (no
fixed counts are convergence; the bound only stops the loop on
non-convergence).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


VERDICT_ACCEPT: str = "ACCEPT"
VERDICT_FIND: str = "FIND"
VERDICT_BLOCKED: str = "BLOCKED"
VERDICTS: tuple[str, ...] = (VERDICT_ACCEPT, VERDICT_FIND, VERDICT_BLOCKED)

# Valid resolution values for an ACCEPT verdict (Charter §8 + example-015.1).
RESOLUTION_CHANGED: str = "changed"
RESOLUTION_NO_CHANGE_PROOF: str = "no-change-proof"
RESOLUTIONS: tuple[str, ...] = (RESOLUTION_CHANGED, RESOLUTION_NO_CHANGE_PROOF)


class ConvergenceError(ValueError):
    """Raised when a §8 acceptance lacks the required evidence.

    Charter §8: "Convergence requires a verified fix or accepted proof.
    Agreement, confidence, reputation, elapsed time or a fixed turn
    count is not convergence." The loop refuses an ``ACCEPT`` whose
    ``reasons`` (or whose evidence chain) is empty, so a misbehaving
    Auditor cannot silently slip the §8 guarantee through.
    """


@dataclass(frozen=True)
class ParsedAuditorProtocol:
    """Structured protocol parsed from an Auditor's ``result.md`` body.

    The Auditor's ``result.md`` may carry a structured block::

        VERDICT: ACCEPT
        RESOLUTION: changed
        EVIDENCE:
        - <evidence item 1>
        - <evidence item 2>

    The fields are **optional**; the parser never raises on a missing
    field. ``_outcome_to_verdict`` decides how to interpret an empty
    evidence alongside a verdict label (downgrade to FIND; see
    Charter §8 + example-015.1).
    """

    verdict: str | None = None  # ACCEPT / FIND / BLOCKED
    resolution: str | None = None  # "changed" / "no-change-proof"
    evidence: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Verdict:
    """Auditor's verdict for one round."""

    verdict: str               # ACCEPT / FIND / BLOCKED
    reasons: tuple[str, ...]
    evidence: tuple[str, ...] = field(default_factory=tuple)
    resolution: str | None = None  # "changed" / "no-change-proof" / None

    @classmethod
    def accept(
        cls,
        *,
        reasons: tuple[str, ...],
        evidence: tuple[str, ...] = (),
        resolution: str | None = None,
    ) -> "Verdict":
        """Build an ``ACCEPT`` verdict.

        Both ``reasons`` and ``evidence`` are required and must be
        non-empty. Per Charter §8 an acceptance needs an accepted
        proof, not bare agreement, and per example-015.1 the evidence
        must originate from the Auditor — the runtime **never**
        backfills it. Empty ``reasons`` or empty ``evidence`` is a
        programming error and raises immediately so it cannot
        silently reach :func:`iterate_until_converged`.

        ``resolution`` records the Auditor's claim about the
        post-FIND state: ``"changed"`` when the artefact changed,
        or ``"no-change-proof"`` when the Auditor justifies
        acceptance without an artefact change. A round-0 ``ACCEPT``
        may carry any resolution (the loop does not gate on it).
        """

        if not reasons:
            raise ConvergenceError(
                "Verdict.accept requires non-empty reasons; bare "
                "ACCEPT is not convergence (Charter §8)."
            )
        if not evidence:
            raise ConvergenceError(
                "Verdict.accept requires non-empty Auditor-originated "
                "evidence; the runtime cannot backfill evidence and an "
                "ACCEPT without it is not convergence (Charter §8; "
                "example-015.1 evidence-required convergence)."
            )
        if resolution is not None and resolution not in RESOLUTIONS:
            raise ConvergenceError(
                f"Verdict.accept resolution={resolution!r} is not one "
                f"of {RESOLUTIONS}; only the canonical post-FIND "
                "resolutions are accepted"
            )
        return cls(
            verdict=VERDICT_ACCEPT,
            reasons=reasons,
            evidence=evidence,
            resolution=resolution,
        )

    @classmethod
    def find(cls, *, reasons: tuple[str, ...], evidence: tuple[str, ...] = ()) -> "Verdict":
        return cls(verdict=VERDICT_FIND, reasons=reasons, evidence=evidence)

    @classmethod
    def blocked(cls, *, reasons: tuple[str, ...]) -> "Verdict":
        return cls(verdict=VERDICT_BLOCKED, reasons=reasons)


# ---------------------------------------------------------------------------
# Auditor protocol parser
# ---------------------------------------------------------------------------


def _strip_section_prefix(line: str) -> str:
    """Strip a leading ``- `` or ``* `` bullet, return the inner text."""

    stripped = line.strip()
    if stripped.startswith("- "):
        return stripped[2:].strip()
    if stripped.startswith("* "):
        return stripped[2:].strip()
    return stripped


def parse_auditor_protocol(result_text: str) -> ParsedAuditorProtocol:
    """Parse the structured ``VERDICT`` / ``RESOLUTION`` / ``EVIDENCE`` block.

    The Auditor's ``result.md`` body may carry::

        VERDICT: ACCEPT
        RESOLUTION: changed
        EVIDENCE:
        - <evidence item 1>
        - <evidence item 2>

    The parser is **permissive**: unrecognised lines are ignored and
    any missing field returns ``None`` / empty tuple. The parser
    **never raises** — :func:`mba_runtime.lifecycle._outcome_to_verdict`
    decides how to interpret an empty evidence alongside a verdict
    label (downgrade to FIND; see Charter §8 + example-015.1).

    The case of the header keywords (``VERDICT:`` / ``RESOLUTION:``
    / ``EVIDENCE:`` / ``REASONS:``) is significant only for the
    verdict and resolution values, which are normalised to
    upper / lower case as appropriate.
    """

    verdict: str | None = None
    resolution: str | None = None
    evidence: list[str] = []
    reasons: list[str] = []
    current_section: str | None = None

    for raw_line in (result_text or "").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.upper().startswith("VERDICT:"):
            verdict = stripped[len("VERDICT:"):].strip().upper() or None
            current_section = None
            continue
        if stripped.upper().startswith("RESOLUTION:"):
            resolution = stripped[len("RESOLUTION:"):].strip().lower() or None
            current_section = None
            continue
        if stripped.upper().startswith("EVIDENCE:"):
            current_section = "evidence"
            continue
        if stripped.upper().startswith("REASONS:"):
            current_section = "reasons"
            continue
        if current_section == "evidence":
            evidence.append(_strip_section_prefix(stripped))
        elif current_section == "reasons":
            reasons.append(_strip_section_prefix(stripped))

    return ParsedAuditorProtocol(
        verdict=verdict,
        resolution=resolution,
        evidence=tuple(evidence),
        reasons=tuple(reasons),
    )


@dataclass(frozen=True)
class ConvergenceResult:
    """Outcome of :func:`iterate_until_converged`."""

    converged: bool
    rounds: int
    final_verdict: Verdict
    transcript: tuple[Verdict, ...]
    transcript_path: Path | None = None


# ---------------------------------------------------------------------------
# Convergence loop
# ---------------------------------------------------------------------------


# The Auditor runner is invoked each round with the per-round context
# and returns a Verdict. The Doer runner is invoked when the Auditor
# returned FIND; it must produce the next artefact (working.md + result.md
# + comment) before the Auditor re-checks. The session runners are
# injected so the runtime can be tested without invoking real AIs.
DoerRunner = Callable[["DoerRoundContext"], "DoerOutcome"]
AuditorRunner = Callable[["AuditorRoundContext"], Verdict]


@dataclass(frozen=True)
class DoerRoundContext:
    """Per-round context given to a Doer runner."""

    round_index: int
    bead_id: str
    session_dir: Path
    artefact_paths: dict[str, Path]
    prior_verdict: Verdict | None  # None on round 0; populated thereafter


@dataclass(frozen=True)
class AuditorRoundContext:
    """Per-round context given to an Auditor runner."""

    round_index: int
    bead_id: str
    session_dir: Path
    artefact_paths: dict[str, Path]


@dataclass(frozen=True)
class DoerOutcome:
    """What a Doer runner produces each round."""

    artefact_paths: dict[str, Path]
    note: str


def _hash_artefacts(artefact_paths: dict[str, Path]) -> str:
    """Return a stable content-hash of the artefacts' current bytes.

    Missing files are included as a sentinel so the hash changes when
    the file is created. The hash is a pure function of the artefacts'
    current contents and the path names; it is used by
    :func:`iterate_until_converged` to detect a real post-FIND
    artefact change versus an unchanged artefact accepted with a
    ``no-change-proof`` resolution (example-015.1).
    """

    parts: list[str] = []
    for name in sorted(artefact_paths):
        path = artefact_paths[name]
        try:
            data = path.read_bytes()
        except (FileNotFoundError, IsADirectoryError, OSError):
            data = b"<missing>"
        parts.append(f"{name}={data.hex()}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def iterate_until_converged(
    *,
    bead_id: str,
    doer_session_dir: Path,
    auditor_session_dir: Path,
    artefact_paths: dict[str, Path],
    doer_runner: DoerRunner,
    auditor_runner: AuditorRunner,
    max_rounds: int = 3,
    transcript_path: Path | None = None,
) -> ConvergenceResult:
    """Run the §8 adversarial loop until verdict = ACCEPT.

    Returns a :class:`ConvergenceResult`. The helper writes the
    verdict chain to ``transcript_path`` when supplied so the
    Orchestrator can render the convergence evidence next to the
    audit.

    Convergence is **evidence-required** (example-015.1). On every round:

    * An ``ACCEPT`` with empty evidence raises
      :class:`ConvergenceError`; the runtime cannot backfill it.
    * After a ``FIND`` (``round_index > 0``), an ``ACCEPT`` requires
      either a **changed** artefact (the content-hash differs from
      the prior round) or an explicit ``RESOLUTION: no-change-proof``
      on the verdict. An ``ACCEPT`` with an unchanged artefact and
      no ``no-change-proof`` is downgraded to ``FIND`` and the loop
      continues.
    """

    if max_rounds < 1:
        raise ValueError(f"max_rounds must be >= 1; got {max_rounds}")

    transcript: list[Verdict] = []
    prior_verdict: Verdict | None = None
    prior_artefact_hash: str | None = None
    converge_path = dict(artefact_paths)

    for round_index in range(max_rounds):
        doer_outcome = doer_runner(
            DoerRoundContext(
                round_index=round_index,
                bead_id=bead_id,
                session_dir=doer_session_dir,
                artefact_paths=converge_path,
                prior_verdict=prior_verdict,
            )
        )
        converge_path = dict(doer_outcome.artefact_paths or converge_path)
        current_artefact_hash = _hash_artefacts(converge_path)

        verdict = auditor_runner(
            AuditorRoundContext(
                round_index=round_index,
                bead_id=bead_id,
                session_dir=auditor_session_dir,
                artefact_paths=converge_path,
            )
        )
        transcript.append(verdict)

        if verdict.verdict == VERDICT_ACCEPT:
            # 1. Defensive: refuse empty evidence (Charter §8 +
            #    example-015.1). The bridge in
            #    :func:`mba_runtime.lifecycle._outcome_to_verdict`
            #    is the canonical place that downgrades an
            #    Auditor-side ``ACCEPT`` with no ``EVIDENCE`` to
            #    ``FIND``; this check catches a misbehaving
            #    auditor runner that bypasses the bridge.
            if not verdict.evidence:
                raise ConvergenceError(
                    f"Auditor returned ACCEPT on round {round_index + 1} "
                    "with empty evidence; an accepted proof requires "
                    "Auditor-originated evidence (Charter §8; "
                    "example-015.1 evidence-required convergence)."
                )
            # 2. Post-FIND check: ACCEPT after a FIND requires either
            #    a changed artefact (default) or a `no-change-proof`
            #    resolution. The runtime hashes the artefacts each
            #    round and compares to the prior round's hash; an
            #    unchanged artefact accepted without a no-change-proof
            #    is downgraded to FIND so the next round can supply
            #    one. The check is skipped on round 0 (no prior FIND
            #    state to compare against).
            if (
                prior_artefact_hash is not None
                and current_artefact_hash == prior_artefact_hash
                and verdict.resolution != RESOLUTION_NO_CHANGE_PROOF
            ):
                downgraded = Verdict.find(
                    reasons=(
                        f"post-FIND ACCEPT on round {round_index + 1} "
                        "with unchanged artefact but no "
                        "`no-change-proof` resolution; "
                        "downgraded to FIND (Charter §8; example-015.1).",
                    ),
                    evidence=verdict.evidence,
                )
                transcript[-1] = downgraded
                prior_verdict = downgraded
                prior_artefact_hash = current_artefact_hash
                continue

            _maybe_write_transcript(transcript_path, transcript, converged=True)
            return ConvergenceResult(
                converged=True,
                rounds=round_index + 1,
                final_verdict=verdict,
                transcript=tuple(transcript),
                transcript_path=transcript_path,
            )

        if verdict.verdict not in (VERDICT_FIND, VERDICT_BLOCKED):
            raise ValueError(
                f"Auditor returned unexpected verdict {verdict.verdict!r}; "
                f"expected one of {VERDICTS}"
            )

        prior_verdict = verdict
        prior_artefact_hash = current_artefact_hash

        # If the Auditor returned BLOCKED we stop the loop; convergence
        # is not a turn count but a verified fix or accepted proof.
        if verdict.verdict == VERDICT_BLOCKED:
            break

    blocked = Verdict.blocked(
        reasons=(
            f"loop exhausted after {max_rounds} rounds without verdict "
            f"= ACCEPT; the last verdict was {transcript[-1].verdict!r}",
        )
    )
    _maybe_write_transcript(transcript_path, transcript, converged=False)
    return ConvergenceResult(
        converged=False,
        rounds=len(transcript),
        final_verdict=blocked,
        transcript=tuple(transcript),
        transcript_path=transcript_path,
    )


def _maybe_write_transcript(
    path: Path | None, transcript: list[Verdict], *, converged: bool
) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"<!-- auto-generated by mba_runtime.convergence -->",
        f"- converged: {converged}",
        f"- rounds: {len(transcript)}",
    ]
    for index, verdict in enumerate(transcript, start=1):
        reasons = "; ".join(verdict.reasons) or "-"
        evidence = "; ".join(verdict.evidence) or "-"
        resolution = verdict.resolution or "-"
        lines.append(
            f"- round {index}: verdict={verdict.verdict}, "
            f"resolution={resolution}, "
            f"reasons={reasons}, "
            f"evidence={evidence}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
