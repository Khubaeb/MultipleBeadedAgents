"""Assignment-contract template generator (``assignment_contract``).

Acceptance row coverage (Primitives AC #4):

* Fills the Markdown template from ``docs/mba/charter.md`` §10 with the
  supplied fields; missing fields surface as explicit ``(None)``
  markers, never as silently dropped defaults.
* Returns the path of the written ``prompt.md`` (within the §10
  ``.mba-work/<bead>/<session>/`` layout).

The Orchestrator uses this helper for every worker session
(``docs/mba/charter.md`` §10 Assignment contract). The contract is the
single contract; stages and organizational roles fill its ``Stage``,
``Organizational role`` and ``Task`` fields rather than choosing a
different template.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .constants import NONE_MARKER

# Canonical §10 template (verbatim from docs/mba/charter.md). The
# template uses ``<placeholder>`` markers; ``_render`` substitutes
# them with values supplied to ``assignment_contract``.
_ASSIGNMENT_CONTRACT_TEMPLATE: str = """# {role} {heading}

- **Bead:** {bead}
- **Stage:** {stage}
- **Responsibility:** {responsibility}
- **Organizational role:** {organizational_role}
- **Session purpose:** {session_purpose}
- **Task:** {task}
- **Read:** {read}
- **Produce:** {produce}
- **Acceptance:** {acceptance}
- **Authority and limits:** {authority_and_limits}
- **Worker-internal AI delegation:** Record either `none` or every worker-internal AI delegation in the Bead comment or its linked details file, including role, tool/session type, and purpose. Do not include private transcript contents or inspect personal user sessions.

## Session boundary

- This prompt is for a worker session launched or resumed for this exact responsibility.
- Do not complete both Doer and Auditor work inside one transcript by changing labels.
- If you are the Orchestrator transcript and no separate worker session was launched, stop and report `BLOCKED` or ask the user; do not manufacture convergence.

## Beads write rules

- If this assignment allows Beads writes, every write uses an explicit role actor: `bd --actor "{role}" ...`; never rely on the OS/Git/default actor.
- Do not use `bd update --claim` for AI-role work; set status and assignee explicitly when allowed.
- Write comment text to `comment.md`, then post it with: `bd --actor "{role}" comments add {bead} -f <comment.md>`.
- On Windows/PowerShell, avoid Bash-only forms such as `||`, `head`, `wc`, `ls -la`, heredocs, `bd comments add -t -`, and `-f -`.
"""


def _coerce(value: Any) -> str:
    """Render a value for the contract.

    ``None`` → ``(None)`` marker (per AC #4). Lists and tuples are
    rendered as Markdown bullets. Other types use ``str(value)``.
    """

    if value is None:
        return NONE_MARKER
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        if not value:
            return NONE_MARKER
        return "\n".join(f"- {item}" for item in value)
    if isinstance(value, Mapping):
        if not value:
            return NONE_MARKER
        return "\n".join(f"- {k}: {v}" for k, v in value.items())
    return str(value)


def _render(
    *,
    role: Any,
    bead: Any,
    stage: Any,
    session_purpose: Any,
    task: Any,
    read: Any,
    produce: Any,
    acceptance: Any,
    authority_and_limits: Any,
    responsibility: Any,
    organizational_role: Any,
    heading: str,
) -> str:
    """Render the contract with the supplied values.

    ``responsibility`` and ``organizational_role`` are derived from
    ``role`` and explicit kwargs so the template matches the
    Orchestrator's worker prompt shape (Doer/Auditor + hat).
    """

    return _ASSIGNMENT_CONTRACT_TEMPLATE.format(
        role=_coerce(role),
        heading=heading,
        bead=_coerce(bead),
        stage=_coerce(stage),
        responsibility=_coerce(responsibility),
        organizational_role=_coerce(organizational_role),
        session_purpose=_coerce(session_purpose),
        task=_coerce(task),
        read=_coerce(read),
        produce=_coerce(produce),
        acceptance=_coerce(acceptance),
        authority_and_limits=_coerce(authority_and_limits),
    )


def assignment_contract(
    role: Any,
    bead: Any,
    stage: Any,
    session_purpose: Any,
    task: Any,
    read: Any,
    produce: Any,
    acceptance: Any,
    authority_and_limits: Any,
    *,
    session_name: str | None = None,
    base_dir: Path | None = None,
    responsibility: Any = None,
    heading: str | None = None,
) -> Path:
    """Write the §10 assignment-contract ``prompt.md`` and return its path.

    Parameters
    ----------
    role, bead, stage, session_purpose, task, read, produce, acceptance, authority_and_limits
        The nine contract fields listed in the AC. ``None`` for any of
        these surfaces an explicit ``(None)`` marker (AC #4). Lists and
        mappings are rendered as Markdown bullets.
    session_name
        Optional directory name under ``.mba-work/<bead>/``. Defaults
        to ``f"{bead}-{role_slug}"`` when omitted so the helper can be
        used in the §10 layout without requiring the caller to invent
        a session name.
    base_dir
        Working directory whose ``.mba-work/<bead>/<session>/`` will
        be written. Defaults to ``Path.cwd()``.
    responsibility
        The Orchestrator's responsibility for this contract (Doer /
        Auditor). ``None`` (the default) surfaces the explicit
        ``(None)`` marker on the §10 Responsibility line — consistent
        with AC #4's philosophy of never silently substituting a
        default for a missing value. The runtime must pass this
        explicitly when the responsibility is known; the marker
        signals "unknown / not supplied" rather than masking it with
        the organisational role.
    heading
        Optional heading fragment. Defaults to ``"Assignment"`` (matching
        ``docs/mba/charter.md`` §10 verbatim); tests can substitute
        ``"Engineer — Primitives"`` for the per-stage variant.
    """

    root = (base_dir or Path.cwd()).resolve()
    resolved_session = session_name or _default_session_name(bead=bead, role=role)
    bead_dir = root / ".mba-work" / str(bead) / resolved_session
    bead_dir.mkdir(parents=True, exist_ok=True)

    rendered = _render(
        role=role,
        bead=bead,
        stage=stage,
        session_purpose=session_purpose,
        task=task,
        read=read,
        produce=produce,
        acceptance=acceptance,
        authority_and_limits=authority_and_limits,
        responsibility=responsibility,
        organizational_role=role,
        heading=heading or "Assignment",
    )
    prompt_path = bead_dir / "prompt.md"
    prompt_path.write_text(rendered, encoding="utf-8")
    return prompt_path


def render_contract_text(
    role: Any,
    bead: Any,
    stage: Any,
    session_purpose: Any,
    task: Any,
    read: Any,
    produce: Any,
    acceptance: Any,
    authority_and_limits: Any,
    *,
    responsibility: Any = None,
    heading: str | None = None,
) -> str:
    """Return the rendered contract text without writing it.

    Useful for tests that want to assert on the rendered Markdown
    directly (no disk side effect).
    """

    return _render(
        role=role,
        bead=bead,
        stage=stage,
        session_purpose=session_purpose,
        task=task,
        read=read,
        produce=produce,
        acceptance=acceptance,
        authority_and_limits=authority_and_limits,
        responsibility=responsibility,
        organizational_role=role,
        heading=heading or "Assignment",
    )


def _default_session_name(*, bead: Any, role: Any) -> str:
    bead_part = str(bead) if bead is not None else "bead"
    role_part = _slugify(str(role) if role is not None else "session")
    return f"{bead_part}-{role_part}"


def _slugify(text: str) -> str:
    out: list[str] = []
    for char in text.lower():
        if char.isalnum() or char in "-_":
            out.append(char)
        elif char.isspace():
            out.append("-")
    slug = "".join(out).strip("-")
    return slug or "session"
