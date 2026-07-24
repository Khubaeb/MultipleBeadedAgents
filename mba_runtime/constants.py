"""Runtime-wide constants.

Derived from:

* ``docs/mba/charter.md`` §4 (the four canonical AI / session patterns);
* ``docs/mba/charter.md`` §11 (user-authority action catalogue);
* ``docs/beads/capabilities.md`` Core (validated versions, exact
  ``bd`` invocations the Runtime issues).
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Patterns. The four canonical Understanding §4 patterns from the Charter
# and from the Selection `.5` section. ``mba_runtime.pattern_router`` checks
# the SessionPlan shape against these labels and refuses to proceed on
# mismatch.
# ---------------------------------------------------------------------------

PATTERN_A: str = "a"
PATTERN_B: str = "b"
PATTERN_C: str = "c"
PATTERN_D: str = "d"
PATTERN_LABELS: tuple[str, ...] = (PATTERN_A, PATTERN_B, PATTERN_C, PATTERN_D)

# Pattern descriptions — verbatim from the Selection's `.5` acceptance
# criteria and from docs/mba/charter.md §4.
PATTERN_DESCRIPTIONS: dict[str, str] = {
    PATTERN_A: (
        "one AI both (same AI fills Doer and Auditor in fresh sessions "
        "with opposing hats)"
    ),
    PATTERN_B: (
        "distinct AIs (one AI per responsibility)"
    ),
    PATTERN_C: (
        "several sessions for one responsibility on one artefact, "
        "findings combined before the opposing responsibility reviews"
    ),
    PATTERN_D: (
        "separated Doer/Auditor sessions even with one AI (one AI, "
        "fresh sessions, opposing hats — the single-AI variant)"
    ),
}


# ---------------------------------------------------------------------------
# Responsibilities (Charter §3 — exactly three workflow responsibilities;
# Orchestrator / Doer / Auditor).
# ---------------------------------------------------------------------------

RESP_ORCHESTRATOR: str = "Orchestrator"
RESP_DOER: str = "Doer"
RESP_AUDITOR: str = "Auditor"
RESPONSIBILITIES: tuple[str, ...] = (RESP_ORCHESTRATOR, RESP_DOER, RESP_AUDITOR)


# ---------------------------------------------------------------------------
# Default organisational roles (Charter §3 "organisational roles / hats").
# The runtime does not hard-code the hat; it accepts any string the user
# records in the AI-resource record or the prompt. Defaults match the
# Selection table.
# ---------------------------------------------------------------------------

DEFAULT_DOER_HAT: str = "Engineer"
DEFAULT_AUDITOR_HAT: str = "Workflow Auditor"


# ---------------------------------------------------------------------------
# §11 user-authority action catalogue (Charter §11). The runtime gates
# every one of these: refusing without a recorded decision is the
# contract; the implementation lives in
# ``mba_runtime.user_authority``.
# ---------------------------------------------------------------------------

USER_AUTHORITY_ACTIONS: frozenset[str] = frozenset(
    {
        "source_git_commit",
        "source_git_push",
        "bd_dolt_push",
        "bd_dolt_pull",
        "deployment",
        "external_message",
        "credentials_or_spending",
        "destructive_change",
        # Permission to run an external worker whose host sandbox
        # cannot prove repository confinement (G1 fix). Default-deny;
        # requires a recorded human-origin decision in the project's
        # decisions log before dispatch. See
        # ``mba_runtime.external_dispatch``.
        "external_dispatch_unconfinement",
        "reusable_workflow_change",
    }
)


# ---------------------------------------------------------------------------
# Path roots.
# ---------------------------------------------------------------------------

AI_RESOURCE_RECORD: str = ".mba-work/.ai-resources.json"
MBA_WORK_DIR: str = ".mba-work"
ORCHESTRATOR_DIR: str = "orchestrator"
FINAL_DIR: str = "final"

# The allowed write root for an external worker is the **entire
# project tree** rooted at ``<project_root>``; see
# ``mba_runtime.external_dispatch.ExternalProcessSessionRunner`` and
# the assignment contract in ``mba_runtime.lifecycle._fill_prompts``.
# A separate session-folder-only constant was removed when the G1
# correction switched to a pre-launch default-deny gate.


# ---------------------------------------------------------------------------
# Convergence settings (Charter §8 — verified fix OR accepted proof).
# "Agreement, confidence, reputation, elapsed time or a fixed turn count
# is not convergence."
# ---------------------------------------------------------------------------

VERDICT_ACCEPT: str = "ACCEPT"
VERDICT_FIND: str = "FIND"      # Adversarial finding requiring fix.
VERDICT_BLOCKED: str = "BLOCKED"  # Configured limit reached without fix.

VERDICTS: tuple[str, ...] = (VERDICT_ACCEPT, VERDICT_FIND, VERDICT_BLOCKED)


# ---------------------------------------------------------------------------
# Comment discipline (Charter §10 + capabilities.md). Worker comments are
# the normal human record: brief, useful, structured, and not padded with
# static Bead fields. The runtime enforces a details line; the line may link a
# separate file only when that file is useful.
# ---------------------------------------------------------------------------

COMMENT_MAX_WORDS: int = 260
COMMENT_MIN_LINES: int = 4
COMMENT_MAX_LINES: int = 16
COMMENT_TARGET_LINES: tuple[int, ...] = tuple(range(COMMENT_MIN_LINES, COMMENT_MAX_LINES + 1))


# ---------------------------------------------------------------------------
# Validated `bd` version (mirrors ``mba_foundation.constants``).
# The runtime refuses to issue ``bd`` writes when the recorded version
# differs from this set; the refusal is part of the audit-grade contract.
# ---------------------------------------------------------------------------

VALIDATED_BD_VERSIONS: frozenset[str] = frozenset({"1.0.4"})


def mba_work_root(cwd: Path) -> Path:
    """Convenience: ``<cwd>/.mba-work``."""

    return cwd / MBA_WORK_DIR
