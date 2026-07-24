"""Cold-start CLI fallback regression tests for ``example-cold``.

Downstream cold-start test surfaced a real failure mode: the published MBA
package's ``mba-runtime`` console script lives under
``%APPDATA%\\Python\\Python3XX\\Scripts`` on a Windows ``pip install
--user`` install, which is not on ``PATH`` by default. An Orchestrator
that followed the installed instructions hit
``mba-runtime : The term 'mba-runtime' is not recognized ...`` and
never reached first-contact.

The fix is small and clear:

* Every cold-start instruction surface that tells an AI / user to run
  ``mba-runtime first-contact --cwd .`` now recommends the reliable,
  deterministic module form:
  ``python -m mba_runtime first-contact --cwd . --apply-setup``.
* The ``mba-runtime`` console script is documented as an optional
  shortcut only when its install location is on ``PATH``.
* The MBA RULES block embedded in ``AGENTS.md`` / ``CLAUDE.md`` of
  installed projects uses the module form, so a fresh Orchestrator
  session does not depend on PATH discovery.

These tests pin that change so a future edit cannot silently revert
to the bare console-script form, and the smoke test proves the
recommended module form actually invokes ``first-contact`` end-to-end.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


SKILL_SOURCE_PATH = (
    PROJECT_ROOT / "mba_foundation" / "resources" / "skills" / "mba" / "SKILL.md"
)
README_PATH = PROJECT_ROOT / "README.md"
USER_GUIDE_PATH = PROJECT_ROOT / "docs" / "USER_GUIDE.md"
CHARTER_PATH = PROJECT_ROOT / "docs" / "mba" / "charter.md"
STARTUP_SETUP_PATH = PROJECT_ROOT / "docs" / "mba" / "startup-setup.md"
MBA_README_PATH = PROJECT_ROOT / "docs" / "mba" / "README.md"
OPENCODE_AGENT_PATH = (
    PROJECT_ROOT / "mba_foundation" / "resources" / "opencode" / "agents" / "mba.md"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# A "bare console-script" cold-start line is one that runs the
# ``mba-runtime`` (or related ``mba-*``) console script directly with
# the ``first-contact`` subcommand, without the ``python -m`` prefix.
# Acceptable forms must use the ``python -m <module>`` invocation.
_BARE_FIRST_CONTACT_PATTERN = re.compile(
    r"""
    (?:^|\W)                       # boundary: start or non-word
    mba-runtime                    # the console-script name
    \s+first-contact               # the cold-start subcommand
    \b                             # word boundary
    """,
    re.VERBOSE,
)


_MODULE_FIRST_CONTACT_PATTERN = re.compile(
    r"python\s+-m\s+mba_runtime\s+first-contact\b"
)


# A line that proves the docs call out the console-script as
# optional, with the PATH caveat. The wording varies slightly across
# files; the strong invariant is the co-presence of "console script"
# and "PATH" in the same cold-start context.
def _mentions_console_script_optionality(text: str) -> bool:
    lowered = text.lower()
    return "console script" in lowered and "path" in lowered


def _has_bare_first_contact_command_line(text: str) -> bool:
    """True when the doc tells a user to invoke the bare
    ``mba-runtime first-contact ...`` form (the failure mode)."""

    return bool(_BARE_FIRST_CONTACT_PATTERN.search(text))


# ---------------------------------------------------------------------------
# The MBA skill (source + installed mirror) — cold-start step
# ---------------------------------------------------------------------------


def test_skill_source_uses_module_form_for_first_contact() -> None:
    """The shipped skill source must recommend the module form."""

    assert SKILL_SOURCE_PATH.is_file(), (
        f"missing MBA skill source at {SKILL_SOURCE_PATH}"
    )
    text = _read(SKILL_SOURCE_PATH)
    assert _MODULE_FIRST_CONTACT_PATTERN.search(text), (
        "SKILL.md (source) must recommend `python -m mba_runtime "
        "first-contact` for cold-start so an Orchestrator does not "
        "depend on `mba-runtime` being on PATH."
    )
    assert not _has_bare_first_contact_command_line(text), (
        "SKILL.md (source) must NOT recommend the bare `mba-runtime "
        "first-contact` console-script form."
    )


def test_skill_source_documents_console_script_optionality() -> None:
    """The skill must explicitly call out the console-script as a
    PATH-conditional shortcut, not the default."""

    text = _read(SKILL_SOURCE_PATH)
    assert _mentions_console_script_optionality(text), (
        "SKILL.md (source) must explain that the `mba-runtime` console "
        "script is optional and only works when its install location is "
        "on PATH."
    )


def test_installed_skill_mirror_matches_source_byte_for_byte(
    tmp_path: Path,
) -> None:
    """A fresh init target receives an exact copy of the skill source."""

    installed = _init_target_or_skip(tmp_path) / ".agents" / "skills" / "mba" / "SKILL.md"
    source = _read(SKILL_SOURCE_PATH)
    assert _read(installed) == source


def test_installed_skill_mirror_uses_module_form_for_first_contact(
    tmp_path: Path,
) -> None:
    """The installed mirror itself must recommend the module form."""

    text = _read(
        _init_target_or_skip(tmp_path) / ".agents" / "skills" / "mba" / "SKILL.md"
    )
    assert _MODULE_FIRST_CONTACT_PATTERN.search(text)
    assert not _has_bare_first_contact_command_line(text)


# ---------------------------------------------------------------------------
# MBA RULES block (markers.py + generated target files)
# ---------------------------------------------------------------------------


def _init_target_or_skip(tmp_path: Path) -> Path:
    import shutil

    if shutil.which("bd") is None:
        pytest.skip("`bd` binary not on PATH; skipping live init proof")
    proc = subprocess.run(
        [sys.executable, "-m", "mba_foundation", "init", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(PROJECT_ROOT),
    )
    if proc.returncode == 5:
        pytest.skip(f"mba init refused the preflight: {proc.stderr[:200]}")
    assert proc.returncode == 0, proc.stderr
    return tmp_path


def test_generated_agent_files_use_module_form(tmp_path: Path) -> None:
    """Freshly generated agent files must carry the safe cold-start block."""

    target = _init_target_or_skip(tmp_path)
    for name in ("AGENTS.md", "CLAUDE.md"):
        text = _read(target / name)
        assert _MODULE_FIRST_CONTACT_PATTERN.search(text)
        assert not _has_bare_first_contact_command_line(text)


def test_generated_agent_files_rules_blocks_are_in_sync(tmp_path: Path) -> None:
    """Fresh init generates matching MBA RULES blocks in both files."""

    from mba_foundation.markers import MBA_RULES_BEGIN_MARKER, MBA_RULES_END_MARKER

    target = _init_target_or_skip(tmp_path)
    blocks = []
    for name in ("AGENTS.md", "CLAUDE.md"):
        text = _read(target / name)
        begin = text.index(MBA_RULES_BEGIN_MARKER)
        end = text.index(MBA_RULES_END_MARKER, begin) + len(MBA_RULES_END_MARKER)
        blocks.append(text[begin:end])
    assert blocks[0] == blocks[1]


def test_mba_rules_block_uses_module_form_for_first_contact() -> None:
    """The ``MBA_RULES_BLOCK`` shipped to ``AGENTS.md`` / ``CLAUDE.md``
    via ``markers.install_block`` must use the module form."""

    from mba_foundation.markers import MBA_RULES_BLOCK

    assert _MODULE_FIRST_CONTACT_PATTERN.search(MBA_RULES_BLOCK), (
        "MBA_RULES_BLOCK (mba_foundation.markers) must recommend "
        "`python -m mba_runtime first-contact` so newly-installed "
        "projects do not depend on PATH."
    )
    assert not _has_bare_first_contact_command_line(MBA_RULES_BLOCK), (
        "MBA_RULES_BLOCK must NOT recommend the bare `mba-runtime "
        "first-contact` console-script form."
    )

# ---------------------------------------------------------------------------
# Docs — README + USER_GUIDE + charter + startup-setup + mba/README
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relpath",
    [
        "README.md",
        "docs/USER_GUIDE.md",
        "docs/mba/charter.md",
        "docs/mba/startup-setup.md",
        "docs/mba/README.md",
    ],
)
def test_doc_uses_module_form_for_first_contact(relpath: str) -> None:
    """Every user-facing doc that walks through the cold-start must
    recommend the module form."""

    path = PROJECT_ROOT / relpath
    assert path.is_file(), f"missing doc {relpath}"
    text = _read(path)
    assert _MODULE_FIRST_CONTACT_PATTERN.search(text), (
        f"{relpath} must recommend `python -m mba_runtime first-contact` "
        "for cold-start."
    )
    assert not _has_bare_first_contact_command_line(text), (
        f"{relpath} must NOT recommend the bare `mba-runtime first-"
        "contact` console-script form."
    )


def test_startup_setup_mermaid_uses_module_form() -> None:
    """The Mermaid diagram in ``startup-setup.md`` is the canonical
    cold-start picture; a stale diagram reverts the fix visually.

    The deterministic cold-start picture now uses ``--apply-setup`` so
    setup handoff is runtime-assisted instead of hand-written.
    """

    text = _read(STARTUP_SETUP_PATH)
    assert (
        'D --> E["Run python -m mba_runtime first-contact --cwd . --apply-setup"]'
        in text
    ), "startup-setup.md Mermaid diagram must show the deterministic module form."


# ---------------------------------------------------------------------------
# Smoke: the recommended module form actually invokes first-contact.
# ---------------------------------------------------------------------------


def test_python_m_mba_runtime_first_contact_module_form_runs() -> None:
    """End-to-end proof: ``python -m mba_runtime first-contact`` runs
    successfully when invoked from the project root (the form we now
    recommend). This is the form that does not depend on PATH.

    The command is the very line an installed Orchestrator would copy
    out of the skill; if this ever stops working the cold-start fix is
    broken, not just the docs."""

    env_addition = str(PROJECT_ROOT)
    # Use a clean PYTHONPATH so the test mirrors a real installed
    # scenario where the runtime package is on sys.path for the
    # interpreter.
    import os

    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        env_addition + os.pathsep + existing if existing else env_addition
    )

    proc = subprocess.run(
        [sys.executable, "-m", "mba_runtime", "first-contact"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode in (0, 4), (
        f"`python -m mba_runtime first-contact` must exit 0 (ready) or "
        f"4 (missing setup handoff). Got rc={proc.returncode}; "
        f"stderr={proc.stderr!r}"
    )
    # The exit-code contract is the same as the documented one; an
    # Orchestrator relies on rc=0 / rc=4 to choose between the ready
    # path and the setup handoff. An exit code outside this set breaks
    # every doc that references it.


# ---------------------------------------------------------------------------
# OpenCode bootstrap regression for Bead ``example-setup``
# ---------------------------------------------------------------------------


OPENCODE_CONFIG_RELPATH = "mba_foundation/resources/opencode/opencode.json"
OPENCODE_AGENT_RELPATH = "mba_foundation/resources/opencode/agents/mba.md"
OPENCODE_WORKER_AGENT_RELPATH = (
    "mba_foundation/resources/opencode/agents/mba-worker.md"
)


def test_opencode_root_config_pins_default_agent_mba() -> None:
    """Regression guard for Bead ``example-setup``: the shipped
    ``opencode.json`` must wire the cold-start agent to ``mba`` so a
    freshly-installed OpenCode consumer activates as the MBA
    Orchestrator *before* reading project files or starting work.

    A future edit that switches ``default_agent`` away from ``mba``
    regresses the downstream test repo-downstream failure mode (OpenCode first read
    many repo files before calling first-contact) that this Bead
    was created to fix.
    """

    config_path = PROJECT_ROOT / OPENCODE_CONFIG_RELPATH
    assert config_path.is_file(), (
        f"OpenCode bootstrap config missing at {config_path}"
    )
    import json
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config.get("default_agent") == "mba", (
        "the shipped opencode.json must keep `default_agent: mba`; "
        "switching it away from mba silently regresses the cold-start "
        "Orchestrator contract."
    )
    instructions = config.get("instructions") or []
    assert "AGENTS.md" in instructions, (
        "the shipped opencode.json must list AGENTS.md in `instructions` "
        "so the MBA RULES block is loaded into the Orchestrator context."
    )


def test_opencode_agent_mandates_first_contact_gate() -> None:
    """The shipped ``mba`` agent must enforce the deterministic
    cold-start gate (``bd version`` + ``python -m mba_runtime
    first-contact --cwd . --apply-setup``) before any project
    exploration, editing, or worker launch — same module-form cold
    start the skill and MBA RULES block mandate.

    A future edit that drops the gate (or switches to the bare
    ``mba-runtime first-contact`` console-script form) regresses the
    fix and breaks every downstream OpenCode consumer that depends
    on the gate.
    """

    agent_path = PROJECT_ROOT / OPENCODE_AGENT_RELPATH
    assert agent_path.is_file(), (
        f"OpenCode bootstrap agent missing at {agent_path}"
    )
    text = agent_path.read_text(encoding="utf-8")
    # Required gate steps.
    assert "bd version" in text, (
        "agent must require `bd version` as the first cold-start step"
    )
    assert "python -m mba_runtime first-contact --cwd . --apply-setup" in text, (
        "agent must use the module form `python -m mba_runtime "
        "first-contact --cwd . --apply-setup` (not the PATH-dependent "
        "`mba-runtime first-contact` console-script form)"
    )
    # Explicit refusal text that the gate must surface to the
    # Orchestrator on a non-zero first-contact exit.
    assert "blocked" in text.lower()
    assert "MBA setup" in text
    # The agent must clearly name what the Orchestrator must NOT do
    # before the gate clears (read / research / edit / launch).
    lowered = text.lower()
    for forbidden in (
        "do not perform",
        "do not launch",
    ):
        assert forbidden in lowered, (
            f"agent must list {forbidden!r} as a forbidden pre-gate action"
        )


def test_opencode_agent_yaml_front_matter_marks_primary_agent() -> None:
    """The shipped agent file must use ``---`` YAML front-matter
    consistent with the OpenCode agent format and ``mode: primary``
    so the consumer activates as the MBA Orchestrator instead of a
    sub-agent."""

    agent_path = PROJECT_ROOT / OPENCODE_AGENT_RELPATH
    text = agent_path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), (
        "agent must begin with YAML front-matter (`---\\n`)"
    )
    # Body opens with the description + mode block.
    assert "description:" in text
    assert "mode: primary" in text
    # The agent role is declared explicitly so the file is not just a
    # prose document.
    assert "MBA Orchestrator" in text


def test_opencode_worker_agent_refuses_orchestrator_mode() -> None:
    """The shipped ``mba-worker`` agent must keep Doer/Auditor
    sessions from re-entering the MBA Orchestrator cold-start path."""

    agent_path = PROJECT_ROOT / OPENCODE_WORKER_AGENT_RELPATH
    assert agent_path.is_file(), (
        f"OpenCode worker agent missing at {agent_path}"
    )
    text = agent_path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "mode: primary" in text
    assert "You are not the Orchestrator" in text
    assert "Do not run `python -m mba_runtime first-contact" in text
    assert "Doer" in text and "Auditor" in text


def test_resource_config_separates_id_from_launch_model() -> None:
    """Cold-start guidance must not let resource ids masquerade as models."""

    for path in (
        README_PATH,
        USER_GUIDE_PATH,
        CHARTER_PATH,
        STARTUP_SETUP_PATH,
        SKILL_SOURCE_PATH,
        OPENCODE_AGENT_PATH,
    ):
        text = path.read_text(encoding="utf-8")
        assert "launch.model" in text, (
            f"{path} must mention the executable launch model field"
        )
    agent_text = OPENCODE_AGENT_PATH.read_text(encoding="utf-8")
    assert "--model\", \"<launch.model>\"" in agent_text
    assert "Never pass <resource-id> to --model" in agent_text


def test_orchestrator_surfaces_enforce_thin_prompts_and_verdict_files() -> None:
    """Installed guidance must prevent the Downstream retry failure modes."""

    from mba_foundation.markers import MBA_RULES_BLOCK

    file_surfaces = (
        README_PATH,
        USER_GUIDE_PATH,
        CHARTER_PATH,
        SKILL_SOURCE_PATH,
        OPENCODE_AGENT_PATH,
    )
    surfaces = [(str(path), path.read_text(encoding="utf-8")) for path in file_surfaces]
    surfaces.append(("markers.MBA_RULES_BLOCK", MBA_RULES_BLOCK))

    for name, text in surfaces:
        lowered = text.lower()
        assert "orchestrator stays thin" in lowered or "thin orchestrator" in lowered, (
            f"{name} must tell the Orchestrator to stay thin"
        )
        assert "pointer-based" in lowered, (
            f"{name} must require pointer-based worker prompts"
        )
        assert "_verdict.txt" in text, (
            f"{name} must mention the mandatory Auditor verdict file"
        )
        assert "missing" in lowered and (
            "not `accept`" in lowered
            or "not ``accept``" in lowered
            or "do not infer `accept`" in lowered
            or "must not infer acceptance" in lowered
            or "must not infer `accept`" in lowered
        ), (
            f"{name} must fail closed when verdict evidence is missing"
        )


def test_windows_surfaces_reject_bash_shortcuts_for_worker_flow() -> None:
    """Windows instructions must stay PowerShell-native."""

    for path in (
        USER_GUIDE_PATH,
        CHARTER_PATH,
        SKILL_SOURCE_PATH,
        OPENCODE_AGENT_PATH,
    ):
        text = path.read_text(encoding="utf-8")
        assert "PowerShell" in text, f"{path} must name PowerShell"
        assert "||" in text, f"{path} must explicitly reject Bash-style ||"
        assert "heredocs" in text, f"{path} must explicitly reject heredocs"


def test_self_run_guidance_rejects_atlas_gap_patterns() -> None:
    """Installed and public surfaces pin the four Downstream self-run fixes."""

    from mba_foundation.markers import MBA_RULES_BLOCK

    orchestrator_surfaces = (
        README_PATH.read_text(encoding="utf-8"),
        USER_GUIDE_PATH.read_text(encoding="utf-8"),
        CHARTER_PATH.read_text(encoding="utf-8"),
        SKILL_SOURCE_PATH.read_text(encoding="utf-8"),
        OPENCODE_AGENT_PATH.read_text(encoding="utf-8"),
        MBA_RULES_BLOCK,
    )
    for text in orchestrator_surfaces:
        lowered = text.lower()
        assert "beads is the only workflow task tracker" in lowered
        assert "todowrite" in lowered
        assert "pointer-based" in lowered
        assert "pre-research" in lowered or "pre-researched" in lowered or "research diffs" in lowered

    auditor_surfaces = (
        README_PATH.read_text(encoding="utf-8"),
        USER_GUIDE_PATH.read_text(encoding="utf-8"),
        CHARTER_PATH.read_text(encoding="utf-8"),
        SKILL_SOURCE_PATH.read_text(encoding="utf-8"),
        OPENCODE_AGENT_PATH.read_text(encoding="utf-8"),
        MBA_RULES_BLOCK,
    )
    for text in auditor_surfaces:
        lowered = text.lower()
        assert "known wrong fact" in lowered
        assert "user-facing deliverable" in lowered
        assert "non-load-bearing" in lowered
        assert "find" in lowered and (
            "requires" in lowered or "require" in lowered
        )


def test_worker_agent_requires_compact_comments_and_strict_fact_verdicts() -> None:
    text = (PROJECT_ROOT / OPENCODE_WORKER_AGENT_RELPATH).read_text(encoding="utf-8")
    lowered = text.lower()
    assert "todo/task-list" in lowered
    assert "4-16 non-blank" in lowered
    assert "bulky evidence" in lowered
    assert "known wrong fact" in lowered
    assert "non-load-bearing" in lowered


def test_user_authority_gate_blocks_for_human_after_convergence() -> None:
    """Accepted work waiting on user approval must not stay in progress."""

    from mba_foundation.markers import MBA_RULES_BLOCK

    file_surfaces = (
        README_PATH,
        USER_GUIDE_PATH,
        CHARTER_PATH,
        SKILL_SOURCE_PATH,
        OPENCODE_AGENT_PATH,
        PROJECT_ROOT / "docs" / "mba" / "technical-flow.md",
    )
    surfaces = [(str(path), path.read_text(encoding="utf-8")) for path in file_surfaces]
    surfaces.append(("markers.MBA_RULES_BLOCK", MBA_RULES_BLOCK))

    for name, text in surfaces:
        lowered = text.lower()
        assert "user-authority gate" in lowered, (
            f"{name} must name the user-authority gate"
        )
        assert "blocked" in lowered and "human" in lowered, (
            f"{name} must route the gate to blocked/Human"
        )
        assert "in_progress" in lowered, (
            f"{name} must reject leaving the Bead silently in_progress"
        )
        assert "ready-for-user" in lowered, (
            f"{name} must say ready-for-user labels are not enough"
        )


# ---------------------------------------------------------------------------
# Cold-start regression round — downstream test repo `prompt-budget-regression` retry fix
# ---------------------------------------------------------------------------
# downstream test repo rerun installed MBA commit ``prompt-budget-regression`` (with the round-1 prose
# rule "prompts must be pointer-based, no precomputed research, no long
# diffs") but the installed Orchestrator still wrote a 13 KiB Doer prompt
# with precomputed conclusions. Prose alone was not enough; round 2
# tried a full regex validator and was rejected as a new framework. This
# block pins the smallest correct fix: a hard pre-launch self-check rule
# in prose, plus a regression test that an downstream-shaped prompt would
# fail it.

_ATLAS_BAD_PROMPT_BUDGET_BYTES: int = 4 * 1024
_ATLAS_BAD_PROMPT_LINE_BUDGET: int = 60
_ATLAS_BAD_PROMPT_FORBIDDEN_DIFF_RE = re.compile(
    r"^(?:diff --git\b|--- a/|\+\+\+ b/|@@\s)", re.MULTILINE,
)
_ATLAS_BAD_PROMPT_FORBIDDEN_PRECOMPUTED_RE = re.compile(
    r"\b(ahead_by|behind_by|total_commits)\s*:\s*\d+\b",
)


def _synthesize_atlas_bad_prompt() -> str:
    """Recreate the downstream test repo ``prompt-budget-regression`` bad prompt shape inline.

    A 10 KiB+ body with numbered "material change" subsections, precomputed
    ``ahead_by: 1`` / ``behind_by: 0`` / ``total_commits: 1`` values for
    the worker, and a worker-discoverable diff section — the prompt still
    satisfies the §10 template so it would pass an installer that only
    checked the canonical fields.
    """

    header = "\n".join([
        "# Researcher Assignment\n",
        "- **Bead:** aca-zif", "- **Stage:** Implementation",
        "- **Responsibility:** Doer", "- **Organizational role:** Researcher",
        "- **Session purpose:** Refresh research file at prompt-budget-regression",
        "- **Task:** Update the research file for the new commit.",
        "- **Read:** `bd show aca-zif --json`; the research file.",
        "- **Produce:** updated research file, report.md, comment.md",
        "- **Acceptance:** the file cites the new commit.",
        "- **Authority and limits:** edit the project; do not commit.",
        "", "## Orchestrator-supplied source conclusions", "",
        "The compare API returns `ahead_by: 1`, `behind_by: 0`, and",
        "`total_commits: 1`. The new commit covers four material changes:",
    ])
    conclusions = "\n".join(
        f"{i}. Precomputed source conclusion {i}: the Orchestrator "
        "already inspected the external commit and is telling the worker "
        "which files, symbols, facts, line counts, and exact edits to report."
        for i in range(1, 41)
    )
    diff = "\n".join([
        "", "## Worker-discoverable diff (do not re-fetch)", "",
        "diff --git a/research/process-packs/multiple-beaded-agents.md "
        "b/research/process-packs/multiple-beaded-agents.md",
        "--- a/research/process-packs/multiple-beaded-agents.md",
        "+++ b/research/process-packs/multiple-beaded-agents.md",
        "@@ -1 +1 @@", "-old", "+new", "",
    ])
    return f"{header}\n{conclusions}\n{diff}\n"


def test_self_run_prompt_self_check_is_mandatory_on_every_surface() -> None:
    """Every Orchestrator-facing surface must carry the pre-launch self-check."""

    from mba_foundation.markers import MBA_RULES_BLOCK

    file_surfaces = (
        README_PATH, USER_GUIDE_PATH, CHARTER_PATH,
        PROJECT_ROOT / "docs" / "mba" / "technical-flow.md",
        SKILL_SOURCE_PATH, OPENCODE_AGENT_PATH,
    )
    surfaces = [(str(p), p.read_text(encoding="utf-8")) for p in file_surfaces]
    surfaces.append(("markers.MBA_RULES_BLOCK", MBA_RULES_BLOCK))

    for name, text in surfaces:
        lowered = text.lower()
        assert "prompt self-check" in lowered, (
            f"{name} must carry the pre-launch prompt self-check rule"
        )
        assert "4 kib" in lowered or "≤ 4" in text or "<= 4" in lowered, (
            f"{name} must state the 4 KiB compact-prompt budget"
        )
        assert "60" in text and "non-blank" in lowered, (
            f"{name} must state the 60 non-blank-line budget"
        )
        assert "ahead_by" in lowered and "total_commits" in lowered, (
            f"{name} must name the precomputed compare-result forbidden values"
        )
        assert "diff --git" in lowered or "unified-diff" in lowered, (
            f"{name} must name the unified-diff forbidden payload"
        )
        assert "blocked" in lowered and "human" in lowered, (
            f"{name} must route an unfixable prompt to blocked/Human"
        )


def test_self_run_atlas_bulky_presolved_prompt_shape_fails_self_check() -> None:
    """Regression: the 13 KiB downstream test repo prompt shape must fail every budget."""

    prompt = _synthesize_atlas_bad_prompt()
    byte_count = len(prompt.encode("utf-8"))
    nonblank_lines = sum(bool(line.strip()) for line in prompt.splitlines())

    assert byte_count > _ATLAS_BAD_PROMPT_BUDGET_BYTES, (
        f"downstream-shape fixture must exceed 4 KiB; got {byte_count} bytes"
    )
    assert nonblank_lines > _ATLAS_BAD_PROMPT_LINE_BUDGET, (
        f"downstream-shape fixture must exceed 60 non-blank lines; got {nonblank_lines}"
    )
    assert _ATLAS_BAD_PROMPT_FORBIDDEN_DIFF_RE.search(prompt) is not None, (
        "downstream-shape fixture must contain a unified-diff payload marker"
    )
    assert _ATLAS_BAD_PROMPT_FORBIDDEN_PRECOMPUTED_RE.search(prompt) is not None, (
        "downstream-shape fixture must contain a precomputed compare-result value"
    )


def test_user_guide_documents_workspace_folder_hygiene() -> None:
    """``docs/USER_GUIDE.md`` must explain what MBA writes where."""

    lowered = USER_GUIDE_PATH.read_text(encoding="utf-8").lower()
    assert "workspace folder hygiene" in lowered
    assert ".mba-work" in lowered
    assert "sibling" in lowered
    assert "test harness" in lowered or "test-harness" in lowered
    assert "-public" in lowered or "publicsync" in lowered


# ---------------------------------------------------------------------------
# Cold-start regression round — downstream test repo self-run retry fix
# ---------------------------------------------------------------------------
# downstream test repo public ``cf184e9`` rerun passed real worker sessions and a
# compact-pointer prompt, but the installed Orchestrator still let
# four concrete gaps slip through: a worker folder named after a
# friendly install / ref hash instead of the actual Bead ID
# (``atlas-public-cf184e9/...`` instead of ``aca-zhc/...``), a Doer
# comment of 26 lines and an Orchestrator comment of 58 lines (both
# over the 4-16 non-blank budget), an Auditor that did not `FIND`
# overlong comments, and docs that imply ``run.log`` / ``run.err``
# are live streams even though OpenCode may flush late.


_ATLAS_FRIENDLY_NAME_BEAD_PATH = ".mba-work/atlas-public-cf184e9/doer"


def test_self_run_worker_folder_must_be_bead_scoped_on_every_surface() -> None:
    """Every surface must require the worker folder to be
    ``.mba-work/<bead-id>/<session-name>/`` where ``<bead-id>`` is
    the actual Bead ID; friendly install name / test harness / ref
    hash is forbidden.
    """

    from mba_foundation.markers import MBA_RULES_BLOCK

    file_surfaces = (
        README_PATH,
        USER_GUIDE_PATH,
        CHARTER_PATH,
        PROJECT_ROOT / "docs" / "mba" / "technical-flow.md",
        SKILL_SOURCE_PATH,
        OPENCODE_AGENT_PATH,
        PROJECT_ROOT
        / "mba_foundation"
        / "resources"
        / "opencode"
        / "agents"
        / "mba-worker.md",
    )
    surfaces = [(str(p), p.read_text(encoding="utf-8")) for p in file_surfaces]
    surfaces.append(("markers.MBA_RULES_BLOCK", MBA_RULES_BLOCK))

    for name, text in surfaces:
        lowered = text.lower()
        assert ".mba-work/<bead-id>/<session-name>/" in text, (
            f"{name} must state the canonical worker folder pattern"
        )
        # The downstream test repo rerun failure used a friendly install name
        # (``atlas-public-cf184e9``) in place of the Bead ID; the rule
        # must explicitly reject that shape.
        assert "friendly install name" in lowered or (
            "actual bead id" in lowered and "ref hash" in lowered
        ), (
            f"{name} must forbid using a friendly install name / ref "
            f"hash in place of the Bead ID"
        )
        assert "test harness" in lowered or "test-harness" in lowered, (
            f"{name} must forbid using a test-harness name in place of "
            f"the Bead ID"
        )


def test_self_run_atlas_friendly_name_path_is_explicitly_rejected() -> None:
    """downstream test repo ``cf184e9`` used a friendly install name in place of the
    Bead ID; the authoritative source surfaces (charter, MBA RULES
    block, USER_GUIDE, and both OpenCode agents) must explicitly bind
    that shape to a refusal-grade error. The general rule is pinned
    on every surface by the bead-scoped test above.
    """

    from mba_foundation.markers import MBA_RULES_BLOCK

    authoritative_surfaces = (
        USER_GUIDE_PATH,
        CHARTER_PATH,
        OPENCODE_AGENT_PATH,
        PROJECT_ROOT
        / "mba_foundation"
        / "resources"
        / "opencode"
        / "agents"
        / "mba-worker.md",
    )
    authoritative = [(str(p), p.read_text(encoding="utf-8")) for p in authoritative_surfaces]
    authoritative.append(("markers.MBA_RULES_BLOCK", MBA_RULES_BLOCK))

    atlas_path = _ATLAS_FRIENDLY_NAME_BEAD_PATH
    for name, text in authoritative:
        # The friendly-name folder example (or its surrounding
        # paragraph) must explicitly bind to a rejection of the
        # non-bead-scoped path shape. We accept the literal
        # ``atlas-public-cf184e9`` example OR an unambiguous
        # ``.mba-work/<friendly-name>/...`` shape that says the
        # ``<friendly-name>`` is not the Bead ID.
        assert (
            "atlas-public-cf184e9" in text.lower()
            or "`.mba-work/<friendly-name>/...`" in text
            or ".mba-work/<friendly-name>/..." in text
        ), (
            f"{name} must name the downstream test repo-friendly-name failure shape "
            f"({atlas_path}) or an equivalent "
            f"`.mba-work/<friendly-name>/...` rejection paragraph"
        )
        assert "refusal-grade" in text.lower(), (
            f"{name} must call a non-bead-scoped worker path a "
            f"refusal-grade error"
        )


# Authoritative surfaces that must explicitly forbid overlong /
# report-like AI comments and require the Auditor to return FIND on
# them. Technical-flow.md is intentionally excluded: it is the
# module-level pipeline view, not the user-facing contract.
_COMMENT_RULE_SURFACES = (
    README_PATH,
    USER_GUIDE_PATH,
    CHARTER_PATH,
    SKILL_SOURCE_PATH,
    OPENCODE_AGENT_PATH,
    PROJECT_ROOT
    / "mba_foundation"
    / "resources"
    / "opencode"
    / "agents"
    / "mba-worker.md",
)


_ATLAS_OVERLONG_COMMENT_LINE_BUDGET: int = 16


def _synthesize_atlas_overlong_comment() -> str:
    """Recreate the downstream test repo ``cf184e9`` bad Doer comment shape inline.

    A 26-line comment that pastes the prompt, repeats static Bead
    fields, and lists every diagnostic from ``report.md`` — the
    shape the Orchestrator cannot accept under the 4-16 non-blank
    budget. The fixture makes the regression deterministic without
    depending on a real Bead.
    """

    header = "\n".join(
        [
            "## Doer result",
            "",
            "- Implemented the patch across all eight required "
            "surfaces (markers, SKILL, charter, technical-flow, "
            "USER_GUIDE, README, mba.md, mba-worker.md).",
            "- Verified by running the focused 27 tests for "
            "`test_cold_start_guidance.py` and the full suite.",
        ]
    )
    padding = "\n".join(
        f"- Diagnostic detail {i}: enumerated the exact round "
        f"outputs, budget markers, missing-pointer checks, "
        f"and test outcomes for the {i}-th bullet."
        for i in range(1, 23)
    )
    trailer = "\n".join(
        [
            "- Did **not** add a new framework, parser, daemon, "
            "or large validator.",
            "- Did **not** commit, push, or modify downstream test repo; "
            "details in report.md; worker-internal delegation: "
            "none.",
        ]
    )
    return f"{header}\n{padding}\n{trailer}\n"


def test_self_run_ai_comment_budget_is_hard_contract_on_every_surface() -> None:
    """Every Orchestrator-facing surface must state the 4-16
    non-blank-line budget as a hard contract on AI Bead comments and
    forbid overlong / report-like / prompt-pasting comments.
    """

    from mba_foundation.markers import MBA_RULES_BLOCK

    surfaces = [(str(p), p.read_text(encoding="utf-8")) for p in _COMMENT_RULE_SURFACES]
    surfaces.append(("markers.MBA_RULES_BLOCK", MBA_RULES_BLOCK))

    for name, text in surfaces:
        lowered = text.lower()
        # Budget wording (any consistent case-insensitive match).
        assert "4-16 non-blank" in lowered, (
            f"{name} must state the 4-16 non-blank-line budget"
        )
        # Hard contract wording: must use stronger language than
        # ``usually`` / ``typically`` for the budget on every surface.
        assert "normally 4-16" in lowered, (
            f"{name} must frame the 4-16 budget as a normal / hard "
            f"contract instead of just a soft preference"
        )
        # Overlong / report-like / prompt-pasting comments are
        # forbidden, not just discouraged.
        assert (
            "overlong" in lowered and "report-like" in lowered
        ), f"{name} must forbid overlong and report-like comments"
        assert (
            "pastes the prompt" in lowered
            or "pasting the prompt" in lowered
            or "paste the prompt" in lowered
            or "paste their transcript" in lowered
            or "pastes their transcript" in lowered
        ), (
            f"{name} must forbid AI comments from pasting the prompt "
            f"or transcript"
        )


def test_self_run_atlas_overlong_comment_shape_fails_comment_budget() -> None:
    """The downstream test repo ``cf184e9`` Doer comment (26 lines, pastes the
    prompt, repeats static Bead fields) is the exact shape every
    installed surface now forbids.
    """

    prompt = _synthesize_atlas_overlong_comment()
    nonblank_lines = sum(bool(line.strip()) for line in prompt.splitlines())

    assert nonblank_lines > _ATLAS_OVERLONG_COMMENT_LINE_BUDGET, (
        f"downstream-shape fixture must exceed 16 non-blank lines; got "
        f"{nonblank_lines}"
    )
    # Marker strings the rule explicitly forbids on every surface.
    for forbidden in (
        "implemented the patch across all eight",
        "focused 27 tests",
        "details in report.md",
        "worker-internal delegation",
        "did **not** add a new framework",
        "did **not** commit, push",
    ):
        assert forbidden in prompt.lower(), (
            f"downstream-shape fixture must include forbidden marker "
            f"{forbidden!r}"
        )


def test_self_run_auditor_must_find_overlong_worker_comments() -> None:
    """The Auditor must return ``FIND`` while a worker comment
    exceeds the 4-16 non-blank budget, pastes the prompt, or
    repeats static Bead fields.
    """

    from mba_foundation.markers import MBA_RULES_BLOCK

    surfaces = [(str(p), p.read_text(encoding="utf-8")) for p in _COMMENT_RULE_SURFACES]
    surfaces.append(("markers.MBA_RULES_BLOCK", MBA_RULES_BLOCK))

    for name, text in surfaces:
        lowered = text.lower()
        # Auditor must return FIND on overlong / report-like worker
        # comments. We accept either ``FIND`` (uppercased) or ``find``
        # (lowercased) so the surface test isn't letter-case-coupled.
        assert "auditor must return ``find``" in lowered or (
            "auditor must" in lowered and "find" in lowered and (
                "overlong" in lowered or "report-like" in lowered
            )
        ), (
            f"{name} must require the Auditor to return FIND on "
            f"overlong / report-like worker comments"
        )
        # Wording the rule explicitly rejects as not enough proof.
        for weak_word in ("descriptive", "minor", "useful context"):
            assert weak_word in lowered, (
                f"{name} must name the weak-evidence exception "
                f"{weak_word!r} that the Auditor must refuse"
            )


def test_self_run_run_logs_are_captured_files_with_late_flush_caveat() -> None:
    """``run.log`` / ``run.err`` are captured files written by
    ``-RedirectStandardOutput`` / ``-RedirectStandardError``. OpenCode
    may flush stdout late, so the log files are **not** a guaranteed
    live stream.
    """

    from mba_foundation.markers import MBA_RULES_BLOCK

    file_surfaces = (
        README_PATH,
        USER_GUIDE_PATH,
        CHARTER_PATH,
        PROJECT_ROOT / "docs" / "mba" / "technical-flow.md",
        SKILL_SOURCE_PATH,
        OPENCODE_AGENT_PATH,
        PROJECT_ROOT
        / "mba_foundation"
        / "resources"
        / "opencode"
        / "agents"
        / "mba-worker.md",
    )
    surfaces = [(str(p), p.read_text(encoding="utf-8")) for p in file_surfaces]
    surfaces.append(("markers.MBA_RULES_BLOCK", MBA_RULES_BLOCK))

    for name, text in surfaces:
        lowered = text.lower()
        # Captured-file naming. At least `run.log` / `run.err` appear
        # as a captured file path on every surface.
        assert "run.log" in lowered and "run.err" in lowered, (
            f"{name} must name the captured run.log / run.err files"
        )
        # Late-flush caveat: OpenCode may flush stdout late; the log
        # file is captured, not a live stream.
        assert (
            "opencode may flush" in lowered
            or "opencode output may flush late" in lowered
        ), (
            f"{name} must say OpenCode may flush late"
        )
        assert "captured log file" in lowered or (
            "captured" in lowered and "log file" in lowered
        ), (
            f"{name} must call ``run.log`` a captured log file "
            f"rather than a live stream"
        )


# ---------------------------------------------------------------------------
# Cold-start regression round — downstream test repo path-guard retry fix
# ---------------------------------------------------------------------------
# downstream test repo public ``path-guard-regression`` rerun installed a self-run hardening patch and
# still exposed three concrete Orchestrator gaps: (a) the first generated
# launch command threw ``Missing parent .mba-work\<bead>`` because the
# Orchestrator invented a defensive ``Split-Path -Parent`` / ``Test-Path``
# pre-check before ``New-Item -Force``; (b) the worker was interrupted
# before producing ``report.md`` / ``comment.md`` and the resumed
# Orchestrator noted an open Bead with a launch receipt but no role
# comment; (c) the Orchestrator recovered context with a broad
# ``bd list --all`` instead of targeted reads. This block pins each gap
# shape on every Orchestrator-facing surface.


_ATLAS_B0B0C5C_LAUNCH_SURFACES = (
    README_PATH,
    USER_GUIDE_PATH,
    CHARTER_PATH,
    PROJECT_ROOT / "docs" / "mba" / "technical-flow.md",
    SKILL_SOURCE_PATH,
    OPENCODE_AGENT_PATH,
)


def test_self_run_canonical_launch_directory_uses_new_item_force_without_parent_precheck() -> None:
    """The canonical launch pattern uses ``New-Item -Force`` directly on
    the session path; the parent pre-check shape is explicitly
    forbidden.

    The downstream test repo ``path-guard-regression`` Orchestrator wrote:

        $session = ".mba-work\\aca-bqz\\doer"
        $parent = Split-Path -Parent $session
        if (-not (Test-Path -LiteralPath $parent)) { throw "Missing parent $parent" }
        New-Item -ItemType Directory -Force -Path $session | Out-Null

    The pre-check threw ``Missing parent .mba-work\aca-bqz`` when the
    Bead-scoped parent did not yet exist; a subsequent retry with the
    parent already present succeeded. The fix is to use
    ``New-Item -ItemType Directory -Force -Path $session | Out-Null``
    directly — ``New-Item -Force`` already creates missing parent
    directories, so the pre-check only ever short-circuits a clean
    launch. Every Orchestrator-facing surface must pin the canonical
    pattern and the literal downstream test repo failure shape.
    """

    from mba_foundation.markers import MBA_RULES_BLOCK

    surfaces = [(str(p), p.read_text(encoding="utf-8")) for p in _ATLAS_B0B0C5C_LAUNCH_SURFACES]
    surfaces.append(("markers.MBA_RULES_BLOCK", MBA_RULES_BLOCK))

    for name, text in surfaces:
        lowered = text.lower()
        assert "new-item -itemtype directory -force" in lowered or (
            "new-item" in lowered and "-force" in lowered and "directory" in lowered
        ), (
            f"{name} must show the canonical New-Item -Force launch "
            f"directory pattern"
        )
        # The downstream test repo failure shape must be named explicitly so the rule
        # is bound to a refusal-grade error.
        assert "missing parent" in lowered, (
            f"{name} must name the downstream test repo ``Missing parent .mba-work\\<bead>`` "
            f"failure shape so the rule is bound to a real failure"
        )
        assert "split-path -parent" in lowered or "split-path" in lowered, (
            f"{name} must name the forbidden Split-Path -Parent pre-check shape"
        )
        assert "test-path" in lowered, (
            f"{name} must name the forbidden Test-Path pre-check shape"
        )


def test_self_run_orchestrator_resume_after_restart_inspects_receipt_and_artefacts() -> None:
    """When the active Orchestrator resumes after a restart,
    disconnection, or other loss of in-progress state, it must not
    assume an open Bead is still being worked. The first action is to
    read the Bead's launch receipt and the worker artefacts to
    determine the round's actual state, then continue the same bounded
    wait → resume → relaunch → blocked/Human contract.
    """

    from mba_foundation.markers import MBA_RULES_BLOCK

    surfaces = [(str(p), p.read_text(encoding="utf-8")) for p in _ATLAS_B0B0C5C_LAUNCH_SURFACES]
    surfaces.append(("markers.MBA_RULES_BLOCK", MBA_RULES_BLOCK))

    for name, text in surfaces:
        lowered = text.lower()
        # Resume / restart trigger phrases.
        assert (
            "orchestrator resume" in lowered
            or "after restart" in lowered
            or "after a restart" in lowered
        ), (
            f"{name} must name the Orchestrator-resume-after-restart rule"
        )
        # First action: read the launch receipt + worker artefacts.
        assert "launch.md" in lowered, (
            f"{name} must say the Orchestrator reads "
            f".mba-work/<bead-id>/<session-name>/launch.md first"
        )
        assert (
            "report.md" in lowered
            and "comment.md" in lowered
        ), (
            f"{name} must say the Orchestrator reads the worker "
            f"report.md / comment.md artefacts"
        )
        # The same bounded policy still applies — never leave the Bead
        # silently in_progress.
        assert "in_progress" in lowered, (
            f"{name} must say the Orchestrator must not leave the Bead "
            f"silently in_progress after resume"
        )
        assert "wait" in lowered and "resume" in lowered and "relaunch" in lowered, (
            f"{name} must keep the bounded wait → resume → relaunch "
            f"contract on the resume path"
        )
        assert "blocked" in lowered and "human" in lowered, (
            f"{name} must route an unrecoverable Bead to blocked/Human"
        )


def test_self_run_targeted_beads_reads_only_no_broad_list_all() -> None:
    """Routine context recovery uses targeted Beads reads — not a
    broad ``bd list --all``.

    The downstream test repo ``path-guard-regression`` Orchestrator recovered context with a broad
    ``bd list --all`` instead of a targeted read on the active Bead.
    The rule pins the targeted reads on every Orchestrator-facing
    surface and explicitly demotes ``bd list --all`` to a non-routine
    move.
    """

    from mba_foundation.markers import MBA_RULES_BLOCK

    surfaces = [(str(p), p.read_text(encoding="utf-8")) for p in _ATLAS_B0B0C5C_LAUNCH_SURFACES]
    surfaces.append(("markers.MBA_RULES_BLOCK", MBA_RULES_BLOCK))

    for name, text in surfaces:
        lowered = text.lower()
        # Targeted reads.
        assert "bd show" in lowered, (
            f"{name} must name ``bd show`` as a routine context read"
        )
        assert "bd ready" in lowered, (
            f"{name} must name ``bd ready`` as a routine context read"
        )
        assert "bd list --status=" in lowered, (
            f"{name} must name a filtered ``bd list --status=...`` "
            f"as a routine context read"
        )
        # ``bd list --all`` must be named as the broad read being demoted.
        assert "bd list --all" in lowered, (
            f"{name} must name ``bd list --all`` as the broad read being "
            f"demoted from routine context recovery"
        )
        # The wording must mark ``bd list --all`` as not routine.
        assert (
            "not a routine" in lowered
            or "not routine" in lowered
            or "broad" in lowered
        ), (
            f"{name} must mark ``bd list --all`` as non-routine context recovery"
        )
