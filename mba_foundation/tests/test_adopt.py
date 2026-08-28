"""``mba adopt`` — fail-closed adopt-existing behaviour tests.

These tests pin the acceptance rows of the adopt-existing issue
(AI-Choices-Atlas-7t6 in the first consumer's tracker):

* A repo that already tracks byte-identical managed MBA content (a
  fresh clone of a publishing consumer) adopts cleanly: only the
  private ``.mba/manifest.json`` (plus explicitly selected private
  OpenCode launch files) is created.
* Any mismatched byte or block refuses the WHOLE adoption with no
  writes (all-or-nothing).
* Absent private OpenCode launch files follow the explicit
  ``--opencode`` choice: ``omit`` (default) or ``create``.
* Adoption is idempotent, and ``mba status`` reports the adopted repo
  as installed with no drift and no conflicts.

The module-level tests are hermetic (pinned source texts, no shell
out). The CLI integration tests at the bottom run the real
``python -m mba_foundation`` surface — like the ``mba init`` CLI
tests they rely on the dev host's validated ``bd`` for the preflight;
the preflight refusal path is exercised in-process via monkeypatch.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from mba_foundation import adopt, cli, manifest
from mba_foundation.adopt import (
    ACTION_ADOPT,
    ACTION_CREATE,
    ACTION_MISMATCH,
    ACTION_OMIT,
    OPENCODE_CHOICE_CREATE,
    OPENCODE_CHOICE_OMIT,
    OPENCODE_LAUNCH_TARGETS,
    apply_adopt,
    plan_adopt,
)
from mba_foundation.manifest import (
    ACTION_CONFLICT,
    ACTION_SKIP,
    ManifestConflictError,
    apply_upgrade,
    build_manifest,
    build_status_summary,
    detect_drift,
    plan_upgrade,
    read_manifest,
    write_manifest,
)
from mba_foundation.markers import (
    MBA_RULES_BEGIN_MARKER,
    MBA_RULES_END_MARKER,
    MBA_RULES_BLOCK,
    install_block,
)
from mba_foundation.product_boundary import (
    install_content_managed_block_targets,
    install_content_targets,
    install_content_verbatim_copy_targets,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Helpers — a pinned upstream + an on-disk "publishing consumer clone".
# ---------------------------------------------------------------------------


_PINNED_VERBATIM_SOURCES: dict[str, str] = {
    "docs/mba/charter.md": "# Charter (test fixture)\n\nNormative text.\n",
    "docs/beads/capabilities.md": "# Capabilities (test fixture)\n\nbd 1.0.4.\n",
    ".agents/skills/mba/SKILL.md": "# MBA skill (test fixture)\n",
    "opencode.json": '{\n  "default_agent": "mba"\n}\n',
    ".opencode/agents/mba.md": "---\ndescription: MBA Orchestrator\n---\n",
    ".opencode/agents/mba-worker.md": "---\ndescription: MBA worker\n---\n",
}

#: The targets a publishing consumer tracks (everything except the
#: private OpenCode launch files).
_TRACKED_VERBATIM_TARGETS: tuple[str, ...] = tuple(
    relpath
    for relpath in install_content_verbatim_copy_targets()
    if relpath not in OPENCODE_LAUNCH_TARGETS
)


def _pinned_upstream(
    *,
    mba_version: str | None = None,
    overrides: dict[str, str] | None = None,
) -> manifest.Manifest:
    """Build the packaged upstream manifest with deterministic sources.

    ``installed_at`` is pinned so two builds compare equal (the
    plan-binding check in :func:`apply_adopt` compares whole
    manifests). ``mba_version`` / ``overrides`` let upgrade tests
    model a newer upstream with changed content.
    """

    sources = dict(_PINNED_VERBATIM_SOURCES)
    if overrides:
        sources.update(overrides)
    return build_manifest(
        preflight_evidence=manifest.PreflightEvidence(
            bd_version="1.0.4",
            matches_record=True,
            raw_output="bd version 1.0.4 (test fixture)",
        ),
        installed_at="2026-08-28T00:00:00+00:00",
        mba_version=mba_version,
        managed_block_targets=install_content_managed_block_targets(),
        verbatim_copy_targets=install_content_verbatim_copy_targets(),
        source_block_body=MBA_RULES_BLOCK,
        verbatim_copy_sources=sources,
    )


def _force_lf(path: Path) -> None:
    """Rewrite ``path`` with LF newlines.

    ``install_block`` writes with the platform's newline translation
    (CRLF on Windows), but the scenario these fixtures model is a *git
    checkout of a publishing consumer*, and both this repo's and the
    reference consumer's ``.gitattributes`` pin ``eol=lf`` for managed
    content — an adoptable clone presents LF bytes on every platform.
    """

    text = path.read_text(encoding="utf-8")
    path.write_text(text, encoding="utf-8", newline="\n")


def _populate_clone(root: Path, *, include_opencode: bool = False) -> None:
    """Write the tracked content of a publishing consumer clone.

    Managed blocks land via the real installer (then pinned to LF, as
    a real ``eol=lf`` checkout presents them); tracked verbatim
    targets get the pinned sources. OpenCode launch files are absent
    unless ``include_opencode`` — the typical publisher keeps them
    private/untracked.
    """

    for relpath in install_content_managed_block_targets():
        target = root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        install_block(target)
        _force_lf(target)
    targets = list(_TRACKED_VERBATIM_TARGETS)
    if include_opencode:
        targets += list(OPENCODE_LAUNCH_TARGETS)
    for relpath in targets:
        target = root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            _PINNED_VERBATIM_SOURCES[relpath], encoding="utf-8", newline="\n"
        )


def _snapshot(root: Path) -> dict[str, bytes]:
    """Byte snapshot of every file under ``root`` (for no-write proofs)."""

    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ---------------------------------------------------------------------------
# Surface consistency
# ---------------------------------------------------------------------------


def test_launch_targets_are_canonical_verbatim_subset() -> None:
    """The launch-file trio must stay in lock-step with the canonical
    install surface, or the ``opencode`` choice silently stops
    covering a launch file."""

    verbatim = set(install_content_verbatim_copy_targets())
    assert set(OPENCODE_LAUNCH_TARGETS) <= verbatim
    assert set(OPENCODE_LAUNCH_TARGETS) <= set(install_content_targets())


# ---------------------------------------------------------------------------
# Identical-content adoption
# ---------------------------------------------------------------------------


def test_plan_adopt_identical_content_all_adopt_and_no_writes(tmp_path: Path) -> None:
    _populate_clone(tmp_path)
    before = _snapshot(tmp_path)

    plan = plan_adopt(tmp_path, _pinned_upstream())

    assert plan.ok
    assert not plan.has_mismatches
    assert set(plan.adopted_paths) == set(
        install_content_managed_block_targets()
    ) | set(_TRACKED_VERBATIM_TARGETS)
    assert set(plan.omitted_paths) == set(OPENCODE_LAUNCH_TARGETS)
    # Planning is pure — nothing on disk moved.
    assert _snapshot(tmp_path) == before
    assert not (tmp_path / ".mba").exists()


def test_apply_adopt_creates_only_the_manifest(tmp_path: Path) -> None:
    _populate_clone(tmp_path)
    before = _snapshot(tmp_path)

    apply_adopt(tmp_path, _pinned_upstream())

    after = _snapshot(tmp_path)
    created = set(after) - set(before)
    assert created == {str(Path(".mba") / "manifest.json")}
    # Every pre-existing tracked file is byte-for-byte untouched.
    assert all(after[path] == before[path] for path in before)

    installed = read_manifest(tmp_path)
    assert installed is not None
    recorded = {entry.relpath for entry in installed.files}
    assert recorded == set(install_content_managed_block_targets()) | set(
        _TRACKED_VERBATIM_TARGETS
    )
    # Omitted launch files are neither created nor recorded as files —
    # they are recorded as durable omissions instead.
    for relpath in OPENCODE_LAUNCH_TARGETS:
        assert not (tmp_path / relpath).exists()
        assert relpath not in recorded
    assert set(installed.omitted) == set(OPENCODE_LAUNCH_TARGETS)


def test_adopted_repo_reports_status_unchanged(tmp_path: Path) -> None:
    """Acceptance: ``mba status`` on an adopted repo is installed,
    drift-free, conflict-free."""

    _populate_clone(tmp_path)
    upstream = _pinned_upstream()
    apply_adopt(tmp_path, upstream)

    drift = detect_drift(tmp_path, read_manifest(tmp_path))
    assert drift.is_installed
    assert not drift.has_drift
    assert not drift.has_conflicts

    summary = build_status_summary(
        tmp_path, drift, upstream_version=upstream.mba_version
    )
    assert summary.installed is True
    assert summary.has_drift is False
    assert summary.has_conflicts is False
    assert summary.upgrade_available is False


def test_apply_adopt_is_idempotent(tmp_path: Path) -> None:
    _populate_clone(tmp_path)
    upstream = _pinned_upstream()
    apply_adopt(tmp_path, upstream)
    manifest_bytes = (tmp_path / ".mba" / "manifest.json").read_bytes()
    before = _snapshot(tmp_path)

    second = apply_adopt(tmp_path, upstream)

    assert second.already_installed is True
    assert _snapshot(tmp_path) == before
    assert (tmp_path / ".mba" / "manifest.json").read_bytes() == manifest_bytes


# ---------------------------------------------------------------------------
# All-or-nothing mismatch refusal
# ---------------------------------------------------------------------------


def _assert_refused_with_no_writes(
    tmp_path: Path, before: dict[str, bytes], *, opencode: str = OPENCODE_CHOICE_CREATE
) -> None:
    """Apply must raise and leave the tree byte-identical.

    ``opencode=create`` on purpose: even a selected launch-file
    creation must not happen when any other target mismatches.
    """

    with pytest.raises(ManifestConflictError):
        apply_adopt(tmp_path, _pinned_upstream(), opencode=opencode)
    assert _snapshot(tmp_path) == before
    assert not (tmp_path / ".mba").exists()
    for relpath in OPENCODE_LAUNCH_TARGETS:
        assert not (tmp_path / relpath).exists()


def test_apply_adopt_refuses_mismatched_verbatim_with_no_writes(
    tmp_path: Path,
) -> None:
    _populate_clone(tmp_path)
    charter = tmp_path / "docs" / "mba" / "charter.md"
    charter.write_text(
        charter.read_text(encoding="utf-8") + "local drift\n",
        encoding="utf-8",
        newline="\n",
    )
    before = _snapshot(tmp_path)

    plan = plan_adopt(tmp_path, _pinned_upstream())
    assert plan.mismatch_paths == ("docs/mba/charter.md",)
    _assert_refused_with_no_writes(tmp_path, before)


def test_apply_adopt_refuses_edited_block_with_no_writes(tmp_path: Path) -> None:
    _populate_clone(tmp_path)
    claude = tmp_path / "CLAUDE.md"
    text = claude.read_text(encoding="utf-8")
    claude.write_text(
        text.replace("## MBA Foundation Rules", "## MBA Foundation Rules (edited)"),
        encoding="utf-8",
        newline="\n",
    )
    before = _snapshot(tmp_path)

    plan = plan_adopt(tmp_path, _pinned_upstream())
    assert plan.mismatch_paths == ("CLAUDE.md",)
    _assert_refused_with_no_writes(tmp_path, before)


def test_apply_adopt_refuses_missing_tracked_target(tmp_path: Path) -> None:
    _populate_clone(tmp_path)
    (tmp_path / "docs" / "beads" / "capabilities.md").unlink()
    before = _snapshot(tmp_path)

    plan = plan_adopt(tmp_path, _pinned_upstream())
    assert plan.mismatch_paths == ("docs/beads/capabilities.md",)
    _assert_refused_with_no_writes(tmp_path, before)


def test_apply_adopt_refuses_second_marker_pair(tmp_path: Path) -> None:
    _populate_clone(tmp_path)
    agents = tmp_path / "AGENTS.md"
    agents.write_text(
        agents.read_text(encoding="utf-8")
        + f"\n{MBA_RULES_BEGIN_MARKER}\nstray\n{MBA_RULES_END_MARKER}\n",
        encoding="utf-8",
        newline="\n",
    )
    before = _snapshot(tmp_path)

    plan = plan_adopt(tmp_path, _pinned_upstream())
    assert "AGENTS.md" in plan.mismatch_paths
    entry = next(e for e in plan.entries if e.relpath == "AGENTS.md")
    assert "exactly one" in entry.reason
    _assert_refused_with_no_writes(tmp_path, before)


def test_apply_adopt_dry_run_never_writes_even_on_mismatch(tmp_path: Path) -> None:
    _populate_clone(tmp_path)
    (tmp_path / ".agents" / "skills" / "mba" / "SKILL.md").write_text(
        "edited skill\n", encoding="utf-8", newline="\n"
    )
    before = _snapshot(tmp_path)

    plan = apply_adopt(
        tmp_path, _pinned_upstream(), opencode=OPENCODE_CHOICE_CREATE, dry_run=True
    )

    assert plan.has_mismatches
    assert _snapshot(tmp_path) == before
    assert not (tmp_path / ".mba").exists()


# ---------------------------------------------------------------------------
# OpenCode launch-file choice
# ---------------------------------------------------------------------------


def test_opencode_create_writes_byte_identical_launch_files(tmp_path: Path) -> None:
    _populate_clone(tmp_path)

    plan = apply_adopt(tmp_path, _pinned_upstream(), opencode=OPENCODE_CHOICE_CREATE)

    assert set(plan.created_paths) == set(OPENCODE_LAUNCH_TARGETS)
    for relpath in OPENCODE_LAUNCH_TARGETS:
        assert (tmp_path / relpath).read_text(encoding="utf-8") == (
            _PINNED_VERBATIM_SOURCES[relpath]
        )
    installed = read_manifest(tmp_path)
    recorded = {entry.relpath for entry in installed.files}
    assert set(OPENCODE_LAUNCH_TARGETS) <= recorded
    # And the created state is immediately drift-free.
    assert not detect_drift(tmp_path, installed).has_drift


def test_opencode_present_identical_is_adopted_even_when_omitting(
    tmp_path: Path,
) -> None:
    _populate_clone(tmp_path)
    config = tmp_path / "opencode.json"
    config.write_text(
        _PINNED_VERBATIM_SOURCES["opencode.json"], encoding="utf-8", newline="\n"
    )

    plan = apply_adopt(tmp_path, _pinned_upstream(), opencode=OPENCODE_CHOICE_OMIT)

    assert "opencode.json" in plan.adopted_paths
    assert set(plan.omitted_paths) == {
        ".opencode/agents/mba.md",
        ".opencode/agents/mba-worker.md",
    }
    recorded = {entry.relpath for entry in read_manifest(tmp_path).files}
    assert "opencode.json" in recorded
    assert ".opencode/agents/mba.md" not in recorded


def test_opencode_present_mismatched_refuses(tmp_path: Path) -> None:
    _populate_clone(tmp_path)
    (tmp_path / "opencode.json").write_text(
        '{\n  "default_agent": "other"\n}\n', encoding="utf-8", newline="\n"
    )
    before = _snapshot(tmp_path)

    plan = plan_adopt(tmp_path, _pinned_upstream(), opencode=OPENCODE_CHOICE_OMIT)
    assert plan.mismatch_paths == ("opencode.json",)
    with pytest.raises(ManifestConflictError):
        apply_adopt(tmp_path, _pinned_upstream(), opencode=OPENCODE_CHOICE_OMIT)
    assert _snapshot(tmp_path) == before
    assert not (tmp_path / ".mba").exists()


def test_plan_adopt_rejects_invalid_opencode_choice(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        plan_adopt(tmp_path, _pinned_upstream(), opencode="maybe")


# ---------------------------------------------------------------------------
# Existing-manifest handling
# ---------------------------------------------------------------------------


def test_plan_adopt_refuses_existing_manifest_with_drift(tmp_path: Path) -> None:
    _populate_clone(tmp_path)
    upstream = _pinned_upstream()
    apply_adopt(tmp_path, upstream)
    manifest_bytes = (tmp_path / ".mba" / "manifest.json").read_bytes()
    charter = tmp_path / "docs" / "mba" / "charter.md"
    charter.write_text("post-install edit\n", encoding="utf-8", newline="\n")

    plan = plan_adopt(tmp_path, upstream)
    assert plan.blocking_reason
    assert not plan.ok
    with pytest.raises(ManifestConflictError):
        apply_adopt(tmp_path, upstream)
    # The existing install record is untouched by the refusal.
    assert (tmp_path / ".mba" / "manifest.json").read_bytes() == manifest_bytes


def test_plan_adopt_refuses_corrupt_manifest(tmp_path: Path) -> None:
    _populate_clone(tmp_path)
    (tmp_path / ".mba").mkdir()
    (tmp_path / ".mba" / "manifest.json").write_text(
        "not json", encoding="utf-8", newline="\n"
    )

    plan = plan_adopt(tmp_path, _pinned_upstream())
    assert "unreadable" in plan.blocking_reason
    with pytest.raises(ManifestConflictError):
        apply_adopt(tmp_path, _pinned_upstream())


# ---------------------------------------------------------------------------
# Stale plans (round-2 finding 1) — apply revalidates from disk and
# refuses any post-plan divergence with zero writes.
# ---------------------------------------------------------------------------


def test_apply_adopt_refuses_stale_tracked_plan(tmp_path: Path) -> None:
    """The retained audit probe: a tracked file edited after planning
    must refuse — never produce a manifest whose drift is immediately
    true."""

    _populate_clone(tmp_path)
    upstream = _pinned_upstream()
    plan = plan_adopt(tmp_path, upstream)
    (tmp_path / "docs" / "mba" / "charter.md").write_text(
        "changed after plan\n", encoding="utf-8", newline="\n"
    )
    before = _snapshot(tmp_path)

    with pytest.raises(ManifestConflictError):
        apply_adopt(tmp_path, upstream, plan=plan)

    assert _snapshot(tmp_path) == before
    assert not (tmp_path / ".mba").exists()


def test_apply_adopt_refuses_stale_create_plan_preserves_user_file(
    tmp_path: Path,
) -> None:
    """The second audit probe: a user launch file created after an
    ``opencode=create`` plan must survive untouched."""

    _populate_clone(tmp_path)
    upstream = _pinned_upstream()
    plan = plan_adopt(tmp_path, upstream, opencode=OPENCODE_CHOICE_CREATE)
    launch = tmp_path / "opencode.json"
    launch.write_text(
        "user content created after plan\n", encoding="utf-8", newline="\n"
    )
    before = _snapshot(tmp_path)

    with pytest.raises(ManifestConflictError):
        apply_adopt(
            tmp_path, upstream, plan=plan, opencode=OPENCODE_CHOICE_CREATE
        )

    assert _snapshot(tmp_path) == before
    assert launch.read_text(encoding="utf-8") == "user content created after plan\n"
    assert not (tmp_path / ".mba").exists()


def test_apply_adopt_refuses_benign_post_plan_divergence(tmp_path: Path) -> None:
    """Even a divergence that would itself be adoptable (an identical
    launch file appearing after an ``omit`` plan) refuses: the caller's
    plan no longer describes the disk, so nothing is written."""

    _populate_clone(tmp_path)
    upstream = _pinned_upstream()
    plan = plan_adopt(tmp_path, upstream)
    (tmp_path / "opencode.json").write_text(
        _PINNED_VERBATIM_SOURCES["opencode.json"], encoding="utf-8", newline="\n"
    )
    before = _snapshot(tmp_path)

    with pytest.raises(ManifestConflictError):
        apply_adopt(tmp_path, upstream, plan=plan)

    assert _snapshot(tmp_path) == before
    assert not (tmp_path / ".mba").exists()


def test_apply_adopt_binds_plan_to_root_upstream_and_choice(tmp_path: Path) -> None:
    _populate_clone(tmp_path)
    upstream = _pinned_upstream()
    plan = plan_adopt(tmp_path, upstream)

    other_root = tmp_path / "elsewhere"
    other_root.mkdir()
    with pytest.raises(ManifestConflictError):
        apply_adopt(other_root, upstream, plan=plan)

    other_upstream = _pinned_upstream(mba_version="9.9.9-test")
    with pytest.raises(ManifestConflictError):
        apply_adopt(tmp_path, other_upstream, plan=plan)

    with pytest.raises(ManifestConflictError):
        apply_adopt(tmp_path, upstream, plan=plan, opencode=OPENCODE_CHOICE_CREATE)

    assert not (tmp_path / ".mba").exists()
    assert not (other_root / ".mba").exists()


def test_create_write_is_exclusive(tmp_path: Path) -> None:
    """The final belt: the launch-file write itself refuses atomically
    when a file exists at the target path."""

    upstream = _pinned_upstream()
    target = tmp_path / "opencode.json"
    target.write_text("user\n", encoding="utf-8", newline="\n")

    with pytest.raises(ManifestConflictError):
        adopt._create_new_launch_file(target, upstream, "opencode.json")

    assert target.read_text(encoding="utf-8") == "user\n"


def _inject_on_nth_create(
    monkeypatch: pytest.MonkeyPatch, n: int, side_effect
) -> None:
    """Run ``side_effect(target)`` immediately before the n-th
    launch-file creation, then continue with the real create —
    propagating its returned identity token, as any well-behaved
    wrapper must (a swallowed token makes the file preserve-only)."""

    original = adopt._create_new_launch_file
    calls = {"count": 0}

    def wrapper(target: Path, upstream_obj, relpath: str):
        calls["count"] += 1
        if calls["count"] == n:
            side_effect(target)
        return original(target, upstream_obj, relpath)

    monkeypatch.setattr(adopt, "_create_new_launch_file", wrapper)


def test_apply_adopt_rolls_back_created_files_on_late_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retained round-2 audit probe: a user file appearing at the
    SECOND create target refuses — and the first launch file this
    invocation created is rolled back, so the pre-call snapshot is
    unchanged except for the concurrent writer's own file."""

    _populate_clone(tmp_path)
    upstream = _pinned_upstream()
    plan = plan_adopt(tmp_path, upstream, opencode=OPENCODE_CHOICE_CREATE)
    before = _snapshot(tmp_path)

    def user_writes_file(target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "user-created-during-apply\n", encoding="utf-8", newline="\n"
        )

    _inject_on_nth_create(monkeypatch, 2, user_writes_file)
    with pytest.raises(ManifestConflictError):
        apply_adopt(
            tmp_path, upstream, plan=plan, opencode=OPENCODE_CHOICE_CREATE
        )

    after = _snapshot(tmp_path)
    injected_key = str(Path(".opencode/agents/mba.md"))
    assert set(after) == set(before) | {injected_key}
    assert all(after[key] == before[key] for key in before)
    assert after[injected_key] == b"user-created-during-apply\n"
    assert not (tmp_path / "opencode.json").exists()  # first create rolled back
    assert not (tmp_path / ".mba").exists()


def test_apply_adopt_late_conflict_restores_exact_pre_call_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal on the THIRD create (no concurrent file of its own)
    must restore the complete pre-call byte snapshot — including
    pruning the directories the rolled-back creations had made."""

    _populate_clone(tmp_path)
    upstream = _pinned_upstream()
    plan = plan_adopt(tmp_path, upstream, opencode=OPENCODE_CHOICE_CREATE)
    before = _snapshot(tmp_path)

    def refuse(_target: Path) -> None:
        raise ManifestConflictError("injected refusal on the third create")

    _inject_on_nth_create(monkeypatch, 3, refuse)
    with pytest.raises(ManifestConflictError):
        apply_adopt(
            tmp_path, upstream, plan=plan, opencode=OPENCODE_CHOICE_CREATE
        )

    assert _snapshot(tmp_path) == before
    assert not (tmp_path / ".opencode").exists()  # created dirs pruned
    assert not (tmp_path / ".mba").exists()


def test_apply_adopt_preserves_concurrently_modified_created_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guarded rollback: a file this invocation created that was then
    concurrently modified is preserved — never deleted — and the
    refusal escalates naming it."""

    _populate_clone(tmp_path)
    upstream = _pinned_upstream()
    plan = plan_adopt(tmp_path, upstream, opencode=OPENCODE_CHOICE_CREATE)

    def modify_first_then_conflict(target: Path) -> None:
        (tmp_path / "opencode.json").write_text(
            "user edited the fresh file\n", encoding="utf-8", newline="\n"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("user file\n", encoding="utf-8", newline="\n")

    _inject_on_nth_create(monkeypatch, 2, modify_first_then_conflict)
    with pytest.raises(ManifestConflictError) as excinfo:
        apply_adopt(
            tmp_path, upstream, plan=plan, opencode=OPENCODE_CHOICE_CREATE
        )

    assert "preserved" in str(excinfo.value)
    assert "opencode.json" in str(excinfo.value)
    assert (tmp_path / "opencode.json").read_text(encoding="utf-8") == (
        "user edited the fresh file\n"
    )
    assert not (tmp_path / ".mba").exists()


def _symlinks_supported(tmp_path: Path) -> bool:
    """True when this host/user can create symlinks (Windows needs
    Developer Mode or elevation; everywhere else this is a given)."""

    probe_target = tmp_path / "_symlink_probe_target"
    probe_link = tmp_path / "_symlink_probe_link"
    probe_target.write_text("x", encoding="utf-8", newline="\n")
    try:
        probe_link.symlink_to(probe_target)
    except (OSError, NotImplementedError):
        return False
    finally:
        if probe_link.is_symlink():
            probe_link.unlink()
        probe_target.unlink()
    return True


def test_apply_adopt_preserves_user_symlink_replacement_at_created_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retained round-3 ownership probe: a user-owned SAME-CONTENT
    symlink swapped in at a command-created path must be preserved —
    byte equality alone is not ownership, and rollback never follows
    a symlink."""

    if not _symlinks_supported(tmp_path):
        pytest.skip("symlink creation not available on this host")

    _populate_clone(tmp_path)
    upstream = _pinned_upstream()
    plan = plan_adopt(tmp_path, upstream, opencode=OPENCODE_CHOICE_CREATE)

    def swap_first_for_symlink_then_conflict(target: Path) -> None:
        first = tmp_path / "opencode.json"
        replacement = tmp_path / "user-owned-same-content.json"
        replacement.write_bytes(first.read_bytes())
        first.unlink()
        first.symlink_to(replacement)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("user-conflict\n", encoding="utf-8", newline="\n")

    _inject_on_nth_create(monkeypatch, 2, swap_first_for_symlink_then_conflict)
    with pytest.raises(ManifestConflictError) as excinfo:
        apply_adopt(
            tmp_path, upstream, plan=plan, opencode=OPENCODE_CHOICE_CREATE
        )

    assert (tmp_path / "opencode.json").is_symlink()  # user symlink preserved
    assert (tmp_path / "user-owned-same-content.json").is_file()  # target intact
    assert "preserved" in str(excinfo.value)
    assert "opencode.json" in str(excinfo.value)
    assert "symlink" in str(excinfo.value)
    assert not (tmp_path / ".mba").exists()


def test_apply_adopt_preserves_same_content_regular_file_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same ownership class without symlinks (runs on every
    platform): a different regular file with identical bytes placed at
    a command-created path is not the command's file — its recorded
    filesystem identity differs — so it is preserved, never deleted."""

    _populate_clone(tmp_path)
    upstream = _pinned_upstream()
    plan = plan_adopt(tmp_path, upstream, opencode=OPENCODE_CHOICE_CREATE)

    def replace_first_with_same_bytes_then_conflict(target: Path) -> None:
        first = tmp_path / "opencode.json"
        data = first.read_bytes()
        first.unlink()
        first.write_bytes(data)  # identical bytes, different object
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("user-conflict\n", encoding="utf-8", newline="\n")

    _inject_on_nth_create(
        monkeypatch, 2, replace_first_with_same_bytes_then_conflict
    )
    with pytest.raises(ManifestConflictError) as excinfo:
        apply_adopt(
            tmp_path, upstream, plan=plan, opencode=OPENCODE_CHOICE_CREATE
        )

    replaced = tmp_path / "opencode.json"
    assert replaced.is_file()
    assert replaced.read_bytes() == _PINNED_VERBATIM_SOURCES[
        "opencode.json"
    ].encode("utf-8")
    assert "preserved" in str(excinfo.value)
    assert "identity differs" in str(excinfo.value)
    assert not (tmp_path / ".mba").exists()


def test_apply_adopt_preserves_replacement_in_capture_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retained round-4 capture-gap probe: a same-content regular
    file swapped in AFTER the helper created (and tokenised) the file
    but BEFORE control returns to the apply loop must be preserved.
    The token comes from the exclusive-create descriptor itself, so
    the replacement can never inherit it — even though this wrapper
    faithfully propagates the original token."""

    _populate_clone(tmp_path)
    upstream = _pinned_upstream()
    plan = plan_adopt(tmp_path, upstream, opencode=OPENCODE_CHOICE_CREATE)
    before = _snapshot(tmp_path)

    original = adopt._create_new_launch_file
    calls = {"count": 0}

    def swap_in_capture_window_then_refuse_next(target, upstream_obj, relpath):
        calls["count"] += 1
        if calls["count"] == 2:
            # Refuse BEFORE creating so the only created file is the
            # first one — the rollback of exactly that path is under
            # test, with no residue from this wrapper.
            raise ManifestConflictError("injected second-create refusal")
        token = original(target, upstream_obj, relpath)
        data = target.read_bytes()
        target.unlink()
        target.write_bytes(data)  # identical bytes, different object
        return token  # the honest token of the file that no longer exists

    monkeypatch.setattr(
        adopt, "_create_new_launch_file", swap_in_capture_window_then_refuse_next
    )
    with pytest.raises(ManifestConflictError) as excinfo:
        apply_adopt(
            tmp_path, upstream, plan=plan, opencode=OPENCODE_CHOICE_CREATE
        )

    after = _snapshot(tmp_path)
    replacement_key = str(Path("opencode.json"))
    assert set(after) == set(before) | {replacement_key}
    assert all(after[key] == before[key] for key in before)
    assert after[replacement_key] == _PINNED_VERBATIM_SOURCES[
        "opencode.json"
    ].encode("utf-8")
    assert "preserved" in str(excinfo.value)
    assert "identity differs" in str(excinfo.value)
    assert not (tmp_path / ".mba").exists()


# ---------------------------------------------------------------------------
# Raw byte identity (round-2 finding 3) — CRLF bytes of LF-managed
# content refuse, with a newline-specific reason.
# ---------------------------------------------------------------------------


def test_plan_adopt_refuses_crlf_verbatim_raw_bytes(tmp_path: Path) -> None:
    """The retained audit probe: raw bytes changed to CRLF must not be
    classified ``adopt``."""

    _populate_clone(tmp_path)
    charter = tmp_path / "docs" / "mba" / "charter.md"
    charter.write_bytes(charter.read_bytes().replace(b"\n", b"\r\n"))
    before = _snapshot(tmp_path)

    plan = plan_adopt(tmp_path, _pinned_upstream())
    row = next(e for e in plan.entries if e.relpath == "docs/mba/charter.md")
    assert row.action == ACTION_MISMATCH
    assert "newline" in row.reason

    with pytest.raises(ManifestConflictError):
        apply_adopt(tmp_path, _pinned_upstream())
    assert _snapshot(tmp_path) == before
    assert not (tmp_path / ".mba").exists()


def test_plan_adopt_refuses_crlf_managed_block_raw_bytes(tmp_path: Path) -> None:
    _populate_clone(tmp_path)
    agents = tmp_path / "AGENTS.md"
    agents.write_bytes(agents.read_bytes().replace(b"\n", b"\r\n"))

    plan = plan_adopt(tmp_path, _pinned_upstream())
    row = next(e for e in plan.entries if e.relpath == "AGENTS.md")
    assert row.action == ACTION_MISMATCH
    assert "newline" in row.reason
    with pytest.raises(ManifestConflictError):
        apply_adopt(tmp_path, _pinned_upstream())
    assert not (tmp_path / ".mba").exists()


def test_plan_adopt_refuses_non_utf8_managed_block(tmp_path: Path) -> None:
    _populate_clone(tmp_path)
    agents = tmp_path / "AGENTS.md"
    agents.write_bytes(agents.read_bytes() + b"\xff\xfe")

    plan = plan_adopt(tmp_path, _pinned_upstream())
    row = next(e for e in plan.entries if e.relpath == "AGENTS.md")
    assert row.action == ACTION_MISMATCH
    assert "UTF-8" in row.reason


# ---------------------------------------------------------------------------
# Durable omission (round-2 finding 2) — the recorded choice survives
# `mba upgrade`; a user file at an omitted path is never overwritten.
# ---------------------------------------------------------------------------


def test_upgrade_preserves_adopted_omission(tmp_path: Path) -> None:
    _populate_clone(tmp_path)
    apply_adopt(tmp_path, _pinned_upstream())

    newer = _pinned_upstream(
        mba_version="0.1.1-test",
        overrides={"docs/mba/charter.md": "# Charter v2 (test fixture)\n"},
    )
    installed = read_manifest(tmp_path)
    plan = plan_upgrade(tmp_path, installed, newer)
    skip_paths = {e.relpath for e in plan.entries if e.action == ACTION_SKIP}
    assert skip_paths == set(OPENCODE_LAUNCH_TARGETS)
    assert not plan.has_conflicts

    apply_upgrade(tmp_path, installed, newer, plan=plan)

    for relpath in OPENCODE_LAUNCH_TARGETS:
        assert not (tmp_path / relpath).exists()
    upgraded = read_manifest(tmp_path)
    assert set(upgraded.omitted) == set(OPENCODE_LAUNCH_TARGETS)
    assert (tmp_path / "docs" / "mba" / "charter.md").read_text(
        encoding="utf-8"
    ) == "# Charter v2 (test fixture)\n"
    assert not detect_drift(tmp_path, upgraded).has_drift

    # A second upgrade keeps preserving the omission — and is a noop.
    plan2 = plan_upgrade(tmp_path, upgraded, newer)
    assert {
        e.relpath for e in plan2.entries if e.action == ACTION_SKIP
    } == set(OPENCODE_LAUNCH_TARGETS)
    assert plan2.is_noop


def test_upgrade_conflicts_on_user_file_at_omitted_path(tmp_path: Path) -> None:
    _populate_clone(tmp_path)
    apply_adopt(tmp_path, _pinned_upstream())
    user = tmp_path / "opencode.json"
    user.write_text("my own config\n", encoding="utf-8", newline="\n")

    installed = read_manifest(tmp_path)
    newer = _pinned_upstream(mba_version="0.1.1-test")
    plan = plan_upgrade(tmp_path, installed, newer)
    row = next(e for e in plan.entries if e.relpath == "opencode.json")
    assert row.action == ACTION_CONFLICT
    assert "user-owned" in row.reason

    with pytest.raises(ManifestConflictError):
        apply_upgrade(tmp_path, installed, newer, plan=plan)
    assert user.read_text(encoding="utf-8") == "my own config\n"


def test_manifest_omitted_field_round_trips_and_stays_absent_when_empty(
    tmp_path: Path,
) -> None:
    upstream = _pinned_upstream()
    assert "omitted" not in upstream.to_dict()  # empty ⇒ key absent

    with_omission = replace(
        upstream,
        files=tuple(f for f in upstream.files if f.relpath != "opencode.json"),
        omitted=("opencode.json",),
    )
    write_manifest(tmp_path, with_omission)
    reread = read_manifest(tmp_path)
    assert reread.omitted == ("opencode.json",)


# ---------------------------------------------------------------------------
# CLI integration — the real packaged surface end to end.
# ---------------------------------------------------------------------------


def _run(argv: list[str], cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mba_foundation", *argv],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd),
    )


def _fresh_clone_equivalent(tmp_path: Path) -> None:
    """Materialise the publishing-consumer clone shape with ``mba init``.

    ``mba init`` writes the full surface; deleting the private manifest
    and the private launch files leaves exactly what a publisher tracks
    — the state a fresh ``git clone`` of that repo presents.
    """

    proc = _run(["init", "--root", str(tmp_path)])
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    (tmp_path / ".mba" / "manifest.json").unlink()
    (tmp_path / ".mba").rmdir()
    for relpath in OPENCODE_LAUNCH_TARGETS:
        target = tmp_path / relpath
        if target.exists():
            target.unlink()
    # `mba init` writes the managed blocks with platform newlines
    # (CRLF on Windows); an adoptable clone presents LF bytes because
    # the publisher pins `eol=lf` — model the checkout, not the init.
    for relpath in install_content_managed_block_targets():
        _force_lf(tmp_path / relpath)


def test_cli_adopt_fresh_clone_equivalent_end_to_end(tmp_path: Path) -> None:
    _fresh_clone_equivalent(tmp_path)

    proc = _run(["adopt", "--root", str(tmp_path)])
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["already_installed"] is False
    assert payload["mismatch_paths"] == []
    assert set(payload["omitted_paths"]) == set(OPENCODE_LAUNCH_TARGETS)
    assert (tmp_path / ".mba" / "manifest.json").exists()

    # Acceptance: status reports installed, no drift, no conflicts.
    status = _run(["status", "--root", str(tmp_path)])
    assert status.returncode == 0, f"stdout={status.stdout!r}"
    status_payload = json.loads(status.stdout)
    assert status_payload["installed"] is True
    assert status_payload["has_drift"] is False
    assert status_payload["has_conflicts"] is False

    # Idempotent re-run.
    again = _run(["adopt", "--root", str(tmp_path)])
    assert again.returncode == 0
    assert json.loads(again.stdout)["already_installed"] is True


def test_cli_adopt_dry_run_writes_nothing(tmp_path: Path) -> None:
    _fresh_clone_equivalent(tmp_path)

    proc = _run(["adopt", "--root", str(tmp_path), "--dry-run"])
    assert proc.returncode == 4, f"stdout={proc.stdout!r}"
    payload = json.loads(proc.stdout)
    assert payload["dry_run"] is True
    assert not (tmp_path / ".mba").exists()


def test_cli_adopt_mismatch_refuses_with_no_writes(tmp_path: Path) -> None:
    _fresh_clone_equivalent(tmp_path)
    charter = tmp_path / "docs" / "mba" / "charter.md"
    charter.write_text(
        charter.read_text(encoding="utf-8") + "drift\n", encoding="utf-8", newline="\n"
    )

    proc = _run(["adopt", "--root", str(tmp_path)])
    assert proc.returncode == 7, f"stdout={proc.stdout!r}"
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert payload["mismatch_paths"] == ["docs/mba/charter.md"]
    assert not (tmp_path / ".mba").exists()


def test_cli_adopt_opencode_create_restores_launch_files(tmp_path: Path) -> None:
    _fresh_clone_equivalent(tmp_path)

    proc = _run(["adopt", "--root", str(tmp_path), "--opencode", "create"])
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = json.loads(proc.stdout)
    assert set(payload["created_paths"]) == set(OPENCODE_LAUNCH_TARGETS)
    for relpath in OPENCODE_LAUNCH_TARGETS:
        assert (tmp_path / relpath).is_file()

    status = _run(["status", "--root", str(tmp_path)])
    assert status.returncode == 0
    assert json.loads(status.stdout)["has_drift"] is False


def test_cli_adopt_preflight_refusal_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A ``bd`` mismatch refuses before any classification or write —
    exercised in-process so no fake binary is needed."""

    def _fake_run_bd_version(bd_binary: str = "bd", cwd: Path | None = None) -> str:
        return "bd version 9.9.9 (unvalidated)"

    monkeypatch.setattr(cli.preflight, "run_bd_version", _fake_run_bd_version)
    rc = cli.main(["adopt", "--root", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 5
    assert payload["ok"] is False
    assert payload["stage"] == "preflight"
    assert payload["bd_version"] == "9.9.9"
    assert not any(tmp_path.iterdir())
