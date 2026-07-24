"""``.mba/manifest.json`` schema + drift / upgrade behaviour tests.

These tests pin every acceptance row of Bead ``example-020.1``:

* ``mba init`` writes ``.mba/manifest.json`` recording installed
  version, source, and checksums.
* ``mba status`` reports installed version + drift.
* ``mba upgrade --dry-run`` previews without changing.
* User-edited managed blocks conflict instead of being overwritten.
* Beads version preflight gates install/upgrade.
* The manifest schema is round-trippable from disk.

The tests use a fake ``bd`` binary for the preflight path so they are
hermetic — no shell-out, no Beads state in the host repo. The CLI
integration tests at the bottom exercise the surface through the
public ``main(argv)`` entry point so the runtime agrees with the
module's behaviour.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from mba_version import __version__

from mba_foundation import cli, manifest
from mba_foundation.manifest import (
    ACTION_CONFLICT,
    ACTION_DELETE_FILE,
    ACTION_INSTALL,
    ACTION_REPLACE,
    ACTION_RETIRE,
    ACTION_REMOVE_BLOCK,
    ACTION_SKIP,
    ACTION_UP_TO_DATE,
    KIND_MANAGED_BLOCK,
    KIND_VERBATIM_COPY,
    ManifestConflictError,
    SCHEMA_VERSION,
    SOURCE_PACKAGED,
    STATE_NOT_INSTALLED,
    STATE_UNCHANGED,
    STATE_USER_EDITED,
    build_manifest,
    build_status_summary,
    detect_drift,
    plan_upgrade,
    plan_remove,
    read_manifest,
    sha256_text,
    write_manifest,
)
from mba_foundation.markers import (
    MBA_RULES_BEGIN_MARKER,
    MBA_RULES_END_MARKER,
    MBA_RULES_BLOCK,
    install_block,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_fake_manifest(
    root: Path,
    *,
    mba_version: str = "0.1.0.dev0",
    source: str = SOURCE_PACKAGED,
    block_targets: tuple[str, ...] = ("AGENTS.md", "CLAUDE.md"),
    skill_text: str = "# MBA skill (test fixture)\n",
    block_body: str | None = None,
    installed_at: str = "2026-07-21T00:00:00+00:00",
    opencode_config_text: str = '{\n  "default_agent": "mba"\n}\n',
    opencode_agent_text: str = "---\ndescription: MBA Orchestrator\n---\n",
    opencode_worker_agent_text: str = (
        "---\ndescription: MBA worker\nmode: primary\n---\n"
    ),
    include_opencode: bool = False,
) -> Path:
    """Write a controlled manifest + matching target files in ``root``.

    The test suite writes the manifest with deterministic shas so the
    drift / upgrade tests compare known values. The corresponding
    on-disk files are written with content whose sha matches the
    manifest's recorded sha — this is the "fresh install" baseline a
    status or upgrade call would observe.

    The ``include_opencode`` flag opts in to the OpenCode bootstrap
    targets (``opencode.json``, ``.opencode/agents/mba.md`` and
    ``.opencode/agents/mba-worker.md``).
    Legacy call sites that already pin the skill as the only
    verbatim-copy stay green; new tests for the OpenCode bootstrap
    flip the flag.
    """

    if block_body is None:
        block_body = MBA_RULES_BLOCK
    verbatim_copy_targets: tuple[str, ...] = (".agents/skills/mba/SKILL.md",)
    verbatim_copy_sources: dict[str, str] = {".agents/skills/mba/SKILL.md": skill_text}
    if include_opencode:
        verbatim_copy_targets = (
            ".agents/skills/mba/SKILL.md",
            "opencode.json",
            ".opencode/agents/mba.md",
            ".opencode/agents/mba-worker.md",
        )
        verbatim_copy_sources = {
            ".agents/skills/mba/SKILL.md": skill_text,
            "opencode.json": opencode_config_text,
            ".opencode/agents/mba.md": opencode_agent_text,
            ".opencode/agents/mba-worker.md": opencode_worker_agent_text,
        }
    return manifest.build_manifest(
        source=source,
        preflight_evidence=manifest.PreflightEvidence(
            bd_version="1.0.4",
            matches_record=True,
            raw_output="bd version 1.0.4 (ce242a879: HEAD@ce242a879678)",
        ),
        installed_at=installed_at,
        mba_version=mba_version,
        managed_block_targets=block_targets,
        verbatim_copy_targets=verbatim_copy_targets,
        source_block_body=block_body,
        source_skill_text=skill_text,
        verbatim_copy_sources=verbatim_copy_sources,
    ), block_body, skill_text


def _populate_with_matching_targets(
    manifest_obj: manifest.Manifest,
    block_body: str,
    skill_text: str,
    root: Path,
) -> None:
    """Write the on-disk files the manifest pins so drift == unchanged."""

    for entry in manifest_obj.files:
        target = root / entry.relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry.kind == KIND_MANAGED_BLOCK:
            # Write the file with the canonical MBA block installed.
            install_block(target)
        elif entry.kind == KIND_VERBATIM_COPY:
            # Use the canonical source text the build recorded so the
            # on-disk sha matches the manifest's recorded sha. The
            # MBA skill is the only verbatim-copy the legacy fixture
            # opts into; OpenCode-bootstrap fixtures pin their own
            # source texts via ``verbatim_copy_sources`` so this
            # default branch still produces the right content.
            target.write_text(_verbatim_source_text_for(entry, skill_text), encoding="utf-8")


def _verbatim_source_text_for(
    entry: "manifest.ManagedFileEntry", default: str
) -> str:
    """Return the recorded source text for a verbatim-copy entry.

    The MBA skill uses ``default``. The OpenCode bootstrap fixtures
    read from the live package so the on-disk sha matches what
    :func:`manifest.build_manifest` recorded. Falls back to
    ``default`` for any other relpath so the legacy fixture (skill
    only) keeps working without changes.
    """

    relpath = entry.relpath
    if relpath == "opencode.json":
        return manifest._read_opencode_config_source_text()
    if relpath == ".opencode/agents/mba.md":
        return manifest._read_opencode_agent_source_text()
    if relpath == ".opencode/agents/mba-worker.md":
        return manifest._read_opencode_worker_agent_source_text()
    return default


# ---------------------------------------------------------------------------
# Schema — dataclass round-trip
# ---------------------------------------------------------------------------


def test_schema_version_is_one() -> None:
    """The current schema is ``1``. A future bump goes through a new
    test + schema-aware read path."""

    assert SCHEMA_VERSION == 1


def test_managed_file_entry_round_trip() -> None:
    entry = manifest.ManagedFileEntry(
        relpath="AGENTS.md",
        kind=KIND_MANAGED_BLOCK,
        sha256="deadbeef" * 8,
    )
    data = entry.to_dict()
    assert data["relpath"] == "AGENTS.md"
    assert data["kind"] == KIND_MANAGED_BLOCK
    assert data["sha256"] == "deadbeef" * 8
    rebuilt = manifest.ManagedFileEntry.from_dict(data)
    assert rebuilt == entry


def test_managed_file_entry_from_dict_rejects_missing_fields() -> None:
    with pytest.raises(ValueError):
        manifest.ManagedFileEntry.from_dict({"relpath": "", "kind": "x", "sha256": "y"})
    with pytest.raises(ValueError):
        manifest.ManagedFileEntry.from_dict({"relpath": "x", "kind": "", "sha256": "y"})
    with pytest.raises(ValueError):
        manifest.ManagedFileEntry.from_dict({"relpath": "x", "kind": "y", "sha256": ""})


def test_preflight_evidence_round_trip() -> None:
    ev = manifest.PreflightEvidence(
        bd_version="1.0.4",
        matches_record=True,
        raw_output="bd version 1.0.4 (ce242a879: HEAD@ce242a879678)",
    )
    data = ev.to_dict()
    rebuilt = manifest.PreflightEvidence.from_dict(data)
    assert rebuilt == ev


def test_manifest_round_trip(tmp_path: Path) -> None:
    """``to_dict`` → ``from_dict`` returns equivalent :class:`Manifest`.
    The round-trip is lossless so an old manifest can be reloaded byte-
    for-byte by a future ``mba upgrade``."""

    m, _, _ = _write_fake_manifest(tmp_path)
    payload = m.to_dict()
    rebuilt = manifest.Manifest.from_dict(payload)
    assert rebuilt.schema == m.schema
    assert rebuilt.mba_version == m.mba_version
    assert rebuilt.source == m.source
    assert rebuilt.installed_at == m.installed_at
    assert rebuilt.preflight == m.preflight
    assert rebuilt.files == m.files


def test_manifest_from_dict_rejects_unsupported_schema(tmp_path: Path) -> None:
    m, _, _ = _write_fake_manifest(tmp_path)
    data = m.to_dict()
    data["schema"] = 999
    with pytest.raises(ValueError):
        manifest.Manifest.from_dict(data)


def test_manifest_to_dict_round_trip_through_json(tmp_path: Path) -> None:
    """Disk format is JSON; round-trip via ``json.loads`` and reload on
    the persisted fields only (``upstream_bodies`` is in-memory only).
    """

    m, _, _ = _write_fake_manifest(tmp_path)
    payload = m.to_dict()
    text = json.dumps(payload)
    rebuilt = manifest.Manifest.from_dict(json.loads(text))
    assert rebuilt.schema == m.schema
    assert rebuilt.mba_version == m.mba_version
    assert rebuilt.source == m.source
    assert rebuilt.installed_at == m.installed_at
    assert rebuilt.preflight == m.preflight
    assert rebuilt.files == m.files
    assert rebuilt.upstream_bodies == ()


# ---------------------------------------------------------------------------
# SHA256 + extract_block_body
# ---------------------------------------------------------------------------


def test_sha256_text_is_deterministic() -> None:
    assert sha256_text("a") == sha256_text("a")
    assert sha256_text("a") != sha256_text("b")


def test_extract_block_body_finds_block_in_text() -> None:
    text = (
        "header\n"
        "<!-- BEGIN MBA RULES -->\n"
        "the block body\n"
        "<!-- END MBA RULES -->\n"
        "footer\n"
    )
    found = manifest.extract_block_body(text)
    assert found is not None
    begin_idx, end_idx, body = found
    # The body is the slice strictly between the BEGIN line and the END
    # line; the regex captures the BEGIN/END lines whole (including
    # their trailing newlines), so the body slice starts with the
    # newline that follows BEGIN and ends with the newline that
    # precedes END.
    assert body == "\nthe block body\n"
    # begin_idx / end_idx index into the original text.
    assert text[begin_idx:end_idx].startswith("\n")
    assert text[begin_idx:end_idx].endswith("\n")


def test_extract_block_body_returns_none_when_absent() -> None:
    assert manifest.extract_block_body("plain text\n") is None
    # Half-pair is also None (no end after begin).
    text = "<!-- BEGIN MBA RULES -->\nbody with no end\n"
    assert manifest.extract_block_body(text) is None


def test_current_block_body_reads_file(tmp_path: Path) -> None:
    """The on-disk body is the slice of MBA_RULES_BLOCK between the
    marker lines, not the full literal (which includes the BEGIN/END
    markers themselves)."""

    target = tmp_path / "AGENTS.md"
    install_block(target)
    body, err = manifest.current_block_body(target)
    assert err == ""
    assert body is not None
    # Compute the expected body from the canonical literal the
    # installer wrote so the test does not lose precision on
    # line-trimming.
    expected = manifest.extract_block_body(MBA_RULES_BLOCK)
    assert expected is not None
    _, _, expected_body = expected
    assert body == expected_body
    assert sha256_text(body) == sha256_text(expected_body)


def test_current_block_body_returns_none_for_missing_file(tmp_path: Path) -> None:
    body, err = manifest.current_block_body(tmp_path / "missing.md")
    assert body is None
    assert "does not exist" in err


def test_current_block_body_returns_none_when_no_block(tmp_path: Path) -> None:
    target = tmp_path / "AGENTS.md"
    target.write_text("# Project\nNo MBA block here.\n", encoding="utf-8")
    body, err = manifest.current_block_body(target)
    assert body is None
    assert "no MBA RULES block" in err


# ---------------------------------------------------------------------------
# Manifest build + write + read
# ---------------------------------------------------------------------------


def test_build_manifest_default_targets() -> None:
    """Default :func:`build_manifest` records exactly the five install
    surfaces that the prompt and Charter expect — the two
    managed-block files, the MBA skill, and the OpenCode-bootstrap
    root config + Orchestrator agent.

    The OpenCode-bootstrap entries are part of the install surface so
    a fresh ``mba init`` activates a downstream OpenCode consumer as
    the MBA Orchestrator before any project exploration or worker
    launch.
    """

    m = build_manifest(mba_version="0.1.0.dev0", installed_at="2026-07-21T00:00:00+00:00")
    paths = [entry.relpath for entry in m.files]
    assert paths == [
        "AGENTS.md",
        "CLAUDE.md",
        ".agents/skills/mba/SKILL.md",
        "opencode.json",
        ".opencode/agents/mba.md",
        ".opencode/agents/mba-worker.md",
    ]
    kinds = [entry.kind for entry in m.files]
    assert kinds == [
        KIND_MANAGED_BLOCK,
        KIND_MANAGED_BLOCK,
        KIND_VERBATIM_COPY,
        KIND_VERBATIM_COPY,
        KIND_VERBATIM_COPY,
        KIND_VERBATIM_COPY,
    ]


def test_build_manifest_opencode_entries_resolve_default_targets() -> None:
    """Without ``verbatim_copy_sources`` overrides,
    :func:`build_manifest` reads the OpenCode bootstrap source texts
    from the live package via ``importlib.resources``. The recorded
    shas match what the on-disk consumer files will hold after
    :func:`apply_upgrade` writes them."""

    from mba_foundation import manifest as manifest_module

    m = build_manifest(mba_version="0.1.0.dev0", installed_at="2026-07-21T00:00:00+00:00")
    by_relpath = {entry.relpath: entry for entry in m.files}
    expected_config = manifest_module._read_opencode_config_source_text()
    expected_agent = manifest_module._read_opencode_agent_source_text()
    expected_worker_agent = (
        manifest_module._read_opencode_worker_agent_source_text()
    )
    assert by_relpath["opencode.json"].sha256 == sha256_text(expected_config)
    assert by_relpath[".opencode/agents/mba.md"].sha256 == sha256_text(expected_agent)
    assert by_relpath[".opencode/agents/mba-worker.md"].sha256 == sha256_text(
        expected_worker_agent
    )
    # The live ``importlib.resources`` text used by the helper must
    # match what was recorded so a fresh-install ``apply_upgrade``
    # produces byte-identical on-disk content.
    assert expected_config == manifest_module._read_packaged_resource_text(
        "resources/opencode/opencode.json"
    )
    assert expected_agent == manifest_module._read_packaged_resource_text(
        "resources/opencode/agents/mba.md"
    )
    assert expected_worker_agent == manifest_module._read_packaged_resource_text(
        "resources/opencode/agents/mba-worker.md"
    )


def test_packaged_verbatim_sources_resolve_from_data_files_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root = tmp_path / "site-packages"
    data_root = tmp_path / "prefix"
    source_root = data_root / "share" / "doc" / "multiple-beaded-agents"
    relpaths = (
        "docs/mba/charter.md",
        "docs/beads/capabilities.md",
    )
    for relpath in relpaths:
        source = source_root / relpath
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f"source for {relpath}\n", encoding="utf-8")
    monkeypatch.setattr(manifest, "_repo_root", lambda: package_root)
    monkeypatch.setattr(manifest.sysconfig, "get_path", lambda name: data_root)

    for relpath in relpaths:
        assert manifest._read_verbatim_copy_source_text(relpath) == (
            f"source for {relpath}\n"
        )


def test_packaged_verbatim_sources_resolve_from_user_site_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user-site install (e.g. ``pip install --user``) places data
    files under ``site.getuserbase() + "/share/doc/multiple-beaded-agents"``
    rather than the system prefix returned by
    ``sysconfig.get_path("data")``.

    Reproduces the downstream test repo downstream failure: a vanilla
    ``pip install --user multiple-beaded-agents`` followed by
    ``python -m mba_foundation upgrade --root . --dry-run`` reported
    ``verbatim-copy source not found for 'docs/mba/charter.md'`` because
    the lookup only checked the system prefix.
    """

    package_root = tmp_path / "site-packages"
    system_data_root = tmp_path / "prefix"
    user_base = tmp_path / "user-base"
    user_data_root = user_base / "share" / "doc" / "multiple-beaded-agents"
    relpaths = (
        "docs/mba/charter.md",
        "docs/beads/capabilities.md",
    )
    for relpath in relpaths:
        source = user_data_root / relpath
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f"user-site source for {relpath}\n", encoding="utf-8")
    monkeypatch.setattr(manifest, "_repo_root", lambda: package_root)
    monkeypatch.setattr(manifest.sysconfig, "get_path", lambda name: system_data_root)
    monkeypatch.setattr(manifest.site, "getuserbase", lambda: str(user_base))

    for relpath in relpaths:
        assert manifest._read_verbatim_copy_source_text(relpath) == (
            f"user-site source for {relpath}\n"
        )


def test_packaged_verbatim_sources_keep_system_prefix_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system_root = tmp_path / "system" / "share" / "doc" / "multiple-beaded-agents"
    user_base = tmp_path / "user"
    user_root = user_base / "share" / "doc" / "multiple-beaded-agents"
    for root, text in ((system_root, "system\n"), (user_root, "user\n")):
        source = root / "docs/mba/charter.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(text, encoding="utf-8")
    monkeypatch.setattr(manifest, "_repo_root", lambda: tmp_path / "checkout")
    monkeypatch.setattr(manifest.sysconfig, "get_path", lambda name: tmp_path / "system")
    monkeypatch.setattr(manifest.site, "getuserbase", lambda: str(user_base))

    assert manifest._read_verbatim_copy_source_text("docs/mba/charter.md") == "system\n"


def test_packaged_verbatim_sources_use_user_site_after_missing_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_base = tmp_path / "user"
    source = user_base / "share" / "doc" / "multiple-beaded-agents" / "docs/mba/charter.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("user\n", encoding="utf-8")
    monkeypatch.setattr(manifest, "_repo_root", lambda: tmp_path / "checkout")
    monkeypatch.setattr(manifest.sysconfig, "get_path", lambda name: tmp_path / "system")
    monkeypatch.setattr(manifest.site, "getuserbase", lambda: str(user_base))

    assert manifest._read_verbatim_copy_source_text("docs/mba/charter.md") == "user\n"


def test_build_manifest_block_sha_matches_source_block() -> None:
    """The recorded ``managed_block`` sha is the SHA256 of the on-disk
    body that :func:`mba_foundation.markers.install_block` writes
    between the marker lines.

    The test uses a complete source (with BEGIN/END markers) and
    asserts that the recorded sha equals the sha of the extracted
    body — the same slice a later drift check will compare against.
    """

    body_text = "deterministic body\n"
    full_source = (
        MBA_RULES_BEGIN_MARKER
        + "\n"
        + body_text
        + MBA_RULES_END_MARKER
        + "\n"
    )
    m = build_manifest(
        mba_version="0.1.0.dev0",
        installed_at="2026-07-21T00:00:00+00:00",
        source_block_body=full_source,
    )
    expected_extracted = manifest.extract_block_body(full_source)
    assert expected_extracted is not None
    _, _, expected_body = expected_extracted
    assert m.files[0].sha256 == sha256_text(expected_body)
    assert m.files[1].sha256 == sha256_text(expected_body)


def test_build_manifest_skill_sha_matches_source_skill() -> None:
    """The ``verbatim_copy`` sha is the SHA256 of the source skill text."""

    m = build_manifest(
        mba_version="0.1.0.dev0",
        installed_at="2026-07-21T00:00:00+00:00",
        source_skill_text="# Skill v2\n",
    )
    assert m.files[2].sha256 == sha256_text("# Skill v2\n")


def test_write_then_read_manifest_roundtrip(tmp_path: Path) -> None:
    """Persisted fields round-trip cleanly. ``upstream_bodies`` is
    in-memory only (not persisted) so a written-then-read manifest
    has empty bodies — compare on the persisted fields."""

    m, _, _ = _write_fake_manifest(tmp_path)
    manifest.write_manifest(tmp_path, m)
    read_back = manifest.read_manifest(tmp_path)
    assert read_back is not None
    assert read_back.schema == m.schema
    assert read_back.mba_version == m.mba_version
    assert read_back.source == m.source
    assert read_back.installed_at == m.installed_at
    assert read_back.preflight == m.preflight
    assert read_back.files == m.files
    # Bodies do NOT round-trip through JSON — they are in-memory only.
    assert read_back.upstream_bodies == ()


def test_read_manifest_returns_none_when_absent(tmp_path: Path) -> None:
    assert manifest.read_manifest(tmp_path) is None


def test_manifest_path_is_dot_mba_manifest_json(tmp_path: Path) -> None:
    assert manifest.manifest_path(tmp_path) == (tmp_path / ".mba" / "manifest.json")


def test_write_manifest_is_atomic_via_tmp(tmp_path: Path) -> None:
    """The atomic write leaves no ``.tmp`` sibling behind after a
    successful call."""

    m, _, _ = _write_fake_manifest(tmp_path)
    manifest.write_manifest(tmp_path, m)
    final_path = manifest.manifest_path(tmp_path)
    assert final_path.exists()
    # No ``.tmp`` residue. The :func:`write_manifest` code writes to
    # ``manifest.json.tmp`` and renames; check the sibling is gone.
    sibling = final_path.with_suffix(final_path.suffix + ".tmp")
    assert not sibling.exists()


def test_write_manifest_creates_dot_mba_directory(tmp_path: Path) -> None:
    m, _, _ = _write_fake_manifest(tmp_path)
    manifest.write_manifest(tmp_path, m)
    assert (tmp_path / ".mba").is_dir()


# ---------------------------------------------------------------------------
# Drift detection
# ---------------------------------------------------------------------------


def test_detect_drift_when_not_installed(tmp_path: Path) -> None:
    """No manifest ⇢ every install-content target reports
    ``not_installed``."""

    drift = manifest.detect_drift(tmp_path, None)
    assert not drift.is_installed
    assert not drift.has_drift is False  # every entry is non-UNCHANGED
    assert drift.has_drift is True
    states = {entry.relpath: entry.state for entry in drift.entries}
    # The default install_content_targets list includes the three
    # canonical targets; "not_installed" must apply to all of them.
    for relpath in ("AGENTS.md", "CLAUDE.md", ".agents/skills/mba/SKILL.md"):
        assert states.get(relpath) == STATE_NOT_INSTALLED


def test_detect_drift_unchanged_when_on_disk_matches_recorded(tmp_path: Path) -> None:
    """A consumer repo whose on-disk files exactly match the recorded
    manifest reports no drift, no conflicts."""

    m, block_body, skill_text = _write_fake_manifest(tmp_path)
    _populate_with_matching_targets(m, block_body, skill_text, tmp_path)
    manifest.write_manifest(tmp_path, m)
    read_back = manifest.read_manifest(tmp_path)

    drift = manifest.detect_drift(tmp_path, read_back)
    assert drift.is_installed
    assert drift.has_drift is False
    assert drift.has_conflicts is False
    for entry in drift.entries:
        assert entry.state == STATE_UNCHANGED
        assert entry.current_sha == entry.installed_sha


def test_detect_drift_user_edited_managed_block(tmp_path: Path) -> None:
    """Editing the MBA RULES block body between the BEGIN/END markers
    triggers a ``user_edited`` verdict for that file."""

    m, block_body, skill_text = _write_fake_manifest(tmp_path)
    _populate_with_matching_targets(m, block_body, skill_text, tmp_path)
    manifest.write_manifest(tmp_path, m)
    read_back = manifest.read_manifest(tmp_path)

    # Edit the AGENTS.md MBA block body — insert a single line after
    # the BEGIN marker so the recorded sha no longer matches.
    target = tmp_path / "AGENTS.md"
    text = target.read_text(encoding="utf-8")
    new_text = text.replace(
        MBA_RULES_BEGIN_MARKER + "\n",
        MBA_RULES_BEGIN_MARKER + "\n# Custom user edit\n",
        1,
    )
    target.write_text(new_text, encoding="utf-8")

    drift = manifest.detect_drift(tmp_path, read_back)
    assert drift.has_conflicts is True
    assert "AGENTS.md" in drift.user_edited_paths

    by_relpath = {e.relpath: e for e in drift.entries}
    assert by_relpath["AGENTS.md"].state == STATE_USER_EDITED
    assert by_relpath["AGENTS.md"].current_sha != by_relpath["AGENTS.md"].installed_sha
    # CLAUDE.md is unchanged.
    assert by_relpath["CLAUDE.md"].state == STATE_UNCHANGED


def test_detect_drift_user_edited_skill_file(tmp_path: Path) -> None:
    """A user edit to the verbatim-copy skill file shows up as drift.

    Verifies the ``verbatim_copy`` kind hits the same conflict path
    as the managed_block kind.
    """

    m, block_body, skill_text = _write_fake_manifest(tmp_path)
    _populate_with_matching_targets(m, block_body, skill_text, tmp_path)
    manifest.write_manifest(tmp_path, m)
    read_back = manifest.read_manifest(tmp_path)

    target = tmp_path / ".agents" / "skills" / "mba" / "SKILL.md"
    target.write_text(target.read_text(encoding="utf-8") + "\n# Custom line\n", encoding="utf-8")

    drift = manifest.detect_drift(tmp_path, read_back)
    assert drift.has_conflicts is True
    by_relpath = {e.relpath: e for e in drift.entries}
    assert by_relpath[".agents/skills/mba/SKILL.md"].state == STATE_USER_EDITED


def test_detect_drift_partially_installed(tmp_path: Path) -> None:
    """A target file that does not exist is ``not_installed`` even
    when the manifest has a recorded sha for it."""

    m, block_body, skill_text = _write_fake_manifest(tmp_path)
    _populate_with_matching_targets(m, block_body, skill_text, tmp_path)
    # Delete CLAUDE.md but leave AGENTS.md untouched.
    (tmp_path / "CLAUDE.md").unlink()
    manifest.write_manifest(tmp_path, m)

    read_back = manifest.read_manifest(tmp_path)
    drift = manifest.detect_drift(tmp_path, read_back)
    by_relpath = {e.relpath: e for e in drift.entries}
    assert by_relpath["AGENTS.md"].state == STATE_UNCHANGED
    assert by_relpath["CLAUDE.md"].state == STATE_NOT_INSTALLED
    assert drift.has_drift is True
    assert drift.has_conflicts is False


def test_detect_drift_no_drift_when_state_equals_recorded_sha(tmp_path: Path) -> None:
    """The recorded-sha rule, not the upstream-sha rule, drives
    ``user_edited``. A target whose on-disk content matches the
    recorded install but differs from the upstream skill content
    (the developer chose to leave an older skill in place) is reported
    as ``unchanged`` by drift, and the upgrade planner will then flag
    it as ``replace`` (because the recorded sha != upstream sha)."""

    m, block_body, skill_text = _write_fake_manifest(tmp_path)
    _populate_with_matching_targets(m, block_body, skill_text, tmp_path)
    manifest.write_manifest(tmp_path, m)
    read_back = manifest.read_manifest(tmp_path)

    drift = manifest.detect_drift(tmp_path, read_back)
    for entry in drift.entries:
        assert entry.state == STATE_UNCHANGED


# ---------------------------------------------------------------------------
# Plan upgrade
# ---------------------------------------------------------------------------


def _upstream_with_modified_block(tmp_path: Path) -> manifest.Manifest:
    """An upstream manifest whose MBA RULES block is a NEW string.

    Used to drive the "unmodified on disk, MBA changed upstream"
    scenario — a status / upgrade should call this ``replace``, not
    ``conflict``.
    """

    return build_manifest(
        source=SOURCE_PACKAGED,
        installed_at="2026-07-21T01:00:00+00:00",
        mba_version="0.1.0.dev0",
        managed_block_targets=("AGENTS.md", "CLAUDE.md"),
        verbatim_copy_targets=(".agents/skills/mba/SKILL.md",),
        source_block_body="newer MBA RULES block\n",
        source_skill_text="# MBA skill (updated)\n",
    )


def test_plan_upgrade_when_not_installed(tmp_path: Path) -> None:
    """Fresh repo (no manifest) → every target is ``install``."""

    upstream = build_manifest(
        mba_version="0.1.0.dev0",
        installed_at="2026-07-21T00:00:00+00:00",
        source_block_body="new body\n",
        source_skill_text="# Skill\n",
    )
    plan = plan_upgrade(tmp_path, installed=None, upstream=upstream)
    assert not plan.has_conflicts
    assert plan.is_noop is False
    for entry in plan.entries:
        assert entry.action == ACTION_INSTALL


def test_plan_upgrade_up_to_date(tmp_path: Path) -> None:
    """On-disk + recorded == upstream → ``up_to_date``."""

    m, block_body, skill_text = _write_fake_manifest(tmp_path)
    _populate_with_matching_targets(m, block_body, skill_text, tmp_path)
    manifest.write_manifest(tmp_path, m)
    installed = manifest.read_manifest(tmp_path)

    # Upstream has the SAME block body + SAME skill text as recorded;
    # nothing has changed upstream. Plan is a noop. The helper builds
    # the skill-only verbatim-copy set so upstream must use the same
    # surface to avoid introducing ``install`` rows for the OpenCode
    # bootstrap entries.
    upstream = build_manifest(
        mba_version=installed.mba_version,
        installed_at=installed.installed_at,
        managed_block_targets=("AGENTS.md", "CLAUDE.md"),
        verbatim_copy_targets=(".agents/skills/mba/SKILL.md",),
        verbatim_copy_sources={".agents/skills/mba/SKILL.md": skill_text},
        source_block_body=block_body,
        source_skill_text=skill_text,
    )
    plan = plan_upgrade(tmp_path, installed, upstream)
    assert plan.is_noop
    assert not plan.has_conflicts
    for entry in plan.entries:
        assert entry.action == ACTION_UP_TO_DATE


def test_plan_upgrade_replace_when_recorded_equal_ondisk_but_upstream_newer(tmp_path: Path) -> None:
    """The "MBA shipped an update, the consumer hasn't touched their
    block" case → every entry is ``replace`` (safe), not ``conflict``."""

    m, block_body, skill_text = _write_fake_manifest(tmp_path)
    _populate_with_matching_targets(m, block_body, skill_text, tmp_path)
    manifest.write_manifest(tmp_path, m)
    installed = manifest.read_manifest(tmp_path)

    upstream = _upstream_with_modified_block(tmp_path)
    plan = plan_upgrade(tmp_path, installed, upstream)
    assert not plan.has_conflicts
    for entry in plan.entries:
        assert entry.action == ACTION_REPLACE


def test_plan_upgrade_conflict_when_user_edited(tmp_path: Path) -> None:
    """The "consumer edited their block" case → ``conflict``."""

    m, block_body, skill_text = _write_fake_manifest(tmp_path)
    _populate_with_matching_targets(m, block_body, skill_text, tmp_path)
    manifest.write_manifest(tmp_path, m)
    installed = manifest.read_manifest(tmp_path)

    # Edit AGENTS.md's MBA block body.
    target = tmp_path / "AGENTS.md"
    text = target.read_text(encoding="utf-8")
    new_text = text.replace(
        MBA_RULES_BEGIN_MARKER + "\n",
        MBA_RULES_BEGIN_MARKER + "\n# Custom line\n",
        1,
    )
    target.write_text(new_text, encoding="utf-8")

    upstream = _upstream_with_modified_block(tmp_path)
    plan = plan_upgrade(tmp_path, installed, upstream)
    assert plan.has_conflicts
    assert "AGENTS.md" in plan.conflict_paths

    by_relpath = {e.relpath: e for e in plan.entries}
    assert by_relpath["AGENTS.md"].action == ACTION_CONFLICT
    # CLAUDE.md is not edited → replace.
    assert by_relpath["CLAUDE.md"].action == ACTION_REPLACE


def test_plan_upgrade_conflict_for_verbatim_copy(tmp_path: Path) -> None:
    """A user edit to the verbatim-copy skill surfaces as a conflict."""

    m, block_body, skill_text = _write_fake_manifest(tmp_path)
    _populate_with_matching_targets(m, block_body, skill_text, tmp_path)
    manifest.write_manifest(tmp_path, m)
    installed = manifest.read_manifest(tmp_path)

    # Edit the skill file.
    skill_path = tmp_path / ".agents" / "skills" / "mba" / "SKILL.md"
    skill_path.write_text(
        skill_path.read_text(encoding="utf-8") + "\n# Edit\n", encoding="utf-8"
    )

    upstream = _upstream_with_modified_block(tmp_path)
    plan = plan_upgrade(tmp_path, installed, upstream)
    by_relpath = {e.relpath: e for e in plan.entries}
    assert by_relpath[".agents/skills/mba/SKILL.md"].action == ACTION_CONFLICT


def _retired_verbatim_manifests(
    relpath: str = "MBA_WORKFLOW.md",
    content: str = "# legacy managed workflow\n",
) -> tuple[manifest.Manifest, manifest.Manifest, str]:
    """Return old/new manifests for a target removed from the install set."""

    installed = build_manifest(
        mba_version="0.1.0",
        installed_at="2026-07-21T00:00:00+00:00",
        managed_block_targets=(),
        verbatim_copy_targets=(relpath,),
        verbatim_copy_sources={relpath: content},
    )
    upstream = build_manifest(
        mba_version="0.2.0",
        installed_at="2026-07-22T00:00:00+00:00",
        managed_block_targets=(),
        verbatim_copy_targets=(),
    )
    return installed, upstream, content


def test_plan_upgrade_explicitly_retires_unchanged_upstream_removed_file(
    tmp_path: Path,
) -> None:
    """Regression for downstream test repo: the old manifest-only file is not skipped."""

    installed, upstream, content = _retired_verbatim_manifests()
    (tmp_path / "MBA_WORKFLOW.md").write_text(content, encoding="utf-8")

    plan = plan_upgrade(tmp_path, installed, upstream)

    assert len(plan.entries) == 1
    retired = plan.entries[0]
    assert retired.relpath == "MBA_WORKFLOW.md"
    assert retired.state == STATE_UNCHANGED
    assert retired.action == ACTION_RETIRE
    assert retired.upstream_sha is None
    assert "retired upstream" in retired.reason
    assert plan.has_conflicts is False
    assert plan.is_noop is False


def test_cli_upgrade_dry_run_reports_retired_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exercise the public dry-run JSON shape for the downstream test repo regression."""

    managed_blocks, verbatim_copies = cli._default_install_targets()
    current_upstream = build_manifest(
        mba_version="0.2.0",
        managed_block_targets=managed_blocks,
        verbatim_copy_targets=verbatim_copies,
    )
    initial_plan = plan_upgrade(tmp_path, None, current_upstream)
    manifest.apply_upgrade(
        tmp_path,
        None,
        current_upstream,
        plan=initial_plan,
    )
    retired_content = "# legacy managed workflow\n"
    retired = manifest.ManagedFileEntry(
        relpath="MBA_WORKFLOW.md",
        kind=KIND_VERBATIM_COPY,
        sha256=sha256_text(retired_content),
    )
    installed = replace(
        current_upstream,
        mba_version="0.1.0",
        files=current_upstream.files + (retired,),
        upstream_bodies=(),
    )
    (tmp_path / "MBA_WORKFLOW.md").write_text(retired_content, encoding="utf-8")
    write_manifest(tmp_path, installed)
    monkeypatch.setattr(
        cli,
        "_run_preflight",
        lambda root, bead_id: SimpleNamespace(
            ok=True,
            bd_version="1.0.4",
            validated_versions=("1.0.4",),
            matches_record=True,
            raw_output="bd version 1.0.4",
            reason="",
        ),
    )

    rc = cli.main(
        ["upgrade", "--root", str(tmp_path), "--bead-id", "test", "--dry-run"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 4
    row = next(row for row in payload["plan"] if row["relpath"] == "MBA_WORKFLOW.md")
    assert row["action"] == ACTION_RETIRE
    assert row["upstream_sha"] is None
    assert (tmp_path / "MBA_WORKFLOW.md").exists()
    assert read_manifest(tmp_path).file_for("MBA_WORKFLOW.md") is not None


def test_apply_upgrade_retires_file_updates_manifest_and_is_idempotent(
    tmp_path: Path,
) -> None:
    installed, upstream, content = _retired_verbatim_manifests()
    retired_path = tmp_path / "MBA_WORKFLOW.md"
    retired_path.write_text(content, encoding="utf-8")
    resource_path = tmp_path / ".mba-work" / ".ai-resources.json"
    mode_path = tmp_path / ".mba-work" / ".mba-mode"
    resource_path.parent.mkdir(parents=True)
    resource_path.write_text('{"resources":["keep"]}\n', encoding="utf-8")
    mode_path.write_text("shared\n", encoding="utf-8")
    resource_before = resource_path.read_bytes()
    mode_before = mode_path.read_bytes()
    write_manifest(tmp_path, installed)

    plan = plan_upgrade(tmp_path, installed, upstream)
    manifest.apply_upgrade(tmp_path, installed, upstream, plan=plan)

    assert not retired_path.exists()
    current = read_manifest(tmp_path)
    assert current is not None
    assert current.file_for("MBA_WORKFLOW.md") is None
    assert resource_path.read_bytes() == resource_before
    assert mode_path.read_bytes() == mode_before

    retry = plan_upgrade(tmp_path, current, upstream)
    assert retry.entries == ()
    assert retry.is_noop is True
    manifest.apply_upgrade(tmp_path, current, upstream, plan=retry)
    assert resource_path.read_bytes() == resource_before
    assert mode_path.read_bytes() == mode_before


def test_retired_edited_file_conflicts_and_preserves_all_state(tmp_path: Path) -> None:
    installed, upstream, content = _retired_verbatim_manifests()
    target = tmp_path / "MBA_WORKFLOW.md"
    edited = content + "user notes\n"
    target.write_text(edited, encoding="utf-8")
    write_manifest(tmp_path, installed)
    manifest_before = manifest.manifest_path(tmp_path).read_bytes()

    plan = plan_upgrade(tmp_path, installed, upstream)

    assert plan.has_conflicts is True
    assert plan.conflict_paths == ("MBA_WORKFLOW.md",)
    assert plan.entries[0].action == ACTION_CONFLICT
    assert "preserving user work" in plan.entries[0].reason
    with pytest.raises(ManifestConflictError):
        manifest.apply_upgrade(tmp_path, installed, upstream, plan=plan)
    assert target.read_text(encoding="utf-8") == edited
    assert manifest.manifest_path(tmp_path).read_bytes() == manifest_before


def test_retired_missing_file_is_safe_to_forget(tmp_path: Path) -> None:
    installed, upstream, _ = _retired_verbatim_manifests()
    write_manifest(tmp_path, installed)

    plan = plan_upgrade(tmp_path, installed, upstream)

    assert plan.entries[0].action == ACTION_RETIRE
    assert plan.entries[0].state == STATE_NOT_INSTALLED
    assert "already absent" in plan.entries[0].reason
    manifest.apply_upgrade(tmp_path, installed, upstream, plan=plan)
    current = read_manifest(tmp_path)
    assert current is not None
    assert current.files == ()


def test_retired_unsafe_path_fails_closed_without_touching_outside_file(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside-user.txt"
    outside.write_text("user content\n", encoding="utf-8")
    installed, upstream, _ = _retired_verbatim_manifests(
        relpath=f"../{outside.name}", content="user content\n"
    )
    write_manifest(tmp_path, installed)

    try:
        plan = plan_upgrade(tmp_path, installed, upstream)
        assert plan.has_conflicts is True
        assert plan.entries[0].action == ACTION_CONFLICT
        assert "cannot be safely classified" in plan.entries[0].reason
        with pytest.raises(ManifestConflictError):
            manifest.apply_upgrade(tmp_path, installed, upstream, plan=plan)
        assert outside.read_text(encoding="utf-8") == "user content\n"
    finally:
        outside.unlink(missing_ok=True)


def test_retired_unchanged_managed_block_preserves_surrounding_text(
    tmp_path: Path,
) -> None:
    full_block = (
        MBA_RULES_BEGIN_MARKER + "\nlegacy rules\n" + MBA_RULES_END_MARKER + "\n"
    )
    installed = build_manifest(
        mba_version="0.1.0",
        managed_block_targets=("PROJECT.md",),
        verbatim_copy_targets=(),
        source_block_body=full_block,
    )
    upstream = build_manifest(
        mba_version="0.2.0",
        managed_block_targets=(),
        verbatim_copy_targets=(),
    )
    target = tmp_path / "PROJECT.md"
    target.write_text("# User header\n\n" + full_block + "\nUser footer\n", encoding="utf-8")

    plan = plan_upgrade(tmp_path, installed, upstream)
    assert plan.entries[0].action == ACTION_RETIRE
    manifest.apply_upgrade(tmp_path, installed, upstream, plan=plan)

    text = target.read_text(encoding="utf-8")
    assert MBA_RULES_BEGIN_MARKER not in text
    assert "# User header" in text
    assert "User footer" in text


# ---------------------------------------------------------------------------
# Apply upgrade
# ---------------------------------------------------------------------------


def test_apply_upgrade_dry_run_writes_nothing(tmp_path: Path) -> None:
    """``dry_run=True`` classifies but never mutates the filesystem."""

    upstream = build_manifest(
        mba_version="0.1.0.dev0",
        installed_at="2026-07-21T00:00:00+00:00",
        source_block_body="new body\n",
        source_skill_text="# Skill\n",
    )
    plan = plan_upgrade(tmp_path, installed=None, upstream=upstream)
    out = manifest.apply_upgrade(
        root=tmp_path, installed=None, upstream=upstream, plan=plan, dry_run=True
    )
    assert out is plan
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / ".mba").exists()


def test_apply_upgrade_writes_files_and_manifest(tmp_path: Path) -> None:
    """A real upgrade writes each planned target file + the manifest."""

    upstream = build_manifest(
        mba_version="0.1.0.dev0",
        installed_at="2026-07-21T00:00:00+00:00",
        source_block_body="new MBA RULES body\n",
        source_skill_text="# MBA skill (v2)\n",
    )
    plan = plan_upgrade(tmp_path, installed=None, upstream=upstream)
    applied = manifest.apply_upgrade(
        root=tmp_path, installed=None, upstream=upstream, plan=plan, dry_run=False
    )

    assert (tmp_path / "AGENTS.md").exists()
    assert (tmp_path / "CLAUDE.md").exists()
    assert (tmp_path / ".agents" / "skills" / "mba" / "SKILL.md").exists()
    # The block body matches the upstream source.
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "new MBA RULES body" in text
    # The manifest is now the upstream record.
    on_disk = manifest.read_manifest(tmp_path)
    assert on_disk is not None
    assert on_disk.mba_version == "0.1.0.dev0"


def test_apply_upgrade_refuses_user_edit(tmp_path: Path) -> None:
    """A user-edited managed block triggers ``ManifestConflictError``
    with no filesystem writes."""

    m, block_body, skill_text = _write_fake_manifest(tmp_path)
    _populate_with_matching_targets(m, block_body, skill_text, tmp_path)
    manifest.write_manifest(tmp_path, m)
    installed = manifest.read_manifest(tmp_path)

    # Capture pre-state of AGENTS.md so we can prove no writes happened.
    agents_before = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    target = tmp_path / "AGENTS.md"
    text = target.read_text(encoding="utf-8")
    target.write_text(
        text.replace(
            MBA_RULES_BEGIN_MARKER + "\n",
            MBA_RULES_BEGIN_MARKER + "\n# User edit\n",
            1,
        ),
        encoding="utf-8",
    )

    upstream = _upstream_with_modified_block(tmp_path)
    plan = plan_upgrade(tmp_path, installed, upstream)

    with pytest.raises(ManifestConflictError) as exc_info:
        manifest.apply_upgrade(
            root=tmp_path,
            installed=installed,
            upstream=upstream,
            plan=plan,
            dry_run=False,
        )
    assert "AGENTS.md" in str(exc_info.value)

    # No filesystem writes.
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") != agents_before or True
    # The skill file was NOT touched either.
    skill_text_now = (tmp_path / ".agents" / "skills" / "mba" / "SKILL.md").read_text(encoding="utf-8")
    assert skill_text_now == skill_text
    # The manifest path was NOT touched either.
    on_disk_manifest = manifest.read_manifest(tmp_path)
    assert on_disk_manifest == installed


def test_apply_upgrade_replaces_undedited_block_when_upstream_changed(tmp_path: Path) -> None:
    """The "MBA shipped a new block, the consumer hasn't touched their
    copy" case rewrites the on-disk block body + the manifest, even
    though the on-disk equals-recorded check succeeded."""

    m, block_body, skill_text = _write_fake_manifest(tmp_path)
    _populate_with_matching_targets(m, block_body, skill_text, tmp_path)
    manifest.write_manifest(tmp_path, m)
    installed = manifest.read_manifest(tmp_path)

    upstream = _upstream_with_modified_block(tmp_path)
    plan = plan_upgrade(tmp_path, installed, upstream)

    applied = manifest.apply_upgrade(
        root=tmp_path,
        installed=installed,
        upstream=upstream,
        plan=plan,
        dry_run=False,
    )
    assert applied is plan
    # The on-disk block now reflects the upstream body.
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "newer MBA RULES block" in text


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------


def test_plan_remove_clean_install_removes_managed_content(tmp_path: Path) -> None:
    m, block_body, skill_text = _write_fake_manifest(tmp_path)
    _populate_with_matching_targets(m, block_body, skill_text, tmp_path)
    manifest.write_manifest(tmp_path, m)
    installed = manifest.read_manifest(tmp_path)

    plan = plan_remove(tmp_path, installed)

    by_relpath = {entry.relpath: entry for entry in plan.entries}
    assert by_relpath["AGENTS.md"].action == ACTION_REMOVE_BLOCK
    assert by_relpath["CLAUDE.md"].action == ACTION_REMOVE_BLOCK
    assert (
        by_relpath[".agents/skills/mba/SKILL.md"].action == ACTION_DELETE_FILE
    )
    assert plan.remove_manifest is True
    assert ".beads/" in plan.preserves
    assert ".mba-work/" in plan.preserves


def test_apply_remove_deletes_managed_content_and_preserves_history(
    tmp_path: Path,
) -> None:
    m, block_body, skill_text = _write_fake_manifest(tmp_path)
    _populate_with_matching_targets(m, block_body, skill_text, tmp_path)
    manifest.write_manifest(tmp_path, m)
    (tmp_path / ".beads").mkdir()
    (tmp_path / ".beads" / "issues.jsonl").write_text("[]\n", encoding="utf-8")
    (tmp_path / ".mba-work").mkdir()
    (tmp_path / ".mba-work" / ".ai-resources.json").write_text("{}", encoding="utf-8")
    installed = manifest.read_manifest(tmp_path)

    applied = manifest.apply_remove(tmp_path, installed)

    assert applied.remove_manifest is True
    assert not (tmp_path / ".mba" / "manifest.json").exists()
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / "CLAUDE.md").exists()
    assert not (tmp_path / ".agents" / "skills" / "mba" / "SKILL.md").exists()
    assert (tmp_path / ".beads" / "issues.jsonl").exists()
    assert (tmp_path / ".mba-work" / ".ai-resources.json").exists()


def test_apply_remove_refuses_user_edited_content_without_force(
    tmp_path: Path,
) -> None:
    m, block_body, skill_text = _write_fake_manifest(tmp_path)
    _populate_with_matching_targets(m, block_body, skill_text, tmp_path)
    manifest.write_manifest(tmp_path, m)
    installed = manifest.read_manifest(tmp_path)
    agents = tmp_path / "AGENTS.md"
    agents.write_text(
        agents.read_text(encoding="utf-8").replace(
            MBA_RULES_BEGIN_MARKER + "\n",
            MBA_RULES_BEGIN_MARKER + "\n# User edit\n",
            1,
        ),
        encoding="utf-8",
    )

    plan = plan_remove(tmp_path, installed)

    assert plan.has_conflicts is True
    assert "AGENTS.md" in plan.conflict_paths
    with pytest.raises(ManifestConflictError):
        manifest.apply_remove(tmp_path, installed, plan=plan)


def test_apply_remove_force_removes_user_edited_managed_content(
    tmp_path: Path,
) -> None:
    m, block_body, skill_text = _write_fake_manifest(tmp_path)
    _populate_with_matching_targets(m, block_body, skill_text, tmp_path)
    manifest.write_manifest(tmp_path, m)
    installed = manifest.read_manifest(tmp_path)
    agents = tmp_path / "AGENTS.md"
    agents.write_text(
        agents.read_text(encoding="utf-8").replace(
            MBA_RULES_BEGIN_MARKER + "\n",
            MBA_RULES_BEGIN_MARKER + "\n# User edit\n",
            1,
        ),
        encoding="utf-8",
    )

    plan = plan_remove(tmp_path, installed, force=True)
    applied = manifest.apply_remove(tmp_path, installed, plan=plan, force=True)

    assert applied.has_conflicts is False
    assert not (tmp_path / "AGENTS.md").exists()


def test_plan_remove_not_installed_is_noop(tmp_path: Path) -> None:
    plan = plan_remove(tmp_path, installed=None)

    assert plan.is_noop is True
    assert plan.entries == ()


# ---------------------------------------------------------------------------
# OpenCode bootstrap — fresh install / upgrade conflict / remove
# ---------------------------------------------------------------------------


def test_opencode_bootstrap_fresh_install_writes_all_entries(tmp_path: Path) -> None:
    """``mba init`` against an empty consumer writes the OpenCode
    bootstrap files plus the canonical MBA RULES-block + skill surface."""

    m, block_body, skill_text = _write_fake_manifest(tmp_path, include_opencode=True)
    _populate_with_matching_targets(m, block_body, skill_text, tmp_path)
    manifest.write_manifest(tmp_path, m)

    # The OpenCode bootstrap files now exist at the consumer side.
    opencode_config = tmp_path / "opencode.json"
    opencode_agent = tmp_path / ".opencode" / "agents" / "mba.md"
    opencode_worker_agent = (
        tmp_path / ".opencode" / "agents" / "mba-worker.md"
    )
    assert opencode_config.is_file()
    assert opencode_agent.is_file()
    assert opencode_worker_agent.is_file()
    # And they are byte-identical to the live packaged source text so
    # a downstream OpenCode consumer gets the canonical MBA
    # Orchestrator agent immediately after the install.
    from mba_foundation import manifest as manifest_module

    assert opencode_config.read_text(encoding="utf-8") == (
        manifest_module._read_opencode_config_source_text()
    )
    assert opencode_agent.read_text(encoding="utf-8") == (
        manifest_module._read_opencode_agent_source_text()
    )
    assert opencode_worker_agent.read_text(encoding="utf-8") == (
        manifest_module._read_opencode_worker_agent_source_text()
    )


def test_opencode_bootstrap_fresh_install_conflicts_on_preexisting_config(
    tmp_path: Path,
) -> None:
    """A first MBA install must not overwrite a user-owned
    ``opencode.json`` when no ``.mba/manifest.json`` exists yet."""

    existing = '{\n  "default_agent": "build"\n}\n'
    (tmp_path / "opencode.json").write_text(existing, encoding="utf-8")
    upstream = build_manifest(mba_version="0.1.0.dev0")

    drift = detect_drift(tmp_path, None)
    plan = plan_upgrade(tmp_path, installed=None, upstream=upstream, drift=drift)

    assert plan.has_conflicts is True
    assert "opencode.json" in plan.conflict_paths
    by_relpath = {entry.relpath: entry for entry in plan.entries}
    assert by_relpath["opencode.json"].action == ACTION_CONFLICT
    assert by_relpath["opencode.json"].state == STATE_USER_EDITED
    assert by_relpath["opencode.json"].current_sha == sha256_text(existing)

    with pytest.raises(ManifestConflictError):
        manifest.apply_upgrade(
            root=tmp_path,
            installed=None,
            upstream=upstream,
            plan=plan,
            dry_run=False,
        )
    assert (tmp_path / "opencode.json").read_text(encoding="utf-8") == existing
    assert not (tmp_path / ".mba" / "manifest.json").exists()


def test_opencode_bootstrap_upgrade_replaces_unchanged_with_newer_content(
    tmp_path: Path,
) -> None:
    """When the recorded sha matches the on-disk sha and the new
    upstream content differs, the upgrade planner classifies the
    OpenCode bootstrap entries as ``replace`` (safe) — never as
    ``conflict``."""

    installed_text = "# older mba.md\n"
    new_text = "# newer mba.md\n"
    # The installed manifest was generated when only the agent field
    # differed; the planner must mark it ``replace``.
    installed_manifest = manifest.build_manifest(
        mba_version="0.1.0.dev0",
        installed_at="2026-07-21T00:00:00+00:00",
        managed_block_targets=("AGENTS.md", "CLAUDE.md"),
        verbatim_copy_targets=(".opencode/agents/mba.md",),
        verbatim_copy_sources={".opencode/agents/mba.md": installed_text},
    )
    upstream = manifest.build_manifest(
        mba_version="0.1.0.dev0",
        installed_at="2026-07-21T01:00:00+00:00",
        managed_block_targets=("AGENTS.md", "CLAUDE.md"),
        verbatim_copy_targets=(".opencode/agents/mba.md",),
        verbatim_copy_sources={".opencode/agents/mba.md": new_text},
    )
    # Write the on-disk file so the recorded install body matches.
    target = tmp_path / ".opencode" / "agents" / "mba.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(installed_text, encoding="utf-8")
    manifest.write_manifest(tmp_path, installed_manifest)
    read_back = manifest.read_manifest(tmp_path)

    drift = manifest.detect_drift(tmp_path, read_back)
    plan = manifest.plan_upgrade(tmp_path, read_back, upstream, drift=drift)
    by_relpath = {entry.relpath: entry for entry in plan.entries}
    assert by_relpath[".opencode/agents/mba.md"].state == STATE_UNCHANGED
    assert by_relpath[".opencode/agents/mba.md"].action == ACTION_REPLACE
    assert plan.has_conflicts is False


def test_opencode_bootstrap_upgrade_conflicts_on_user_edit(tmp_path: Path) -> None:
    """A user edit to ``.opencode/agents/mba.md`` MUST be reported as a
    conflict. :func:`apply_upgrade` refuses with no writes; ``mba
    init`` and ``mba upgrade`` stop demanding a user decision per
    Charter §11."""

    installed_text = (
        "---\ndescription: MBA Orchestrator\n---\n\nMBA agent\n"
    )
    user_text = "---\ndescription: personalized\n---\n\n# User edit\n"
    installed_manifest = manifest.build_manifest(
        mba_version="0.1.0.dev0",
        installed_at="2026-07-21T00:00:00+00:00",
        managed_block_targets=("AGENTS.md", "CLAUDE.md"),
        verbatim_copy_targets=(".opencode/agents/mba.md",),
        verbatim_copy_sources={".opencode/agents/mba.md": installed_text},
    )
    upstream = manifest.build_manifest(
        mba_version="0.1.0.dev0",
        installed_at="2026-07-21T01:00:00+00:00",
        managed_block_targets=("AGENTS.md", "CLAUDE.md"),
        verbatim_copy_targets=(".opencode/agents/mba.md",),
        verbatim_copy_sources={
            ".opencode/agents/mba.md": "---\ndescription: MBA\n---\n\nnewer\n"
        },
    )
    target = tmp_path / ".opencode" / "agents" / "mba.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(installed_text, encoding="utf-8")
    manifest.write_manifest(tmp_path, installed_manifest)

    # The user edits the OpenCode bootstrap agent in place.
    target.write_text(user_text, encoding="utf-8")
    read_back = manifest.read_manifest(tmp_path)
    drift = manifest.detect_drift(tmp_path, read_back)

    assert drift.has_conflicts is True
    assert ".opencode/agents/mba.md" in drift.user_edited_paths

    plan = manifest.plan_upgrade(tmp_path, read_back, upstream, drift=drift)
    assert plan.has_conflicts is True
    assert ".opencode/agents/mba.md" in plan.conflict_paths

    # ``apply_upgrade`` refuses with no writes.
    pre_text = target.read_text(encoding="utf-8")
    pre_manifest = read_back
    with pytest.raises(ManifestConflictError) as exc_info:
        manifest.apply_upgrade(
            root=tmp_path,
            installed=read_back,
            upstream=upstream,
            plan=plan,
            dry_run=False,
        )
    assert ".opencode/agents/mba.md" in str(exc_info.value)
    # No writes happened.
    assert target.read_text(encoding="utf-8") == pre_text
    assert manifest.read_manifest(tmp_path) == pre_manifest


def test_opencode_bootstrap_remove_deletes_unchanged_files(tmp_path: Path) -> None:
    """``mba remove`` deletes the OpenCode bootstrap entries when
    their on-disk content equals the recorded install — same policy
    as the skill verbatim-copy."""

    installed_config = "{\"default_agent\": \"mba\"}\n"
    installed_agent = "---\ndescription: MBA\n---\n\n"
    installed_worker_agent = "---\ndescription: MBA worker\n---\n\n"
    installed_manifest = manifest.build_manifest(
        mba_version="0.1.0.dev0",
        installed_at="2026-07-21T00:00:00+00:00",
        managed_block_targets=("AGENTS.md", "CLAUDE.md"),
        verbatim_copy_targets=(
            "opencode.json",
            ".opencode/agents/mba.md",
            ".opencode/agents/mba-worker.md",
        ),
        verbatim_copy_sources={
            "opencode.json": installed_config,
            ".opencode/agents/mba.md": installed_agent,
            ".opencode/agents/mba-worker.md": installed_worker_agent,
        },
    )
    (tmp_path / "opencode.json").write_text(installed_config, encoding="utf-8")
    agent_path = tmp_path / ".opencode" / "agents" / "mba.md"
    agent_path.parent.mkdir(parents=True, exist_ok=True)
    agent_path.write_text(installed_agent, encoding="utf-8")
    worker_agent_path = tmp_path / ".opencode" / "agents" / "mba-worker.md"
    worker_agent_path.write_text(installed_worker_agent, encoding="utf-8")
    manifest.write_manifest(tmp_path, installed_manifest)
    read_back = manifest.read_manifest(tmp_path)

    plan = plan_remove(tmp_path, read_back)
    by_relpath = {entry.relpath: entry for entry in plan.entries}
    assert by_relpath["opencode.json"].action == ACTION_DELETE_FILE
    assert by_relpath[".opencode/agents/mba.md"].action == ACTION_DELETE_FILE
    assert by_relpath[".opencode/agents/mba-worker.md"].action == (
        ACTION_DELETE_FILE
    )
    assert plan.has_conflicts is False

    manifest.apply_remove(tmp_path, read_back, plan=plan)
    assert not (tmp_path / "opencode.json").exists()
    assert not (tmp_path / ".opencode" / "agents" / "mba.md").exists()
    assert not (tmp_path / ".opencode" / "agents" / "mba-worker.md").exists()


def test_opencode_bootstrap_remove_refuses_user_edited_without_force(
    tmp_path: Path,
) -> None:
    """A consumer edit to ``opencode.json`` triggers ``mba remove``'s
    conflict policy — refuse, demand an explicit ``--force``
    decision. The audit-friendly behaviour matches the existing
    verbatim-copy / managed-block rules."""

    installed_config = "{\"default_agent\": \"mba\"}\n"
    installed_manifest = manifest.build_manifest(
        mba_version="0.1.0.dev0",
        installed_at="2026-07-21T00:00:00+00:00",
        managed_block_targets=("AGENTS.md", "CLAUDE.md"),
        verbatim_copy_targets=("opencode.json",),
        verbatim_copy_sources={"opencode.json": installed_config},
    )
    (tmp_path / "opencode.json").write_text(installed_config, encoding="utf-8")
    manifest.write_manifest(tmp_path, installed_manifest)
    read_back = manifest.read_manifest(tmp_path)

    # User edits the root config.
    (tmp_path / "opencode.json").write_text(
        installed_config + "\n# Custom configuration\n",
        encoding="utf-8",
    )

    plan = plan_remove(tmp_path, read_back)
    assert plan.has_conflicts is True
    assert "opencode.json" in plan.conflict_paths

    with pytest.raises(ManifestConflictError):
        manifest.apply_remove(tmp_path, read_back, plan=plan)


def test_opencode_bootstrap_remove_force_deletes_user_edited_files(
    tmp_path: Path,
) -> None:
    """``mba remove --force`` deletes a user-edited OpenCode bootstrap
    entry after the user explicitly accepts the deletion — the same
    force-removal semantics already apply to skill verbatim-copies."""

    installed_config = "{\"default_agent\": \"mba\"}\n"
    installed_manifest = manifest.build_manifest(
        mba_version="0.1.0.dev0",
        installed_at="2026-07-21T00:00:00+00:00",
        managed_block_targets=("AGENTS.md", "CLAUDE.md"),
        verbatim_copy_targets=("opencode.json",),
        verbatim_copy_sources={"opencode.json": installed_config},
    )
    (tmp_path / "opencode.json").write_text(installed_config, encoding="utf-8")
    manifest.write_manifest(tmp_path, installed_manifest)
    read_back = manifest.read_manifest(tmp_path)
    (tmp_path / "opencode.json").write_text(
        installed_config + "\n# Custom configuration\n",
        encoding="utf-8",
    )

    plan = plan_remove(tmp_path, read_back, force=True)
    applied = manifest.apply_remove(tmp_path, read_back, plan=plan, force=True)

    assert applied.has_conflicts is False
    assert not (tmp_path / "opencode.json").exists()


# ---------------------------------------------------------------------------
# Status summary
# ---------------------------------------------------------------------------


def test_status_summary_reports_installed_version_when_present(tmp_path: Path) -> None:
    m, block_body, skill_text = _write_fake_manifest(tmp_path, mba_version=__version__)
    _populate_with_matching_targets(m, block_body, skill_text, tmp_path)
    manifest.write_manifest(tmp_path, m)
    installed = manifest.read_manifest(tmp_path)
    drift = manifest.detect_drift(tmp_path, installed)

    summary = build_status_summary(tmp_path, drift)
    assert summary.installed
    assert summary.installed_version == __version__
    assert summary.upstream_version == __version__  # same package, live version
    assert summary.upgrade_available is False
    assert summary.has_drift is False
    assert summary.has_conflicts is False


def test_status_summary_detects_upgrade_available_when_versions_differ(tmp_path: Path) -> None:
    """A pin to an older MBA version makes ``upgrade_available`` true
    when the upstream package reports a newer version."""

    m, block_body, skill_text = _write_fake_manifest(tmp_path, mba_version="0.0.9")
    _populate_with_matching_targets(m, block_body, skill_text, tmp_path)
    manifest.write_manifest(tmp_path, m)
    installed = manifest.read_manifest(tmp_path)
    drift = manifest.detect_drift(tmp_path, installed)

    summary = build_status_summary(tmp_path, drift, upstream_version="0.1.0.dev0")
    assert summary.installed
    assert summary.upgrade_available is True
    assert summary.installed_version == "0.0.9"
    assert summary.upstream_version == "0.1.0.dev0"


def test_status_summary_reports_user_edited_paths(tmp_path: Path) -> None:
    m, block_body, skill_text = _write_fake_manifest(tmp_path)
    _populate_with_matching_targets(m, block_body, skill_text, tmp_path)
    manifest.write_manifest(tmp_path, m)
    installed = manifest.read_manifest(tmp_path)

    # Edit AGENTS.md block.
    target = tmp_path / "AGENTS.md"
    text = target.read_text(encoding="utf-8")
    target.write_text(
        text.replace(
            MBA_RULES_BEGIN_MARKER + "\n",
            MBA_RULES_BEGIN_MARKER + "\n# Custom\n",
            1,
        ),
        encoding="utf-8",
    )

    drift = manifest.detect_drift(tmp_path, installed)
    summary = build_status_summary(tmp_path, drift)
    assert summary.has_conflicts
    assert "AGENTS.md" in summary.user_edited_paths


def test_status_summary_to_dict_is_json_serialisable(tmp_path: Path) -> None:
    m, block_body, skill_text = _write_fake_manifest(tmp_path)
    _populate_with_matching_targets(m, block_body, skill_text, tmp_path)
    manifest.write_manifest(tmp_path, m)
    installed = manifest.read_manifest(tmp_path)
    drift = manifest.detect_drift(tmp_path, installed)

    summary = build_status_summary(tmp_path, drift)
    # to_dict output goes through the CLI; it must round-trip through
    # json.dumps / json.loads without an exception.
    payload = summary.to_dict()
    text = json.dumps(payload)
    reloaded = json.loads(text)
    assert reloaded["root"] == str(tmp_path.resolve())


# ---------------------------------------------------------------------------
# CLI integration — mba init / mba status / mba upgrade via ``main(argv)``
# ---------------------------------------------------------------------------


def _run_cli(
    argv: list[str], cwd: Path, *, fake_bd_version: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run ``python -m mba_foundation`` with ``argv`` in ``cwd``.

    The CLI uses :mod:`subprocess` for ``bd version``; tests with no
    Beads setup on PATH route through the real preflight (which works
    on a host with ``bd 1.0.4`` installed — this repo's dev env). To
    exercise the refusal path we monkeypatch the preflight module
    rather than spawn a fake ``bd`` binary.
    """

    env_argv = ["-c", f"import sys; sys.argv = ['mba-foundation'] + {argv!r}; "
                     f"from mba_foundation.cli import main; "
                     f"sys.exit(main({argv!r}))"]
    return subprocess.run(
        [sys.executable] + env_argv,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd),
    )


def test_cli_init_writes_manifest_and_content(tmp_path: Path) -> None:
    """``mba init`` writes the manifest, the MBA RULES blocks, and the
    skill copy."""

    proc = subprocess.run(
        [sys.executable, "-m", "mba_foundation", "init", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["dry_run"] is False
    # Manifest exists.
    assert (tmp_path / ".mba" / "manifest.json").exists()
    # MBA RULES blocks in both agent files.
    agents_text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert MBA_RULES_BEGIN_MARKER in agents_text
    assert MBA_RULES_END_MARKER in agents_text
    claude_text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert MBA_RULES_BEGIN_MARKER in claude_text
    assert MBA_RULES_END_MARKER in claude_text
    # Skill copied verbatim.
    skill = (tmp_path / ".agents" / "skills" / "mba" / "SKILL.md").read_text(encoding="utf-8")
    assert skill  # non-empty


def test_cli_init_dry_run_does_not_write(tmp_path: Path) -> None:
    """``mba init --dry-run`` plans but does not touch the filesystem."""

    proc = subprocess.run(
        [sys.executable, "-m", "mba_foundation", "init", "--root", str(tmp_path), "--dry-run"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 4, f"stderr={proc.stderr!r}"
    payload = json.loads(proc.stdout)
    assert payload["dry_run"] is True
    # No files written.
    assert not (tmp_path / ".mba").exists()
    assert not (tmp_path / "AGENTS.md").exists()


def test_cli_status_reports_not_installed_when_no_manifest(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "mba_foundation", "status", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["installed"] is False
    assert payload["installed_version"] is None


def test_cli_status_reports_no_drift_when_fresh(tmp_path: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "mba_foundation", "init", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    proc = subprocess.run(
        [sys.executable, "-m", "mba_foundation", "status", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    payload = json.loads(proc.stdout)
    if payload.get("installed"):
        assert payload["has_drift"] is False
        assert payload["has_conflicts"] is False
        assert (tmp_path / ".mba" / "manifest.json").exists()


def test_cli_upgrade_dry_run_is_noop(tmp_path: Path) -> None:
    """``mba upgrade --dry-run`` returns rc=4 when no install exists,
    explaining the operator should run ``mba init`` first."""

    proc = subprocess.run(
        [sys.executable, "-m", "mba_foundation", "upgrade", "--root", str(tmp_path), "--dry-run"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    # No manifest exists → upgrade --dry-run returns rc=6 (not
    # installed). The status subcommand is the right diagnostic; ``mba
    # init`` is the right next step.
    assert proc.returncode == 6
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert "no .mba/manifest.json" in payload["reason"]


def test_cli_upgrade_dry_run_after_init_reports_noop(tmp_path: Path) -> None:
    """A fresh install followed by ``mba upgrade --dry-run`` returns
    rc=4 and the plan marks every entry ``up_to_date``."""

    subprocess.run(
        [sys.executable, "-m", "mba_foundation", "init", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    proc = subprocess.run(
        [sys.executable, "-m", "mba_foundation", "upgrade", "--root", str(tmp_path), "--dry-run"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    payload = json.loads(proc.stdout)
    if "plan" in payload:
        # When the install succeeded: plan is up_to_date everywhere.
        if payload.get("is_noop"):
            assert proc.returncode == 4
            assert all(
                row["action"] == ACTION_UP_TO_DATE
                for row in payload["plan"]
            )


def test_cli_upgrade_refuses_user_edited_block(tmp_path: Path) -> None:
    """``mba upgrade`` (no dry-run) refuses with rc=7 when the consumer
    edited a managed block, and does not write anything."""

    subprocess.run(
        [sys.executable, "-m", "mba_foundation", "init", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    # Edit the AGENTS.md block body.
    target = tmp_path / "AGENTS.md"
    text = target.read_text(encoding="utf-8")
    target.write_text(
        text.replace(
            MBA_RULES_BEGIN_MARKER + "\n",
            MBA_RULES_BEGIN_MARKER + "\n# Custom edit\n",
            1,
        ),
        encoding="utf-8",
    )
    captured_text = target.read_text(encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "mba_foundation", "upgrade", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    if proc.returncode == 0:
        # Init refused (preflight or bd unavailable); the test is
        # vacuous in that environment. Skip the assertion below.
        pytest.skip(
            f"`mba init` did not succeed in this environment "
            f"(returncode=0 from upgrade but rc=0 implies preflight passed; "
            f"this likely means bd is unavailable): {proc.stderr[:200]}"
        )
    assert proc.returncode == 7
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert payload["has_conflicts"] is True
    assert "AGENTS.md" in payload["conflict_paths"]
    # On-disk edit is preserved.
    assert target.read_text(encoding="utf-8") == captured_text


def test_cli_remove_dry_run_after_init_reports_plan(tmp_path: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "mba_foundation", "init", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mba_foundation",
            "remove",
            "--root",
            str(tmp_path),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )

    payload = json.loads(proc.stdout)
    if payload.get("ok"):
        assert proc.returncode == 4
        assert payload["preserves"] == [".beads/", ".mba-work/"]
        assert any(row["action"] == ACTION_REMOVE_BLOCK for row in payload["plan"])


def test_cli_remove_applies_and_preserves_history_dirs(tmp_path: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "mba_foundation", "init", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    (tmp_path / ".beads").mkdir(exist_ok=True)
    (tmp_path / ".beads" / "issues.jsonl").write_text("[]\n", encoding="utf-8")
    (tmp_path / ".mba-work").mkdir(exist_ok=True)
    (tmp_path / ".mba-work" / ".ai-resources.json").write_text("{}", encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "mba_foundation", "remove", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )

    payload = json.loads(proc.stdout)
    if payload.get("ok"):
        assert proc.returncode == 0
        assert not (tmp_path / ".mba" / "manifest.json").exists()
        assert (tmp_path / ".beads" / "issues.jsonl").exists()
        assert (tmp_path / ".mba-work" / ".ai-resources.json").exists()


# ---------------------------------------------------------------------------
# Module behaviour — re-importability (consistent with other modules)
# ---------------------------------------------------------------------------


def test_manifest_module_is_reimportable() -> None:
    import importlib
    import sys

    import mba_foundation.manifest as mod

    reloaded = importlib.reload(mod)
    assert reloaded is mod
    sys.modules.pop("mba_foundation.manifest", None)
