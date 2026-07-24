"""Tiny CLI dispatching every Foundation operation to the public functions.

Subcommands:

* ``preflight``           — run ``bd version`` + record evidence.
* ``detect``              — detect Beads; refuse without authority.
* ``nested-init``         — run the nested-init guard.
* ``sync-guard``          — Windows sync guard for ``bd dolt push``.
* ``workspace install``   — record the ``.mba-work/`` mode.
* ``workspace check``     — runtime privacy check.
* ``classify``            — show ATOMIC vs STAGED classification.
* ``markers install``     — install / refresh MBA RULES block.
* ``markers verify``      — confirm one BEGIN + one END per file.
* ``boundary summary``    — per-bucket file counts + overlap check.
* ``boundary classify``   — classify one or more relative paths.
* ``boundary install-content``
                          — list install-content source files + their
                            consumer-project targets.
* ``boundary check-overlap``
                          — return non-zero if any product or
                            install-content path is also excluded.
* ``init``                — opinionated **target-repo** install
                            (``mba_hgl.20``): preflight → manifest →
                            content install into ``AGENTS.md``,
                            ``CLAUDE.md``, and ``.agents/skills/mba/``.
* ``status``              — report installed MBA version + drift.
* ``upgrade``             — preview / apply a refresh from current
                            MBA source. User-edited managed blocks
                            conflict; record is refused.
* ``remove``              — preview / remove MBA-managed install
                            content while preserving Beads history and
                            `.mba-work`.
* ``bootstrap``           — opinionated internal **Foundation bootstrap**
                            (per-bead orchestrator working area, used
                            by the Foundation itself / tests; renamed
                            from the old ``init`` subcommand so the
                            user-facing namespace is uncluttered).

The CLI is intentionally script-friendly (``--json`` machine output)
and is the single executable surface the Orchestrator invokes. The
runtime that ships with ``example-005.1`` will call the underlying
functions; this CLI exists so tests and ad-hoc audits can reproduce
the same operations from a shell.
"""

from __future__ import annotations

import argparse
import json as _json
import sys
from pathlib import Path

from mba_version import __version__

from . import detect, manifest, markers, orchestrator, preflight, product_boundary, sync_guard, workspace


def _emit(obj: dict) -> None:
    print(_json.dumps(obj, indent=2, sort_keys=True))


def cmd_preflight(args: argparse.Namespace) -> int:
    orchestrator_dir = Path(args.bead_orch_dir).resolve()
    bead_dir = orchestrator_dir.parent
    result = preflight.preflight(
        bead_id=args.bead_id,
        orchestrator_dir=orchestrator_dir,
        bd_binary=args.bd,
        cwd=Path(args.cwd).resolve() if args.cwd else None,
    )
    payload = {
        "bd_version": result.bd_version,
        "raw_output": result.raw_output,
        "matches_record": result.matches_record,
        "validated_versions": list(result.validated_versions),
        "reason": result.reason,
        "ok": result.ok,
        "working_md": str(orchestrator_dir / "working.md"),
        "bead_dir": str(bead_dir),
    }
    _emit(payload)
    return 0 if result.ok else 2


def cmd_detect(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve()
    outcome = detect.detect_beads(cwd)
    payload = {
        "beads_dir": str(outcome.beads_dir),
        "valid": outcome.valid,
        "backend": outcome.backend,
        "sync_remote": outcome.sync_remote,
        "issues_path": str(outcome.issues_path) if outcome.issues_path else None,
        "reason": outcome.reason,
    }
    _emit(payload)
    return 0 if outcome.valid else 1


def cmd_nested_init(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve()
    result = detect.check_nested_init(cwd)
    payload = {
        "ancestor_beads_dir": str(result.ancestor_beads_dir) if result.ancestor_beads_dir else None,
        "ancestor_sync_remote": result.ancestor_sync_remote,
        "blocked": result.blocked,
        "reason": result.reason,
    }
    _emit(payload)
    return 1 if result.blocked else 0


def cmd_sync_guard(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve()
    decision = sync_guard.check_push_safety(cwd)
    payload = {
        "ok": decision.ok,
        "backend": decision.backend,
        "resolved_path_length": decision.resolved_path_length,
        "max_path_threshold": decision.max_path_threshold,
        "reason": decision.reason,
        "alternative": decision.alternative,
    }
    _emit(payload)
    return 0 if decision.ok else 3


def cmd_workspace_install(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve()
    state = workspace.install_mode(cwd, mode=args.mode)
    payload = {"mode": state.mode, "mode_file": str(state.mode_file)}
    _emit(payload)
    return 0


def cmd_workspace_check(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve()
    mode_state = workspace.load_mode(cwd)
    ai_ok, ai_reason = workspace.assert_ai_resource_ignored(cwd)
    payload = {
        "mode": mode_state.mode,
        "mode_file": str(mode_state.mode_file),
        "ai_resource_ignored": ai_ok,
        "ai_resource_reason": ai_reason,
    }
    _emit(payload)
    return 0 if ai_ok else 4


def cmd_classify(args: argparse.Namespace) -> int:
    classification = orchestrator.classify_request(args.request)
    selection = orchestrator.select_stages(classification, requested=None)
    payload = {
        "branch": classification.branch.value,
        "rationale": classification.rationale,
        "matched_signal": classification.matched_signal,
        "selection_stages": list(selection.stages) if selection else None,
    }
    _emit(payload)
    return 0 if classification.branch is orchestrator.Branch.ATOMIC else 1


def cmd_markers_install(args: argparse.Namespace) -> int:
    for path_str in args.file:
        path = Path(path_str).resolve()
        markers.install_block(path)
    payload = {"installed": [str(Path(p).resolve()) for p in args.file]}
    _emit(payload)
    return 0


def cmd_markers_verify(args: argparse.Namespace) -> int:
    results: dict[str, object] = {}
    rc = 0
    for path_str in args.file:
        path = Path(path_str).resolve()
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        counts = markers.count_markers(text)
        beads_ok, beads_reason = markers.assert_beads_markers_untouched(path)
        results[str(path)] = {
            "begin_count": counts.begin_count,
            "end_count": counts.end_count,
            "exactly_one_pair": counts.exactly_one_pair,
            "beads_markers_untouched": beads_ok,
            "beads_markers_reason": beads_reason,
        }
        if not counts.exactly_one_pair or not beads_ok:
            rc = 1
    _emit(results)
    return rc


def cmd_boundary(args: argparse.Namespace) -> int:
    """Surface the MBA product / install / excluded boundary.

    Subcommands:

    * ``summary`` — per-bucket file counts + an overlap check.
    * ``classify`` — classify one or more relative paths.
    * ``install-content`` — list the install-content source files
      in this repo.
    * ``check-overlap`` — return a non-zero exit code if any
      product or install-content path is also excluded.
    """

    cwd = Path(args.cwd).resolve()
    summary = product_boundary.summarize(cwd)
    overlap = product_boundary.assert_no_overlap(cwd)
    payload: dict[str, object] = {
        "summary": {
            "product": summary.product,
            "install_content": summary.install_content,
            "excluded": summary.excluded,
            "total": summary.total,
        },
        "overlap": {
            "has_overlap": overlap.has_overlap,
            "excluded_and_product": [str(p) for p in overlap.excluded_and_product],
            "excluded_and_install": [str(p) for p in overlap.excluded_and_install],
        },
    }
    if args.boundary_cmd == "summary":
        _emit(payload)
        return 0
    if args.boundary_cmd == "classify":
        classified = {
            p: product_boundary.classify_path(p, cwd)
            for p in args.path
        }
        payload["classified"] = classified
        _emit(payload)
        return 0
    if args.boundary_cmd == "install-content":
        install_paths = [
            str(p.relative_to(cwd))
            for p in product_boundary.iter_install_content_files(cwd)
        ]
        payload["install_content_sources"] = install_paths
        payload["install_content_targets"] = list(
            product_boundary.install_content_targets()
        )
        _emit(payload)
        return 0
    if args.boundary_cmd == "check-overlap":
        _emit(payload)
        return 1 if overlap.has_overlap else 0
    # Unreachable: argparse ``choices`` enforces the subcommand.
    raise AssertionError(args.boundary_cmd)


def cmd_bootstrap(args: argparse.Namespace) -> int:
    """Single-shot Foundation bootstrap.

    Order:
      1. ``bd version`` preflight.
      2. ``.mba-work/`` mode install (recording default).
      3. ``.gitignore`` reconcile (carrier + AI-resource privacy).
      4. Detect Beads (refuse install without authority).
      5. Nested-init guard.
      6. Windows sync guard for ``bd dolt push``.
      7. Install MBA RULES blocks in AGENTS.md / CLAUDE.md.

    Each step records its outcome to ``<args.bead_orch_dir>/working.md``;
    the final ``result.json`` summarises the bootstrap.

    The preflight is the gate: if the recorded ``bd`` version does not
    match the validated set, every subsequent step is short-circuited.
    """

    cwd = Path(args.cwd).resolve()
    bead_orch_dir = Path(args.bead_orch_dir).resolve()
    bead_orch_dir.mkdir(parents=True, exist_ok=True)

    log: list[dict] = []
    rc = 0

    pre = preflight.preflight(
        bead_id=args.bead_id,
        orchestrator_dir=bead_orch_dir,
        cwd=cwd,
    )
    log.append({"step": "preflight", "ok": pre.ok, "reason": pre.reason})
    if not pre.ok:
        rc = 5
        _emit({"steps": log, "rc": rc})
        return rc

    workspace.install_mode(cwd, mode=args.mode)
    reconcile_changed, reconcile_reason = workspace.reconcile_gitignore(cwd)
    log.append(
        {"step": "workspace_install", "mode": args.mode, "ok": True, "reason": ""}
    )
    log.append(
        {
            "step": "gitignore_reconcile",
            "ok": True,
            "changed": reconcile_changed,
            "reason": reconcile_reason,
        }
    )

    det = detect.detect_beads(cwd)
    log.append(
        {
            "step": "detect_beads",
            "ok": det.valid,
            "reason": det.reason,
            "backend": det.backend,
            "sync_remote": det.sync_remote,
        }
    )

    nested = detect.check_nested_init(cwd)
    log.append(
        {
            "step": "nested_init_guard",
            "blocked": nested.blocked,
            "reason": nested.reason,
        }
    )

    sg = sync_guard.check_push_safety(cwd)
    log.append(
        {
            "step": "windows_sync_guard",
            "ok": sg.ok,
            "reason": sg.reason,
            "resolved_path_length": sg.resolved_path_length,
            "max_path_threshold": sg.max_path_threshold,
        }
    )

    for path_str in args.markers_file:
        path = Path(path_str).resolve()
        markers.install_block(path)
    log.append({"step": "markers_install", "files": list(args.markers_file)})

    summary = {
        "steps": log,
        "rc": rc,
        "bead_orchestrator_dir": str(bead_orch_dir),
    }
    (bead_orch_dir / "result.json").write_text(
        _json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    _emit(summary)
    return rc


# ---------------------------------------------------------------------------
# Target-repo subcommands (``mba init`` / ``status`` / ``upgrade``)
# ---------------------------------------------------------------------------


def _resolve_root(args: argparse.Namespace) -> Path:
    """Return the resolved absolute target-repo root.

    Defaults to ``Path.cwd().resolve()`` when ``--root`` is omitted so
    ``mba init`` from inside a consumer repo Just Works. A relative
    ``--root`` is resolved against the caller's cwd at parse time
    (``Path(args.root).resolve()``); absolute paths pass through.
    """

    raw = getattr(args, "root", None) or "."
    return Path(raw).resolve()


def _bead_id_default(args: argparse.Namespace) -> str:
    """Optional ``--bead-id`` for the orchestrator working-area path.

    The Beads preflight writes its evidence to
    ``<root>/.mba-work/<bead-id>/orchestrator/working.md`` so an
    orchestrator running ``mba init`` directly can pin the Bead it is
    working against. When omitted we use ``_setup`` so setup evidence
    is clearly not a real Bead ID and workers do not try
    ``bd show`` against a placeholder.
    """

    return getattr(args, "bead_id", None) or "_setup"


def _run_preflight(cwd: Path, bead_id: str) -> preflight.PreflightResult:
    """Run the Beads-version preflight at ``cwd`` and record the evidence.

    The orchestrator_dir is pinned to ``<cwd>/.mba-work/<bead-id>/orchestrator``
    so the evidence travels with the target repo's working area. When
    the preflight refuses the manager sees ``ok=False`` and surfaces
    the reason verbatim.
    """

    orch_dir = cwd / ".mba-work" / bead_id / "orchestrator"
    return preflight.preflight(
        bead_id=bead_id,
        orchestrator_dir=orch_dir,
        cwd=cwd,
    )


def _cleanup_legacy_setup_placeholder(cwd: Path) -> bool:
    """Remove the old ``.mba-work/mba-target`` setup placeholder if safe.

    Early MBA setup used ``mba-target`` as the default setup evidence
    path. In live copy-and-use use, workers may mistake that path for a
    real Bead and try ``bd show mba-target``. Cleanup is deliberately
    narrow: remove only the exact auto-generated preflight file whose
    embedded JSON says ``bead_id == "mba-target"``, and only when no
    other files exist under that legacy directory.
    """

    legacy_root = cwd / ".mba-work" / "mba-target"
    working = legacy_root / "orchestrator" / "working.md"
    if not working.is_file():
        return False

    files = [p for p in legacy_root.rglob("*") if p.is_file()]
    if files != [working]:
        return False

    text = working.read_text(encoding="utf-8")
    if "<!-- auto-generated by mba_foundation.preflight.preflight -->" not in text:
        return False
    try:
        payload = _json.loads(text.split("```json", 1)[1].split("```", 1)[0])
    except (IndexError, _json.JSONDecodeError):
        return False
    if payload.get("bead_id") != "mba-target":
        return False

    working.unlink()
    orchestrator = working.parent
    if orchestrator.exists() and not any(orchestrator.iterdir()):
        orchestrator.rmdir()
    if legacy_root.exists() and not any(legacy_root.iterdir()):
        legacy_root.rmdir()
    return True


def _default_install_targets() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return ``(managed_block_targets, verbatim_copy_targets)`` from
    the canonical install-content surface.

    The split mirrors :func:`mba_foundation.product_boundary.
    install_content_managed_block_targets` /
    :func:`install_content_verbatim_copy_targets`. ``mba init`` and
    ``mba upgrade`` both pass the resulting tuples to
    :func:`mba_foundation.manifest.build_manifest` so the manifest
    carries every doc/asset referenced by the installed MBA RULES
    block or skill.
    """

    from .product_boundary import (
        install_content_managed_block_targets,
        install_content_verbatim_copy_targets,
    )

    return (
        install_content_managed_block_targets(),
        install_content_verbatim_copy_targets(),
    )


def cmd_mba_init(args: argparse.Namespace) -> int:
    """``mba init`` — opinionated target-repo install.

    Order:
      1. Resolve target ``--root`` (cwd default).
      2. Run ``bd version`` preflight; refuse on mismatch (rc=5).
      3. Build an install manifest from the current upstream MBA,
         including every doc/asset referenced by the installed MBA
         RULES block or skill.
      4. Apply the install content (``AGENTS.md``, ``CLAUDE.md``,
         ``docs/mba/charter.md``, ``docs/beads/capabilities.md``,
         ``docs/mba/charter.md``, ``.agents/skills/mba/SKILL.md``).
      5. Write ``<root>/.mba/manifest.json`` (atomic).
      6. Print JSON summary; exit 0 on success, 4 on dry-run, 5 on
         preflight refusal.
    """

    root = _resolve_root(args)
    bead_id = _bead_id_default(args)
    pre = _run_preflight(root, bead_id=bead_id)
    if not pre.ok:
        payload = {
            "ok": False,
            "reason": pre.reason,
            "bd_version": pre.bd_version,
            "validated_versions": list(pre.validated_versions),
            "root": str(root),
            "stage": "preflight",
        }
        _emit(payload)
        return 5
    legacy_setup_cleaned = False
    if getattr(args, "bead_id", None) is None:
        legacy_setup_cleaned = _cleanup_legacy_setup_placeholder(root)

    managed_blocks, verbatim_copies = _default_install_targets()
    upstream = manifest.build_manifest(
        source=manifest.SOURCE_PACKAGED,
        preflight_evidence=manifest.PreflightEvidence(
            bd_version=pre.bd_version,
            matches_record=pre.matches_record,
            raw_output=pre.raw_output,
        ),
        managed_block_targets=managed_blocks,
        verbatim_copy_targets=verbatim_copies,
    )

    if args.dry_run:
        # Dry-run: report what the install would do without writing.
        plan = manifest.plan_upgrade(root, installed=None, upstream=upstream)
        payload = {
            "ok": True,
            "dry_run": True,
            "root": str(root),
            "upstream_version": upstream.mba_version,
            "plan": manifest.plan_entries_to_rows(plan.entries),
        }
        _emit(payload)
        return 4

    plan = manifest.plan_upgrade(root, installed=None, upstream=upstream)
    applied_plan = manifest.apply_upgrade(
        root=root,
        installed=None,
        upstream=upstream,
        plan=plan,
        dry_run=False,
    )

    # Install the workspace mode record locally so a downstream
    # `mba-foundation workspace check` agrees with the new install.
    # The default is ``local``; a future ``mba init --mode shared``
    # flag can flip it. Kept idempotent.
    workspace.ensure_workspace_setup(root)

    payload = {
        "ok": True,
        "dry_run": False,
        "root": str(root),
        "installed_version": upstream.mba_version,
        "manifest_path": str(manifest.manifest_path(root)),
        "legacy_setup_placeholder_removed": legacy_setup_cleaned,
        "plan": manifest.plan_entries_to_rows(applied_plan.entries),
    }
    _emit(payload)
    return 0


def cmd_mba_status(args: argparse.Namespace) -> int:
    """``mba status`` — report installed version + drift.

    No state is written. Reads ``<root>/.mba/manifest.json`` (if any)
    and compares each managed file's on-disk state. Exits 0 for
    "installed, no drift", 1 for "not installed", 2 for
    "user-edited block (conflict)", 3 for "drift" other than user
    edits.
    """

    root = _resolve_root(args)
    installed = manifest.read_manifest(root)
    drift = manifest.detect_drift(root, installed)
    summary = manifest.build_status_summary(root, drift)

    _emit(summary.to_dict())

    if not summary.installed:
        return 1
    if summary.has_conflicts:
        return 2
    if summary.has_drift:
        return 3
    return 0


def cmd_mba_upgrade(args: argparse.Namespace) -> int:
    """``mba upgrade`` — preview or apply an MBA-content refresh.

    Order:
      1. Resolve ``--root`` (cwd default).
      2. Run ``bd version`` preflight; refuse on mismatch (rc=5).
      3. Read installed manifest (None ⇒ "not installed", refused
         with rc=6 because the operator clearly intends an install).
      4. Build the upstream manifest.
      5. Plan upgrade (drift + conflict detection).
      6. ``--dry-run`` → print plan only (rc=4).
      7. Apply upgrade: ``install`` / ``replace`` are safe; any
         ``conflict`` triggers ``ManifestConflictError`` (rc=7).
    """

    root = _resolve_root(args)
    bead_id = _bead_id_default(args)
    pre = _run_preflight(root, bead_id=bead_id)
    if not pre.ok:
        payload = {
            "ok": False,
            "reason": pre.reason,
            "bd_version": pre.bd_version,
            "validated_versions": list(pre.validated_versions),
            "root": str(root),
            "stage": "preflight",
        }
        _emit(payload)
        return 5
    legacy_setup_cleaned = False
    if getattr(args, "bead_id", None) is None:
        legacy_setup_cleaned = _cleanup_legacy_setup_placeholder(root)

    installed = manifest.read_manifest(root)
    if installed is None:
        # Refuse: upgrade requires an existing install. The operator
        # almost certainly means ``mba init`` (the answer is hinted).
        payload = {
            "ok": False,
            "reason": (
                f"no .mba/manifest.json at {root}; mba upgrade requires an "
                f"existing install. Run `mba init` first."
            ),
            "root": str(root),
            "stage": "not_installed",
        }
        _emit(payload)
        return 6

    managed_blocks, verbatim_copies = _default_install_targets()
    upstream = manifest.build_manifest(
        source=manifest.SOURCE_PACKAGED,
        preflight_evidence=manifest.PreflightEvidence(
            bd_version=pre.bd_version,
            matches_record=pre.matches_record,
            raw_output=pre.raw_output,
        ),
        managed_block_targets=managed_blocks,
        verbatim_copy_targets=verbatim_copies,
    )

    drift = manifest.detect_drift(root, installed)
    plan = manifest.plan_upgrade(root, installed, upstream, drift=drift)

    if args.dry_run:
        # Dry-run: report the plan and exit without writing. Conflicts
        # are surfaced as plan rows; the manager can preview before
        # deciding to apply.
        payload = {
            "ok": True,
            "dry_run": True,
            "root": str(root),
            "installed_version": installed.mba_version,
            "upstream_version": upstream.mba_version,
            "upgrade_available": installed.mba_version != upstream.mba_version,
            "has_conflicts": plan.has_conflicts,
            "conflict_paths": list(plan.conflict_paths),
            "is_noop": plan.is_noop,
            "legacy_setup_placeholder_removed": legacy_setup_cleaned,
            "plan": manifest.plan_entries_to_rows(plan.entries),
        }
        _emit(payload)
        return 4 if not plan.has_conflicts else 7

    # Apply (raises ManifestConflictError on user-edited blocks).
    try:
        applied = manifest.apply_upgrade(
            root=root,
            installed=installed,
            upstream=upstream,
            plan=plan,
            dry_run=False,
        )
    except manifest.ManifestConflictError as exc:
        payload = {
            "ok": False,
            "reason": str(exc),
            "root": str(root),
            "stage": "apply_upgrade",
            "has_conflicts": plan.has_conflicts,
            "conflict_paths": list(plan.conflict_paths),
            "plan": manifest.plan_entries_to_rows(plan.entries),
        }
        _emit(payload)
        return 7

    payload = {
        "ok": True,
        "dry_run": False,
        "root": str(root),
        "installed_version": installed.mba_version,
        "upstream_version": upstream.mba_version,
        "has_conflicts": False,
        "manifest_path": str(manifest.manifest_path(root)),
        "legacy_setup_placeholder_removed": legacy_setup_cleaned,
        "plan": manifest.plan_entries_to_rows(applied.entries),
    }
    _emit(payload)
    return 0


def _remove_plan_rows(plan: manifest.RemovePlan) -> list[dict[str, object]]:
    """Return JSON-serialisable rows for ``mba remove``."""

    return [
        {
            "relpath": entry.relpath,
            "kind": entry.kind,
            "state": entry.state,
            "action": entry.action,
            "reason": entry.reason,
        }
        for entry in plan.entries
    ]


def cmd_mba_remove(args: argparse.Namespace) -> int:
    """``mba remove`` — preview or remove MBA-managed install content.

    This removes only the manifest-managed MBA install surface: MBA
    RULES blocks, copied docs/skill files, and ``.mba/manifest.json``.
    It deliberately preserves ``.beads`` and ``.mba-work`` because they
    hold task history, local configuration, prompts, transcripts,
    evidence and user choices.
    """

    root = _resolve_root(args)
    installed = manifest.read_manifest(root)
    if installed is None:
        payload = {
            "ok": False,
            "reason": (
                f"no .mba/manifest.json at {root}; MBA managed content "
                "is not installed or the manifest was already removed."
            ),
            "root": str(root),
            "stage": "not_installed",
        }
        _emit(payload)
        return 6

    drift = manifest.detect_drift(root, installed)
    plan = manifest.plan_remove(root, installed, drift=drift, force=bool(args.force))
    if args.dry_run:
        payload = {
            "ok": True,
            "dry_run": True,
            "root": str(root),
            "has_conflicts": plan.has_conflicts,
            "conflict_paths": list(plan.conflict_paths),
            "preserves": list(plan.preserves),
            "plan": _remove_plan_rows(plan),
        }
        _emit(payload)
        return 4 if not plan.has_conflicts else 7

    try:
        applied = manifest.apply_remove(
            root=root,
            installed=installed,
            plan=plan,
            dry_run=False,
            force=bool(args.force),
        )
    except manifest.ManifestConflictError as exc:
        payload = {
            "ok": False,
            "reason": str(exc),
            "root": str(root),
            "stage": "apply_remove",
            "has_conflicts": plan.has_conflicts,
            "conflict_paths": list(plan.conflict_paths),
            "preserves": list(plan.preserves),
            "plan": _remove_plan_rows(plan),
        }
        _emit(payload)
        return 7

    payload = {
        "ok": True,
        "dry_run": False,
        "root": str(root),
        "manifest_removed": True,
        "preserves": list(applied.preserves),
        "plan": _remove_plan_rows(applied),
    }
    _emit(payload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mba-foundation")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("preflight", help="`bd version` preflight + capability check.")
    p.add_argument("--bead-id", required=True)
    p.add_argument("--bead-orch-dir", required=True)
    p.add_argument("--cwd", default=".")
    p.add_argument("--bd", default="bd")
    p.set_defaults(func=cmd_preflight)

    p = sub.add_parser("detect", help="Detect Beads at CWD.")
    p.add_argument("--cwd", default=".")
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("nested-init", help="Run the nested-init guard at CWD.")
    p.add_argument("--cwd", default=".")
    p.set_defaults(func=cmd_nested_init)

    p = sub.add_parser("sync-guard", help="Windows sync guard for `bd dolt push`.")
    p.add_argument("--cwd", default=".")
    p.set_defaults(func=cmd_sync_guard)

    p = sub.add_parser("workspace")
    sub_ws = p.add_subparsers(dest="ws_cmd", required=True)
    pw = sub_ws.add_parser("install", help="Record the `.mba-work/` mode.")
    pw.add_argument("--cwd", default=".")
    pw.add_argument("--mode", default="local", choices=["local", "shared"])
    pw.set_defaults(func=cmd_workspace_install)
    pw = sub_ws.add_parser("check", help="Runtime privacy check (mode + AI-resource ignore).")
    pw.add_argument("--cwd", default=".")
    pw.set_defaults(func=cmd_workspace_check)

    p = sub.add_parser("classify", help="Classify a request as ATOMIC vs STAGED.")
    p.add_argument("request")
    p.set_defaults(func=cmd_classify)

    p = sub.add_parser("markers")
    sub_mk = p.add_subparsers(dest="mk_cmd", required=True)
    pm = sub_mk.add_parser("install", help="Install MBA RULES block in each file.")
    pm.add_argument("--file", action="append", required=True, dest="file")
    pm.set_defaults(func=cmd_markers_install)
    pm = sub_mk.add_parser("verify", help="Verify exactly one BEGIN + one END per file.")
    pm.add_argument("--file", action="append", required=True, dest="file")
    pm.set_defaults(func=cmd_markers_verify)

    p = sub.add_parser(
        "boundary",
        help="Surface the MBA product / install-content / excluded boundary.",
    )
    sub_bd = p.add_subparsers(dest="boundary_cmd", required=True)
    pb = sub_bd.add_parser("summary", help="Per-bucket file counts + overlap check.")
    pb.add_argument("--cwd", default=".")
    pb.set_defaults(func=cmd_boundary)
    pb = sub_bd.add_parser("classify", help="Classify one or more relative paths.")
    pb.add_argument("--cwd", default=".")
    pb.add_argument("path", nargs="+", help="POSIX-style relative paths to classify.")
    pb.set_defaults(func=cmd_boundary)
    pb = sub_bd.add_parser(
        "install-content",
        help="List install-content source files + their consumer-project targets.",
    )
    pb.add_argument("--cwd", default=".")
    pb.set_defaults(func=cmd_boundary)
    pb = sub_bd.add_parser(
        "check-overlap",
        help="Return non-zero if any product or install-content path is also excluded.",
    )
    pb.add_argument("--cwd", default=".")
    pb.set_defaults(func=cmd_boundary)

    p = sub.add_parser(
        "init",
        help="Initialize MBA in a target repo (writes .mba/manifest.json + content).",
    )
    p.add_argument(
        "--root", default=".", help="Target repo root (default: cwd)."
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan the install without writing files.",
    )
    p.add_argument(
        "--bead-id",
        default=None,
        help="Optional Bead ID for the orchestrator working area (default: _setup).",
    )
    p.set_defaults(func=cmd_mba_init)

    p = sub.add_parser(
        "status",
        help="Report installed MBA version, drift, and user-edited managed blocks.",
    )
    p.add_argument(
        "--root", default=".", help="Target repo root (default: cwd)."
    )
    p.set_defaults(func=cmd_mba_status)

    p = sub.add_parser(
        "upgrade",
        help="Preview (--dry-run) or apply an MBA-content upgrade from current upstream.",
    )
    p.add_argument(
        "--root", default=".", help="Target repo root (default: cwd)."
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the upgrade plan without writing files.",
    )
    p.add_argument(
        "--bead-id",
        default=None,
        help="Optional Bead ID for the orchestrator working area (default: _setup).",
    )
    p.set_defaults(func=cmd_mba_upgrade)

    p = sub.add_parser(
        "remove",
        help="Preview (--dry-run) or remove MBA-managed install content.",
    )
    p.add_argument(
        "--root", default=".", help="Target repo root (default: cwd)."
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the removal plan without writing files.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help=(
            "Remove user-edited MBA-managed content too. Review "
            "`mba remove --dry-run` first; .beads and .mba-work are still "
            "preserved."
        ),
    )
    p.set_defaults(func=cmd_mba_remove)

    p = sub.add_parser(
        "bootstrap",
        help="Single-shot Foundation bootstrap (per-bead orchestrator working area).",
    )
    p.add_argument("--bead-id", required=True)
    p.add_argument("--bead-orch-dir", required=True)
    p.add_argument("--cwd", default=".")
    p.add_argument("--mode", default="local", choices=["local", "shared"])
    p.add_argument("--markers-file", action="append", default=["AGENTS.md", "CLAUDE.md"])
    p.set_defaults(func=cmd_bootstrap)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:  # pragma: no cover - top-level guard
        print(f"mba-foundation: error: {exc}", file=sys.stderr)
        return 99


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
