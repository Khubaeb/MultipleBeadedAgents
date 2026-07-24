"""AI-resource record loader and dataclasses.

The runtime reads its AI / session / pattern configuration from
``.mba-work/.ai-resources.json``. The schema is intentionally small
(Charter §4: "The Orchestrator asks the user about available AIs and may
offer read-only checks."). The runtime never invents AIs; the user
populates the record at project setup.

Schema (in JSON, indented for readability, but the parser is order-
independent):

.. code-block:: json

    {
      "schema": 1,
      "note": "local-only AI-resource record (Constraint 20 / F4).",
      "resources": [
        {
          "id": "minimax",
          "label": "MiniMax-M3",
          "capabilities": ["doer", "auditor"],
          "launch": {
            "tool": "opencode",
            "model": "minimax-coding-plan/MiniMax-M3",
            "variant": "max"
          },
          "session_lifetime": "fresh_per_session"
        },
        {
          "id": "claude",
          "label": "Claude Opus 4.8",
          "capabilities": ["doer", "auditor"],
          "launch": {
            "tool": "claude-cli",
            "model": "opus-4.8",
            "variant": "max"
          },
          "session_lifetime": "fresh_per_session"
        }
      ],
      "teams": {
        "default": {
          "doer": {
            "ai": "minimax",
            "hat": "Engineer",
            "session_count": 1
          },
          "auditor": {
            "ai": "claude",
            "hat": "Workflow Auditor",
            "session_count": 1
          },
          "pattern": "b"
        },
        "one-ai": {
          "doer": {"ai": "minimax", "hat": "Engineer", "session_count": 1},
          "auditor": {"ai": "minimax", "hat": "Workflow Auditor", "session_count": 1},
          "pattern": "a"
        }
      }
    }

* ``resources`` is the catalogue of AIs the project knows about. Each
  entry's ``id`` is referenced from teams. The ``launch`` object carries
  the real invocation identity; the ``id`` is never a model string.
* ``teams`` is a named set of pre-recorded configurations. The runtime
  picks one (default = ``"default"``) and routes per the chosen
  pattern. The runtime never invents a config; absent teams raise a
  refusal-grade error.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import bd_client
from .constants import AI_RESOURCE_RECORD, MBA_WORK_DIR


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AIResourceError(ValueError):
    """Raised when the AI-resource record is missing or unusable."""


RESOURCE_SETUP_QUESTIONS: tuple[str, ...] = (
    "Which AI/session is the Orchestrator for this repo?",
    "Which callable AIs or plans are available, and what is each exact launch tool, model and variant?",
    "Which AI should be the default Doer and which should be the default Auditor?",
    "Should any role use multiple independent sessions for critical work?",
    "May the Orchestrator run read-only local checks to discover available AI CLIs?",
)

SETUP_BEAD_TITLE = "MBA setup"
SETUP_BEAD_LABELS: tuple[str, ...] = ("mba", "setup", "human")
SETUP_BEAD_DESCRIPTION: str = (
    "MBA cold-start setup is blocked until the Human supplies a valid "
    "`.mba-work/.ai-resources.json`. Populate the record with the "
    "available AIs and the chosen team configuration, then rerun "
    "`python -m mba_runtime first-contact --cwd . --apply-setup` (the "
    "runtime returns exit 0 only when the record is ready). The deterministic "
    "setup-question comment on this Bead lists the exact data the "
    "runtime needs."
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LaunchConfig:
    """How an AI resource is invoked.

    ``AIResource.id`` is a stable project nickname. It is deliberately
    not the launch model, because using a nickname as a CLI model caused
    real worker launch failures. ``model`` must be the exact provider /
    CLI model identifier the launch surface expects.
    """

    tool: str                            # e.g. "opencode", "claude-cli"
    model: str                           # exact launch model string
    variant: str = ""                    # effort / variant, when supported


@dataclass(frozen=True)
class AIResource:
    """One AI in the catalogue."""

    id: str                              # unique short identifier
    label: str                           # human-readable label
    capabilities: tuple[str, ...]        # subset of ("doer", "auditor")
    session_lifetime: str                # documented lifetime ("fresh_per_session", ...)
    launch: LaunchConfig | None = None   # executable launch identity

    @property
    def can_doer(self) -> bool:
        return "doer" in self.capabilities

    @property
    def can_auditor(self) -> bool:
        return "auditor" in self.capabilities


@dataclass(frozen=True)
class ResponsibilityConfig:
    """How one responsibility is staffed in a team config."""

    ai: str                              # AI id from the resources catalogue
    hat: str                             # organisational role / hat
    session_count: int                   # number of fresh sessions

    def __post_init__(self) -> None:
        if self.session_count < 1:
            raise AIResourceError(
                f"session_count must be >= 1; got {self.session_count!r}"
            )


@dataclass(frozen=True)
class TeamConfig:
    """One named configuration of responsibilities + pattern."""

    name: str
    doer: ResponsibilityConfig
    auditor: ResponsibilityConfig
    pattern: str                         # a / b / c / d

    def responsible_ai_ids(self) -> tuple[str, ...]:
        """Distinct AI ids used by this team, in config order."""

        return tuple(dict.fromkeys((self.doer.ai, self.auditor.ai)))


@dataclass(frozen=True)
class AIResourceRecord:
    """Whole-file view of ``.mba-work/.ai-resources.json``."""

    schema: int
    note: str
    resources: tuple[AIResource, ...]
    teams: dict[str, TeamConfig]

    def resource_by_id(self, ai_id: str) -> AIResource:
        for r in self.resources:
            if r.id == ai_id:
                return r
        raise AIResourceError(
            f"AI id {ai_id!r} is referenced by a team but missing from "
            f"the resources catalogue; the AI-resource record is "
            f"inconsistent"
        )


@dataclass(frozen=True)
class ResourcePreflight:
    """First-contact verdict for the local AI-resource record."""

    ok: bool
    path: Path
    reason: str
    questions: tuple[str, ...] = RESOURCE_SETUP_QUESTIONS
    resources: tuple[str, ...] = ()
    teams: tuple[str, ...] = ()
    default_team_ready: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "path": str(self.path),
            "reason": self.reason,
            "questions": list(self.questions),
            "resources": list(self.resources),
            "teams": list(self.teams),
            "default_team_ready": self.default_team_ready,
        }


def setup_bead_guidance(state: ResourcePreflight) -> dict[str, object]:
    questions = list(state.questions)
    comment_lines = [
        "## MBA setup",
        "",
        "- **Status:** blocked pending Human setup.",
        f"- **Reason:** {state.reason}",
        "- **Questions:**",
        *[f"  {index}. {question}" for index, question in enumerate(questions, 1)],
        "- **Next:** update the private AI-resource record and rerun first-contact.",
    ]
    return {
        "action": "create_or_update",
        "title": SETUP_BEAD_TITLE,
        "type": "task",
        "status": "blocked",
        "assignee": "Human",
        "labels": list(SETUP_BEAD_LABELS),
        "comment": {
            "format": "markdown",
            "body": "\n".join(comment_lines),
            "questions": questions,
        },
        "allowed_now": [
            "create_or_update_setup_bead",
            "post_setup_questions",
        ],
        "blocked_until_ready": [
            "create_or_drive_executable_beads",
            "launch_workers",
        ],
    }


# ---------------------------------------------------------------------------
# Setup-handoff applier (example-setup ``--apply-setup``)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SetupApplyResult:
    """Result of :func:`apply_setup_handoff`.

    Either ``bead_id`` is set (a write happened and the comment was
    posted) or ``skipped`` is ``True`` (resources were ready, so no
    Bead write happens).
    """

    bead_id: str = ""
    comment_path: str = ""
    created: bool = False
    skipped: bool = False
    skipped_reason: str = ""

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "applied": not self.skipped,
            "created": self.created,
            "bead_id": self.bead_id,
            "status": SETUP_BEAD_GUIDANCE_STATUS,
            "assignee": SETUP_BEAD_GUIDANCE_ASSIGNEE,
            "labels": list(SETUP_BEAD_LABELS),
            "comment_path": self.comment_path,
        }
        if self.skipped:
            out["applied"] = False
            out["skipped"] = True
            out["skipped_reason"] = self.skipped_reason
            # Empty fields for the ready-state case so the JSON shape is
            # the same regardless of branch.
            out["bead_id"] = ""
            out["created"] = False
            out["comment_path"] = ""
        return out


SETUP_BEAD_GUIDANCE_STATUS: str = "blocked"
SETUP_BEAD_GUIDANCE_ASSIGNEE: str = "Human"


def _run_bd(
    bd_binary: str,
    argv: tuple[str, ...],
    *,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    """Issue a ``bd`` subcommand with the explicit ``Orchestrator`` actor.

    The applier is the writer; the comment is the audit trail. Every
    write here MUST be attributed to ``"Orchestrator"`` so a future
    reviewer can distinguish the runtime-driven setup handoff from
    any later Human write on the same Bead.
    """

    proc_argv = ("--actor", "Orchestrator", *argv)
    return bd_client.call(bd_binary, args=list(proc_argv), cwd=cwd)


def _bd_create_setup_bead(
    bd_binary: str, *, cwd: Path
) -> str:
    """Run ``bd create`` for the MBA setup Bead; return its id."""

    argv = (
        "create",
        "--title",
        SETUP_BEAD_TITLE,
        "--type",
        "task",
        "--priority",
        "2",
        "--assignee",
        SETUP_BEAD_GUIDANCE_ASSIGNEE,
        "--labels",
        ",".join(SETUP_BEAD_LABELS),
        "--description",
        SETUP_BEAD_DESCRIPTION,
        "--json",
    )
    proc = _run_bd(bd_binary, argv, cwd=cwd)
    if proc.returncode != 0:
        raise AIResourceError(
            f"`bd create --title={SETUP_BEAD_TITLE!r}` exited "
            f"{proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()!r}"
        )
    # ``bd create --json`` prints one JSON object on its last line; we
    # tolerate ``bd`` versions that wrap the JSON in a list or quote it.
    text = proc.stdout.strip()
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("id"), str):
            return str(payload["id"])
    # Fallback: try the last raw JSON value (handles ``[ {...} ]``).
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIResourceError(
            f"`bd create` returned no parseable Bead id: "
            f"{proc.stdout!r}"
        ) from exc
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict) and isinstance(first.get("id"), str):
            return str(first["id"])
    if isinstance(payload, dict) and isinstance(payload.get("id"), str):
        return str(payload["id"])
    raise AIResourceError(
        f"`bd create` returned no parseable Bead id: {proc.stdout!r}"
    )


def _bd_update_setup_bead(
    bead_id: str, bd_binary: str, *, cwd: Path
) -> None:
    """Run ``bd update`` to enforce status / assignee / labels."""

    argv = (
        "update",
        bead_id,
        "--status",
        SETUP_BEAD_GUIDANCE_STATUS,
        "--assignee",
        SETUP_BEAD_GUIDANCE_ASSIGNEE,
        "--set-labels",
        ",".join(SETUP_BEAD_LABELS),
    )
    proc = _run_bd(bd_binary, argv, cwd=cwd)
    if proc.returncode != 0:
        raise AIResourceError(
            f"`bd update {bead_id}` exited {proc.returncode}: "
            f"{proc.stderr.strip() or proc.stdout.strip()!r}"
        )


def _render_setup_comment(state: ResourcePreflight, bead_id: str) -> str:
    """Render the brief structured Markdown comment for the Bead.

    Uses :func:`_short_state_reason` to keep the rendered
    ``verification`` line at the §10 word cap.
    """

    questions = list(state.questions)
    reason = _short_state_reason(state.reason)
    bullet_questions = "".join(
        f"\n  {index}. {q}" for index, q in enumerate(questions, 1)
    )
    text = (
        f"- **result:** created/updated blocked `MBA setup` Bead "
        f"`{bead_id}`; Orchestrator stopped before executable Beads "
        f"or worker launches.\n"
        f"- **changes:** status=blocked; assignee=Human; "
        f"labels=mba,setup,human applied with explicit "
        f"`--actor Orchestrator`.\n"
        f"- **verification:** resource_preflight.ok=False; reason: "
        f"{reason}\n"
        f"- **questions:**{bullet_questions}\n"
        f"- **next:** populate `.mba-work/.ai-resources.json` then "
        f"rerun `python -m mba_runtime first-contact --cwd . --apply-setup`; "
        f"ready-state writes nothing.\n"
        f"- **details:** Bead comment is complete; no separate file.\n"
    )
    _validate_comment_shape(text)
    return text


def _short_state_reason(reason: str) -> str:
    """Cap the rendered ``reason`` to the §10 word cap tail.

    The full preflight reason can be long; the comment carries only
    the leading one-sentence summary so the §10 word cap holds.
    """

    first = reason.splitlines()[0] if reason else ""
    words = first.split()
    if len(words) > 24:
        first = " ".join(words[:24]) + "..."
    return first.strip()


def _validate_comment_shape(text: str) -> None:
    """Match the brief Charter §10 shape (no need to import comments).

    The helper is local because :mod:`mba_runtime.comments` requires a
    `bd` write to validate; here we only want the structural check.
    """

    words = text.split()
    if len(words) > 260:
        raise AIResourceError(
            f"setup comment is {len(words)} words; §10 caps at 260"
        )
    non_blank = [ln for ln in text.splitlines() if ln.strip()]
    if not (4 <= len(non_blank) <= 16):
        raise AIResourceError(
            f"setup comment has {len(non_blank)} non-blank lines; "
            "§10 calls for 4 to 16"
        )


def _bd_post_setup_comment(
    comment_path: Path, bead_id: str, bd_binary: str, *, cwd: Path
) -> None:
    """Run ``bd comments add`` and raise on non-zero returncode."""

    argv = (
        "comments",
        "add",
        bead_id,
        "-f",
        str(comment_path),
    )
    proc = _run_bd(bd_binary, argv, cwd=cwd)
    if proc.returncode != 0:
        raise AIResourceError(
            f"`bd comments add {bead_id} -f {comment_path}` exited "
            f"{proc.returncode}: "
            f"{proc.stderr.strip() or proc.stdout.strip()!r}"
        )


def _existing_setup_ids(bd_binary: str, *, cwd: Path) -> tuple[str, ...]:
    """Return existing open ``MBA setup`` Bead ids, or ``()``.

    Best effort: any non-zero ``returncode`` or malformed payload
    resolves to an empty tuple so the applier creates a fresh Bead.
    """

    proc = bd_client.call(
        bd_binary,
        args=[
            "--actor",
            "Orchestrator",
            "list",
            "--title",
            SETUP_BEAD_TITLE,
            "--all",
            "--json",
        ],
        cwd=cwd,
    )
    if proc.returncode != 0:
        return ()
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return ()
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("issues"), list):
        rows = payload["issues"]
    else:
        return ()
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not isinstance(row.get("id"), str):
            continue
        # Skip closed setup beads; the applier should reuse an open one.
        status = str(row.get("status", "")).lower()
        if status == "closed":
            continue
        out.append(row["id"])
    return tuple(out)


def apply_setup_handoff(
    state: ResourcePreflight,
    *,
    bd_binary: str,
    cwd: Path,
) -> SetupApplyResult:
    """Create or update the blocked ``MBA setup`` Bead and post its comment.

    This function is the runtime helper backing the
    ``first-contact --apply-setup`` CLI flag. It is invoked when
    ``state.ok`` is ``False``; a ready preflight yields the
    :class:`SetupApplyResult` with ``skipped=True`` so the CLI can
    surface "no work needed" without writing a Bead.

    Parameters
    ----------
    state : ResourcePreflight
        The current preflight verdict.
    bd_binary : str
        ``bd`` binary name (production ``"bd"``; tests use the
        in-process stub override via
        :func:`mba_runtime.bd_client.set_subprocess_invoker_override`).
    cwd : Path
        Project root.

    Returns
    -------
    SetupApplyResult
        JSON-serialisable result; the CLI attaches ``to_dict()`` to
        the ``first-contact --apply-setup`` output.
    """

    if state.ok:
        return SetupApplyResult(
            skipped=True,
            skipped_reason=(
                "AI resources are ready; first-contact did not create "
                "or update the MBA setup Bead."
            ),
        )

    artifact_dir = cwd / ".mba-work" / "_setup-runtime"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    comment_path = artifact_dir / "setup-comment.md"

    existing = _existing_setup_ids(bd_binary, cwd=cwd)
    if existing:
        bead_id = existing[0]
        created = False
    else:
        bead_id = _bd_create_setup_bead(bd_binary, cwd=cwd)
        created = True
    _bd_update_setup_bead(bead_id, bd_binary, cwd=cwd)

    comment_text = _render_setup_comment(state, bead_id)
    comment_path.write_text(comment_text, encoding="utf-8")
    _bd_post_setup_comment(comment_path, bead_id, bd_binary, cwd=cwd)

    return SetupApplyResult(
        bead_id=bead_id,
        comment_path=str(comment_path),
        created=created,
    )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _coerce_capabilities(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, (list, tuple)):
        return tuple(str(part).strip() for part in value if str(part).strip())
    raise AIResourceError(
        f"capabilities must be a list or comma-separated string; got "
        f"{type(value).__name__}"
    )


def _parse_resource(raw: object) -> AIResource:
    if not isinstance(raw, dict):
        raise AIResourceError(
            f"resource entries must be objects; got {type(raw).__name__}"
        )
    rid = raw.get("id")
    label = raw.get("label")
    if not isinstance(rid, str) or not rid:
        raise AIResourceError("resource.id must be a non-empty string")
    if not isinstance(label, str) or not label:
        raise AIResourceError(f"resource {rid!r}: label must be a non-empty string")
    return AIResource(
        id=rid,
        label=label,
        capabilities=_coerce_capabilities(raw.get("capabilities")),
        session_lifetime=str(raw.get("session_lifetime", "fresh_per_session")),
        launch=_parse_launch_config(raw.get("launch"), resource_id=rid),
    )


def _parse_launch_config(
    raw: object, *, resource_id: str
) -> LaunchConfig | None:
    """Parse the optional resource launch config.

    A missing launch object is tolerated by the parser so tooling can
    load older records and report a precise first-contact setup block.
    ``resource_preflight`` is the readiness gate.
    """

    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise AIResourceError(
            f"resource {resource_id!r}: launch must be an object"
        )
    tool = raw.get("tool")
    model = raw.get("model")
    variant = raw.get("variant", "")
    if not isinstance(tool, str) or not tool.strip():
        raise AIResourceError(
            f"resource {resource_id!r}: launch.tool must be a non-empty string"
        )
    if not isinstance(model, str) or not model.strip():
        raise AIResourceError(
            f"resource {resource_id!r}: launch.model must be a non-empty string"
        )
    if variant is None:
        variant = ""
    if not isinstance(variant, str):
        raise AIResourceError(
            f"resource {resource_id!r}: launch.variant must be a string"
        )
    if tool.strip().lower() == "opencode" and "/" not in model:
        raise AIResourceError(
            f"resource {resource_id!r}: OpenCode launch.model must be the "
            "real provider/model string, for example "
            "'minimax-coding-plan/MiniMax-M3'; do not use the resource id"
        )
    return LaunchConfig(
        tool=tool.strip(),
        model=model.strip(),
        variant=variant.strip(),
    )


def _parse_responsibility(
    raw: object, *, side: str, team_name: str
) -> ResponsibilityConfig:
    if not isinstance(raw, dict):
        raise AIResourceError(
            f"team {team_name!r}: {side} config must be an object; got "
            f"{type(raw).__name__}"
        )
    ai = raw.get("ai")
    if not isinstance(ai, str) or not ai:
        raise AIResourceError(f"team {team_name!r}: {side}.ai must be a string")
    hat = raw.get("hat")
    if not isinstance(hat, str) or not hat:
        raise AIResourceError(f"team {team_name!r}: {side}.hat must be a string")
    count = raw.get("session_count", 1)
    if not isinstance(count, int):
        raise AIResourceError(
            f"team {team_name!r}: {side}.session_count must be an integer"
        )
    return ResponsibilityConfig(ai=ai, hat=hat, session_count=count)


def _parse_team(name: str, raw: object) -> TeamConfig:
    if not isinstance(raw, dict):
        raise AIResourceError(f"team {name!r} must be an object")
    pattern = raw.get("pattern")
    if pattern not in ("a", "b", "c", "d"):
        raise AIResourceError(
            f"team {name!r}: pattern must be 'a', 'b', 'c', or 'd'; "
            f"got {pattern!r}"
        )
    doer = _parse_responsibility(raw.get("doer"), side="doer", team_name=name)
    auditor = _parse_responsibility(
        raw.get("auditor"), side="auditor", team_name=name
    )
    return TeamConfig(name=name, doer=doer, auditor=auditor, pattern=pattern)


def parse_ai_resource_record(payload: object) -> AIResourceRecord:
    """Parse a parsed JSON object into the dataclasses."""

    if not isinstance(payload, dict):
        raise AIResourceError(
            f"AI-resource record must be a JSON object; got {type(payload).__name__}"
        )
    schema = payload.get("schema")
    note = payload.get("note", "")
    resources_raw = payload.get("resources", [])
    teams_raw = payload.get("teams", {})

    if not isinstance(schema, int):
        raise AIResourceError("AI-resource record: schema must be an integer")
    if not isinstance(note, str):
        raise AIResourceError("AI-resource record: note must be a string")
    if not isinstance(resources_raw, (list, tuple)):
        raise AIResourceError("AI-resource record: resources must be a list")
    if not isinstance(teams_raw, dict):
        raise AIResourceError("AI-resource record: teams must be an object")

    resources = tuple(_parse_resource(item) for item in resources_raw)
    teams = {
        name: _parse_team(name, body)
        for name, body in teams_raw.items()
    }
    return AIResourceRecord(
        schema=schema, note=note, resources=resources, teams=teams
    )


def load_ai_resource_record(
    cwd: Path, *, record_path: Path | None = None
) -> AIResourceRecord:
    """Read the AI-resource record from disk.

    ``record_path`` overrides the default lookup at
    ``<cwd>/.mba-work/.ai-resources.json``. The helper refuses to
    invent defaults: a missing file raises :class:`AIResourceError`
    because the user must populate the record at project setup.
    """

    path = record_path or (cwd / AI_RESOURCE_RECORD)
    if not path.exists():
        raise AIResourceError(
            f"AI-resource record is absent at {path}. Populate it at "
            f"project setup with the available AIs and the chosen team "
            f"configuration; the runtime refuses to invent defaults."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AIResourceError(
            f"AI-resource record at {path} is not valid JSON: {exc}"
        ) from exc
    return parse_ai_resource_record(payload)


def resource_preflight(
    cwd: Path, *, record_path: Path | None = None, team_name: str = "default"
) -> ResourcePreflight:
    """Return the first-contact AI-resource setup status.

    This is deliberately read-only. The active Orchestrator uses the
    verdict to ask the user for the missing configuration or for
    permission to run read-only discovery checks. MBA never invents a
    provider, plan, model, session lifetime, or team.
    """

    path = record_path or (cwd / AI_RESOURCE_RECORD)
    try:
        record = load_ai_resource_record(cwd, record_path=path)
    except AIResourceError as exc:
        return ResourcePreflight(
            ok=False,
            path=path,
            reason=str(exc),
        )

    resource_ids = tuple(r.id for r in record.resources)
    team_names = tuple(sorted(record.teams))
    if not record.resources:
        return ResourcePreflight(
            ok=False,
            path=path,
            reason="AI-resource record has no resources.",
            resources=resource_ids,
            teams=team_names,
        )
    if team_name not in record.teams:
        return ResourcePreflight(
            ok=False,
            path=path,
            reason=(
                f"AI-resource record has no {team_name!r} team; available "
                f"teams: {list(team_names)}"
            ),
            resources=resource_ids,
            teams=team_names,
        )

    team = record.teams[team_name]
    try:
        doer_ai = record.resource_by_id(team.doer.ai)
        auditor_ai = record.resource_by_id(team.auditor.ai)
    except AIResourceError as exc:
        return ResourcePreflight(
            ok=False,
            path=path,
            reason=str(exc),
            resources=resource_ids,
            teams=team_names,
        )

    if not doer_ai.can_doer:
        return ResourcePreflight(
            ok=False,
            path=path,
            reason=f"default Doer AI {doer_ai.id!r} lacks the doer capability.",
            resources=resource_ids,
            teams=team_names,
        )
    if not auditor_ai.can_auditor:
        return ResourcePreflight(
            ok=False,
            path=path,
            reason=(
                f"default Auditor AI {auditor_ai.id!r} lacks the auditor "
                "capability."
            ),
            resources=resource_ids,
            teams=team_names,
        )

    for configured_ai in (doer_ai, auditor_ai):
        if configured_ai.launch is None:
            return ResourcePreflight(
                ok=False,
                path=path,
                reason=(
                    f"default team AI {configured_ai.id!r} lacks "
                    "launch.tool and launch.model. Resource id is only a "
                    "nickname; launch.model must be the real CLI/provider "
                    "model string."
                ),
                resources=resource_ids,
                teams=team_names,
            )

    return ResourcePreflight(
        ok=True,
        path=path,
        reason="AI-resource record is present and the default team is usable.",
        questions=(),
        resources=resource_ids,
        teams=team_names,
        default_team_ready=True,
    )


def team_config(
    record: AIResourceRecord, *, team_name: str = "default"
) -> TeamConfig:
    """Return the chosen team config (default = ``"default"``)."""

    if team_name not in record.teams:
        raise AIResourceError(
            f"team {team_name!r} is absent from the AI-resource record; "
            f"available teams: {sorted(record.teams)}"
        )
    return record.teams[team_name]


def save_ai_resource_record(
    cwd: Path,
    record: AIResourceRecord,
    *,
    record_path: Path | None = None,
) -> Path:
    """Persist an ``AIResourceRecord`` (used by the init helper in tests)."""

    path = record_path or (cwd / AI_RESOURCE_RECORD)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": record.schema,
        "note": record.note,
        "resources": [
            {
                "id": r.id,
                "label": r.label,
                "capabilities": list(r.capabilities),
                "session_lifetime": r.session_lifetime,
                **(
                    {
                        "launch": {
                            "tool": r.launch.tool,
                            "model": r.launch.model,
                            "variant": r.launch.variant,
                        }
                    }
                    if r.launch is not None
                    else {}
                ),
            }
            for r in record.resources
        ],
        "teams": {
            name: {
                "pattern": t.pattern,
                "doer": {
                    "ai": t.doer.ai,
                    "hat": t.doer.hat,
                    "session_count": t.doer.session_count,
                },
                "auditor": {
                    "ai": t.auditor.ai,
                    "hat": t.auditor.hat,
                    "session_count": t.auditor.session_count,
                },
            }
            for name, t in record.teams.items()
        },
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def default_record(
    *,
    minimax: bool = True,
    claude: bool = True,
) -> AIResourceRecord:
    """Return a default record for testing.

    The runtime never invents AIs in production; this helper only
    exists so tests have a canonical fixture. Production must read
    from the record the user populated at project setup.
    """

    resources: list[AIResource] = []
    teams: dict[str, TeamConfig] = {}

    if minimax:
        resources.append(
            AIResource(
                id="minimax",
                label="MiniMax-M3",
                capabilities=("doer", "auditor"),
                session_lifetime="fresh_per_session",
                launch=LaunchConfig(
                    tool="opencode",
                    model="minimax-coding-plan/MiniMax-M3",
                    variant="max",
                ),
            )
        )
    if claude:
        resources.append(
            AIResource(
                id="claude",
                label="Claude Opus 4.8",
                capabilities=("doer", "auditor"),
                session_lifetime="fresh_per_session",
                launch=LaunchConfig(
                    tool="claude-cli",
                    model="opus-4.8",
                    variant="max",
                ),
            )
        )

    if minimax and claude:
        teams["default"] = TeamConfig(
            name="default",
            doer=ResponsibilityConfig(
                ai="minimax", hat="Engineer", session_count=1
            ),
            auditor=ResponsibilityConfig(
                ai="claude", hat="Workflow Auditor", session_count=1
            ),
            pattern="b",
        )
        teams["pattern-a"] = TeamConfig(
            name="pattern-a",
            doer=ResponsibilityConfig(
                ai="minimax", hat="Engineer", session_count=1
            ),
            auditor=ResponsibilityConfig(
                ai="minimax", hat="Workflow Auditor", session_count=1
            ),
            pattern="a",
        )

    return AIResourceRecord(
        schema=1,
        note="local-only AI-resource record (Constraint 20 / F4).",
        resources=tuple(resources),
        teams=teams,
    )
