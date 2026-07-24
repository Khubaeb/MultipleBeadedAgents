"""Detect Beads → install/initialise with explicit user authority + nested-init guard.

Acceptance row coverage (Foundation AC #3, AC #4):

* Detect Beads; on missing/invalid ``.beads/``, prompt the user for
  authority to install/initialise. **No silent install/init.** (Charter §9
  rule 1 + ``docs/beads/capabilities.md`` Setup integration Core row.)
* Nested-init guard: refuse ``bd init`` (non-zero exit + explanation)
  inside a directory whose ancestor Beads workspace owns a ``sync.remote``.
  (Conditional row in capabilities.md: Conditional behavior used in its
  stated case; recorded in this Stage because the install/initialise flow
  must never silently initialise a workspace nested under a project-owned
  Beads remote.)
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


@dataclass(frozen=True)
class DetectOutcome:
    """What `detect_beads` found."""

    beads_dir: Path
    valid: bool                 # True ⇢ `.beads/` exists AND `metadata.json` parses.
    backend: str | None         # The storage engine (`dolt` / `sqlite` / None).
    mode: str | None            # `embedded` / `server` / None — drives sync guard.
    sync_remote: str | None     # `git+...` if `.beads/config.yaml` declares one.
    issues_path: Path | None    # `.beads/issues.jsonl` if present.
    reason: str                 # Empty on success; explanation when ``valid=False``.


@dataclass(frozen=True)
class NestedInitCheck:
    """Result of the nested-init guard."""

    ancestor_beads_dir: Path | None      # The first ancestor owning `.beads/`.
    ancestor_sync_remote: str | None     # The ``sync.remote`` that triggered the guard.
    blocked: bool                        # True ⇢ refuse `bd init` in the nested cwd.
    reason: str


def _read_yaml_scalar(path: Path, key: str) -> str | None:
    """Tiny single-key YAML reader for ``.beads/config.yaml``.

    A full YAML dependency is overkill for ``sync.remote``. We parse the
    one ``key: value`` line we care about. Quote handling is best-effort.

    Empty-scalar handling (Audit finding F2, turn-2 correction):
    ``sync.remote: ""`` and ``sync.remote:''`` both yield ``None``. The
    quote-strip runs **before** the empty check so the empty-string
    literal cannot be returned in place of an absent value. Bare
    ``sync.remote:`` (no value) also returns ``None``. Any non-empty
    value is returned as-is (without quote stripping affecting
    non-quoted scalars).
    """

    if not path.exists():
        return None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line:
            continue
        if ":" not in line:
            continue
        head, _, tail = line.partition(":")
        if head.strip() != key:
            continue
        value = tail.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        if not value:
            return None
        return value
    return None


def detect_beads(cwd: Path) -> DetectOutcome:
    """Return the Beads state of ``cwd``.

    A "valid" workspace is one whose ``.beads/metadata.json`` parses as
    JSON and reports a ``database`` we recognise.
    """

    beads_dir = (cwd / ".beads").resolve()
    issues_path = beads_dir / "issues.jsonl" if (beads_dir / "issues.jsonl").exists() else None
    if not beads_dir.exists():
        return DetectOutcome(
            beads_dir=beads_dir,
            valid=False,
            backend=None,
            mode=None,
            sync_remote=None,
            issues_path=None,
            reason=f".beads/ not present at {beads_dir}",
        )

    metadata_path = beads_dir / "metadata.json"
    if not metadata_path.exists():
        return DetectOutcome(
            beads_dir=beads_dir,
            valid=False,
            backend=None,
            mode=None,
            sync_remote=None,
            issues_path=issues_path,
            reason=f".beads/metadata.json missing at {metadata_path}",
        )

    try:
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return DetectOutcome(
            beads_dir=beads_dir,
            valid=False,
            backend=None,
            mode=None,
            sync_remote=None,
            issues_path=issues_path,
            reason=f".beads/metadata.json is malformed: {exc}",
        )

    backend = str(meta.get("backend") or meta.get("database") or "")
    mode = str(meta.get("dolt_mode") or "") or None
    sync_remote = _read_yaml_scalar(beads_dir / "config.yaml", "sync.remote")

    return DetectOutcome(
        beads_dir=beads_dir,
        valid=bool(backend),
        backend=backend or None,
        mode=mode,
        sync_remote=sync_remote,
        issues_path=issues_path,
        reason="" if backend else "metadata.json has no `database`/`backend` field",
    )


def check_nested_init(cwd: Path) -> NestedInitCheck:
    """Walk parents looking for an ancestor Beads workspace with a ``sync.remote``.

    A nested ``bd init`` would create a second Beads database under an
    already-projected remote. Per Charter §9 and Constraint 20,
    capability record Setup integration Core row, this guard refuses to
    proceed unless the user explicitly overrides.
    """

    here = cwd.resolve()
    for parent in [here, *here.parents]:
        candidate = parent / ".beads"
        if not candidate.exists():
            continue
        remote = _read_yaml_scalar(candidate / "config.yaml", "sync.remote")
        if remote:
            return NestedInitCheck(
                ancestor_beads_dir=candidate,
                ancestor_sync_remote=remote,
                blocked=True,
                reason=(
                    f"refuse `bd init` at {here}: ancestor Beads workspace "
                    f"{candidate} already owns sync.remote={remote!r}. "
                    f"Pick a sibling directory or remove sync.remote before "
                    f"re-initialising here. Per docs/beads/capabilities.md "
                    f"Setup integration Core row, install/initialise is "
                    f"explicit, not silent."
                ),
            )
    return NestedInitCheck(
        ancestor_beads_dir=None,
        ancestor_sync_remote=None,
        blocked=False,
        reason="no ancestor Beads workspace with sync.remote detected",
    )


def install_or_initialize(
    cwd: Path,
    *,
    authority: bool = False,
    prefix: str | None = None,
    bd_binary: str = "bd",
    prompt_fn: Callable[[str], bool] | None = None,
    init_args: Sequence[str] = (),
) -> subprocess.CompletedProcess[str]:
    """Install/initialise Beads in ``cwd`` **only** with explicit user authority.

    Behaviour:

    * When authority is explicit (``authority=True`` or ``prompt_fn`` returns
      True), invoke ``bd init`` and return the completed process.
    * When authority is **not** explicit, return a failed process
      (``returncode=1``) with a refusal message on stderr. The function NEVER
      installs or initialises silently.
    * Nested-init guard runs first; if it triggers, the operation is refused
      with the guard's reason in stderr, regardless of authority.
    """

    nested = check_nested_init(cwd)
    if nested.blocked:
        proc = subprocess.run(
            ["cmd", "/c", "echo"], capture_output=True, text=True, check=False
        )
        return subprocess.CompletedProcess(
            args=[bd_binary, "init"],
            returncode=1,
            stdout="",
            stderr=nested.reason + "\n",
        )

    granted = bool(authority)
    if not granted and prompt_fn is not None:
        prompt = (
            f"No `.beads/` found at {cwd}. MBA refuses to silent-install.\n"
            f"Authorise `bd init` here? (y/N) "
        )
        granted = bool(prompt_fn(prompt))

    if not granted:
        return subprocess.CompletedProcess(
            args=[bd_binary, "init"],
            returncode=1,
            stdout="",
            stderr=(
                "refuse `bd init`: no user authority recorded. Per "
                "docs/mba/charter.md §9 rule 1 and docs/beads/capabilities.md "
                "Setup integration Core row, Beads install/initialise "
                "requires explicit user authority.\n"
            ),
        )

    cmd: list[str] = [bd_binary, "init", "--non-interactive"]
    if prefix:
        cmd.extend(["--prefix", prefix])
    cmd.extend(init_args)
    return subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=str(cwd))
