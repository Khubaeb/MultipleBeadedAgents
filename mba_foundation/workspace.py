"""`.mba-work/` mode toggle and AI-resource-record privacy guarantee.

Acceptance row coverage (Foundation AC #9, AC #10):

* ``.mba-work/`` mode toggle (F4): the implementation surfaces a
  project-setup choice at install time — ``Git-ignored local`` (default)
  vs ``Git-tracked shared`` — and the chosen policy persists across runs;
  the default ``Git-ignored local`` honours the current
  ``.gitignore:8`` ``.mba-work/`` line.
* AI-resource-record privacy (F4): the local AI-resource record is
  always private and always Git-ignored, regardless of the chosen
  ``.mba-work/`` mode; a ``.gitignore`` check enforces this at runtime.

Implementation contract:

* The chosen mode lives in ``<cwd>/.mba-work/.mba-mode``. Because
  ``.mba-work/`` itself is the carrier of the mode, the mode file moves
  with the working area in the ``shared`` mode and stays local in the
  ``local`` mode — exactly the symmetry the Charter §5 / Constraint 20
  describe.
* The AI-resource record lives at
  ``<cwd>/.mba-work/.ai-resources.json``. A ``.gitignore`` line
  (``.mba-work/.ai-resources*``) ignores it in **both** modes. The runtime
  check rejects a workspace whose ``.gitignore`` does not ignore the
  AI-resource record.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .constants import AI_RESOURCE_RECORD, MBA_MODE_LOCAL, VALID_MBA_MODES

MODE_FILE_NAME: str = ".mba-mode"

# The .gitignore rules that must be present in **local** mode. The
# shared-mode transition (see ``transition_to_shared``) removes the
# broad carrier rule while preserving the private AI-resource record
# and the mode-file rules below.
GITIGNORE_LOCAL_MODE_RULES: tuple[str, ...] = (
    # `.mba-work/` carrier rule — ignored fully in local mode.
    ".mba-work/",
)

# Rules that MUST be present in **both** modes. The AI-resource privacy
# rule is the unconditional Constraint 20 / F4 privacy guarantee.
GITIGNORE_PERSISTENT_RULES: tuple[str, ...] = (
    # AI-resource record privacy — never removable in any mode.
    ".mba-work/.ai-resources*",
    # `.mba-mode` file carries the policy; ignored when the carrier is
    # local so the file does not leak into shared revisions by accident.
    # In shared mode this rule is also fine to keep (it would be a
    # no-op for files inside a tracked `.mba-work/`), but the
    # transition removes it for symmetry.
    ".mba-work/.mba-mode",
)

# Back-compat alias: tests in turn-1 consumed ``GITIGNORE_RULES``. The
# new shape is documented above and the helper still surfaces the union
# of local-mode rules and persistent rules as its default.
GITIGNORE_RULES: tuple[str, ...] = GITIGNORE_LOCAL_MODE_RULES + GITIGNORE_PERSISTENT_RULES


@dataclass(frozen=True)
class ModeState:
    """Persisted mode state for the project."""

    mode: str                           # "local" or "shared".
    mode_file: Path                     # `<cwd>/.mba-work/.mba-mode`.

    @property
    def is_local(self) -> bool:
        return self.mode == MBA_MODE_LOCAL

    @property
    def is_shared(self) -> bool:
        return not self.is_local


def _read_mode_file(mode_file: Path) -> str | None:
    if not mode_file.exists():
        return None
    text = mode_file.read_text(encoding="utf-8").strip().lower()
    return text if text in VALID_MBA_MODES else None


def install_mode(
    cwd: Path,
    *,
    mode: str,
    choice_fn=None,
) -> ModeState:
    """Record the project-setup mode choice.

    The persisted mode file lives inside ``.mba-work/`` so it travels
    with the working area in shared mode. The ``.gitignore`` state is
    reconciled with the chosen mode so local-mode projects keep the
    ``.mba-work/`` carrier rule and shared-mode projects remove it
    (preserving the AI-resource privacy rule, per the F4 audit correction).

    If ``choice_fn`` is supplied and ``mode`` is not explicitly given,
    the choice is delegated to it (a one-line prompt).
    """

    if mode not in VALID_MBA_MODES:
        if choice_fn is None:
            raise ValueError(
                f"mode must be one of {sorted(VALID_MBA_MODES)}; got {mode!r}"
            )
        answer = choice_fn(
            "Choose `.mba-work/` mode: 'local' (Git-ignored) or 'shared' "
            "(Git-tracked)? [local] "
        )
        mode = answer.strip().lower() or MBA_MODE_LOCAL
        if mode not in VALID_MBA_MODES:
            raise ValueError(
                f"choice_fn returned {mode!r}; expected one of "
                f"{sorted(VALID_MBA_MODES)}"
            )

    mba_work = cwd / ".mba-work"
    mba_work.mkdir(parents=True, exist_ok=True)
    mode_file = mba_work / MODE_FILE_NAME
    mode_file.write_text(mode + "\n", encoding="utf-8")

    # Reconcile ``.gitignore`` with the chosen mode. ``apply_mode_to_gitignore``
    # is idempotent: calling it twice with the same mode is a no-op. The
    # AI-resource privacy rule survives both directions.
    apply_mode_to_gitignore(cwd, mode=mode)

    return ModeState(mode=mode, mode_file=mode_file)


def load_mode(cwd: Path) -> ModeState:
    """Load the persisted mode. Defaults to ``local`` (the Charter default)."""

    mode_file = cwd / ".mba-work" / MODE_FILE_NAME
    mode = _read_mode_file(mode_file) or MBA_MODE_LOCAL
    return ModeState(mode=mode, mode_file=mode_file)


def ensure_workspace_setup(cwd: Path, *, mode: str | None = None) -> ModeState:
    """Idempotent: read the persisted mode or install it.

    * If the mode file exists, return it.
    * If absent, install ``mode`` (defaulting to ``local``), returning
      the freshly recorded state.
    """

    existing = load_mode(cwd)
    if existing.mode_file.exists():
        return existing
    return install_mode(cwd, mode=mode or MBA_MODE_LOCAL)


# ---------------------------------------------------------------------------
# AI-resource-record privacy
# ---------------------------------------------------------------------------


def assert_ai_resource_ignored(cwd: Path) -> tuple[bool, str]:
    """Runtime check that the AI-resource record is Git-ignored.

    Returns ``(ok, reason)``. ``ok=False`` means privacy is NOT enforced
    and the project is non-conformant with Constraint 20.

    The expected rule is the glob ``.mba-work/.ai-resources*`` (matches
    the record file plus any rotation / lock files). The check accepts
    either that glob form or a literal exact path.
    """

    gitignore = cwd / ".gitignore"
    if not gitignore.exists():
        return False, f".gitignore not present at {gitignore}"
    content = gitignore.read_text(encoding="utf-8")

    target = Path(AI_RESOURCE_RECORD).name  # `.ai-resources.json`
    accepted_needles = (
        f".mba-work/{target.rsplit('.', 1)[0]}*",   # `.mba-work/.ai-resources*`
        f".mba-work/{target}",                       # exact record
    )
    rules_present = any(
        any(line.strip() == needle for needle in accepted_needles)
        for line in content.splitlines()
    )
    if rules_present:
        return True, ""
    return False, (
        f".gitignore at {gitignore} does not contain a "
        f"'.mba-work/.ai-resources*' rule; Constraint 20 / Foundation F4 "
        f"require the AI-resource record to be ignored in BOTH "
        f"`.mba-work/` modes. Add the rule and retry."
    )


def install_ai_resource_record(cwd: Path, *, payload: dict | None = None) -> Path:
    """Write the AI-resource record (private, always ignored).

    The ``.gitignore`` privacy rule is installed (idempotently) before
    the record is written, so the privacy invariant is upheld on a
    workspace that has not yet reconciled its ``.gitignore`` (e.g.,
    a fresh ``.mba-work/`` carrier in shared mode).
    """

    payload = payload or {
        "schema": 1,
        "note": "AI-resource record — always private and Git-ignored.",
        "resources": [],
    }
    mba_work = cwd / ".mba-work"
    mba_work.mkdir(parents=True, exist_ok=True)
    record = mba_work / Path(AI_RESOURCE_RECORD).name
    import json as _json  # local import keeps the top of the file lean.

    # Privacy invariant — install the persistent rule no matter the
    # current mode. Idempotent.
    _ensure_persistent_rules(cwd)

    record.write_text(
        _json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return record


def _ensure_persistent_rules(cwd: Path) -> bool:
    """Install the persistent MBA ``.gitignore`` rules (idempotent).

    Used by ``install_ai_resource_record`` and ``install_mode`` to keep
    the AI-resource privacy guarantee intact regardless of the
    operating mode. Returns True if any rule was added.
    """

    lines = _read_gitignore_lines(cwd)
    seen = {line.strip() for line in lines}
    missing = [rule for rule in GITIGNORE_PERSISTENT_RULES if rule not in seen]
    if not missing:
        return False

    new_lines = list(lines)
    if new_lines and new_lines[-1].strip() != "":
        new_lines.append("")
    new_lines.append("# MBA F4: persistent (mode-independent) privacy rules")
    for rule in missing:
        new_lines.append(rule)
    _write_gitignore_lines(cwd, new_lines)
    return True


# ---------------------------------------------------------------------------
# `.gitignore` reconciliation
# ---------------------------------------------------------------------------


def _read_gitignore_lines(cwd: Path) -> list[str]:
    gitignore = cwd / ".gitignore"
    if not gitignore.exists():
        return []
    return gitignore.read_text(encoding="utf-8").splitlines()


def _write_gitignore_lines(cwd: Path, lines: list[str]) -> None:
    gitignore = cwd / ".gitignore"
    gitignore.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def reconcile_gitignore(cwd: Path) -> tuple[bool, str]:
    """Ensure local-mode ``.gitignore`` rules are present (idempotent).

    Returns ``(changed, reason)``. ``changed=True`` ⇢ the file was
    rewritten. Conservative: never removes any rule, never disturbs
    user-authored lines. Adds the local-mode carrier +
    mode-file-and-AI-resource rules when absent. For shared mode, use
    :func:`apply_mode_to_gitignore` so the carrier is correctly removed.
    """

    lines = _read_gitignore_lines(cwd)
    seen = {line.strip() for line in lines}
    target = list(GITIGNORE_LOCAL_MODE_RULES) + list(GITIGNORE_PERSISTENT_RULES)
    missing = [rule for rule in target if rule not in seen]
    if not missing:
        return False, ""

    new_lines = list(lines)
    if new_lines and new_lines[-1].strip() != "":
        new_lines.append("")
    new_lines.append("# MBA F4: working-area carrier + AI-resource privacy")
    for rule in missing:
        new_lines.append(rule)
    _write_gitignore_lines(cwd, new_lines)
    return True, "added: " + ", ".join(missing)


def transition_to_shared(cwd: Path) -> tuple[bool, str]:
    """Remove the broad ``.mba-work/`` carrier + ``.mba-work/.mba-mode``.

    Idempotent. Always preserves ``.mba-work/.ai-resources*`` (the
    AI-resource privacy rule). Returns ``(changed, reason)``.
    """

    rules_to_drop = {".mba-work/", ".mba-work/.mba-mode"}
    lines = _read_gitignore_lines(cwd)
    removed: list[str] = []
    new_lines: list[str] = []
    for line in lines:
        if line.strip() in rules_to_drop:
            removed.append(line.strip())
            continue
        new_lines.append(line)
    if not removed:
        return False, ""
    _write_gitignore_lines(cwd, new_lines)
    return True, "removed: " + ", ".join(removed)


def transition_to_local(cwd: Path) -> tuple[bool, str]:
    """Inverse of :func:`transition_to_shared` (idempotent).

    Re-installs ``.mba-work/`` carrier and ``.mba-work/.mba-mode``.
    Preserves ``.mba-work/.ai-resources*`` (or adds it on a fresh
    workspace).
    """

    lines = _read_gitignore_lines(cwd)
    seen = {line.strip() for line in lines}
    target = list(GITIGNORE_LOCAL_MODE_RULES) + [".mba-work/.mba-mode"]
    missing = [rule for rule in target if rule not in seen]
    if not missing:
        return False, ""

    new_lines = list(lines)
    if new_lines and new_lines[-1].strip() != "":
        new_lines.append("")
    new_lines.append("# MBA F4: working-area carrier + AI-resource privacy")
    for rule in missing:
        new_lines.append(rule)
    # Persistent AI-resource rule should also be present.
    if ".mba-work/.ai-resources*" not in seen:
        new_lines.append(".mba-work/.ai-resources*")
    _write_gitignore_lines(cwd, new_lines)
    return True, "added: " + ", ".join(missing)


def apply_mode_to_gitignore(cwd: Path, *, mode: str) -> tuple[bool, str]:
    """Apply the chosen mode to ``.gitignore`` (idempotent).

    * ``mode == 'local'`` ⇢ ensure ``.mba-work/`` is present.
    * ``mode == 'shared'`` ⇢ remove ``.mba-work/`` carrier +
      ``.mba-work/.mba-mode`` (preserving AI-resource privacy).
    """

    if mode == MBA_MODE_LOCAL:
        return transition_to_local(cwd)
    return transition_to_shared(cwd)


def current_gitignore_state(cwd: Path) -> dict[str, bool]:
    """Return a snapshot of which canonical MBA rules are present.

    Keys are the canonical MBA rule strings; values indicate whether
    each is present in the current ``.gitignore``. Used by tests, the
    CLI audit surface, and the runtime privacy check.
    """

    lines = _read_gitignore_lines(cwd)
    seen = {line.strip() for line in lines}
    return {
        rule: rule in seen
        for rule in (*GITIGNORE_LOCAL_MODE_RULES, *GITIGNORE_PERSISTENT_RULES)
    }
