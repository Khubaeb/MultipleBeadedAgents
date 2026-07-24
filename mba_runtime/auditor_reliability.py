"""Auditor reliability guards.

Runtime code cannot prove that an Auditor truly reasoned. It can,
however, fail closed when an ``ACCEPT`` conflicts with facts supplied by
the Orchestrator or earlier evidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .convergence import Verdict


@dataclass(frozen=True)
class EvidenceConflict:
    """A known-fact contradiction found in an Auditor result."""

    known_fact: str
    auditor_claim: str
    evidence: str

    def as_reason(self) -> str:
        return (
            f"Auditor ACCEPT conflicts with known fact: {self.known_fact}; "
            f"claim={self.auditor_claim}; evidence={self.evidence}"
        )


@dataclass(frozen=True)
class ReliabilityIncident:
    """One model-role reliability issue."""

    bead_id: str
    model: str
    role: str
    issue: str
    evidence: str

    def as_dict(self) -> dict[str, str]:
        return {
            "bead_id": self.bead_id,
            "model": self.model,
            "role": self.role,
            "issue": self.issue,
            "evidence": self.evidence,
        }


def downgrade_accept_on_conflicts(
    verdict: Verdict,
    conflicts: tuple[EvidenceConflict, ...],
) -> Verdict:
    """Turn ``ACCEPT`` into unresolved ``FIND`` when conflicts exist."""

    if verdict.verdict != "ACCEPT" or not conflicts:
        return verdict
    return Verdict.find(
        reasons=tuple(conflict.as_reason() for conflict in conflicts),
        evidence=tuple(conflict.evidence for conflict in conflicts) + verdict.evidence,
    )


def render_compact_audit_packet(
    *,
    bead_id: str,
    known_facts: tuple[str, ...],
    artefact_paths: tuple[Path, ...] = (),
    prior_findings: tuple[str, ...] = (),
) -> str:
    """Render a small, contradiction-resistant packet for an Auditor."""

    lines = [
        f"# Compact audit packet: {bead_id}",
        "",
        "## Known facts",
    ]
    lines.extend(f"- {fact}" for fact in (known_facts or ("None supplied.",)))
    if artefact_paths:
        lines.append("")
        lines.append("## Artefacts to inspect")
        lines.extend(f"- `{path}`" for path in artefact_paths)
    if prior_findings:
        lines.append("")
        lines.append("## Prior findings")
        lines.extend(f"- {finding}" for finding in prior_findings)
    lines.append("")
    lines.append("## Auditor rule")
    lines.append("- If your ACCEPT contradicts any known fact, return FIND or BLOCKED.")
    return "\n".join(lines) + "\n"


def append_reliability_incident(path: Path, incident: ReliabilityIncident) -> None:
    """Append one reliability incident to a local JSONL record."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(incident.as_dict(), sort_keys=True) + "\n")


def unreliable_model_role_pairs(path: Path) -> set[tuple[str, str]]:
    """Return ``(model, role)`` pairs that already have incidents."""

    if not path.exists():
        return set()
    pairs: set[tuple[str, str]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        pairs.add((str(row.get("model", "")), str(row.get("role", ""))))
    return pairs
