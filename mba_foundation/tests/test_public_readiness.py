"""Public-readiness tests — pins the copy-and-use surface for `example-public`.

These tests assert that a fresh checkout is shippable to a new project
or person: the LICENSE exists, the metadata URLs are correct, the
packaging data files cover every public doc + the LICENSE + the MBA
skill, the installed MBA RULES block references only those packaged
docs, the installed SKILL.md references only those packaged docs, and
``mba init`` writes the canonical install surface into a fresh target.

The tests are hermetic (filesystem + regex; no ``pip`` install, no
network) except where the test needs to drive the actual ``mba init``
CLI to prove the end-to-end install surface; that path is conditioned on
``bd`` being on PATH and preflight accepting the installed version.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
MANIFEST_IN_PATH = PROJECT_ROOT / "MANIFEST.in"
LICENSE_PATH = PROJECT_ROOT / "LICENSE"
USER_GUIDE_PATH = PROJECT_ROOT / "docs" / "USER_GUIDE.md"
MBA_SKILL_SOURCE_PATH = (
    PROJECT_ROOT / "mba_foundation" / "resources" / "skills" / "mba" / "SKILL.md"
)
MBA_SKILL_SOURCE_RELPATH = "mba_foundation/resources/skills/mba/SKILL.md"


PUBLIC_DOC_PATHS: tuple[str, ...] = (
    "README.md",
    "LICENSE",
    "docs/mba/README.md",
    "docs/mba/charter.md",
    "docs/mba/assets/glm-cheatsheet.svg",
    "docs/mba/assets/glm-diagram.svg",
    "docs/mba/assets/glm-guide.html",
    "docs/mba/assets/minimax-cheatsheet.svg",
    "docs/mba/assets/minimax-diagram.svg",
    "docs/mba/assets/minimax-guide.html",
    "docs/mba/non-technical-flow.md",
    "docs/mba/technical-flow.md",
    "docs/mba/startup-setup.md",
    "docs/mba/roadmap.md",
    "docs/mba/implementation-status.md",
    "docs/beads/capabilities.md",
    "docs/beads/evaluation.md",
    "docs/USER_GUIDE.md",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# LICENSE
# ---------------------------------------------------------------------------


def test_license_file_exists_at_repo_root() -> None:
    """The package declares MIT in pyproject.toml; the file must exist."""

    assert LICENSE_PATH.is_file(), f"missing {LICENSE_PATH}"
    text = _read(LICENSE_PATH)
    # Real MIT licence text anchors (case-insensitive).
    assert "MIT License" in text or "MIT licence" in text
    assert "Copyright (c)" in text
    assert "Permission is hereby granted, free of charge" in text
    assert "THE SOFTWARE IS PROVIDED" in text


def test_license_author_matches_package_metadata() -> None:
    """The LICENSE copyright holder matches the pyproject author."""

    pyproject = _read(PYPROJECT_PATH)
    license_text = _read(LICENSE_PATH)
    # The package declares one author named Khubaeb.
    assert "Khubaeb" in pyproject
    # The LICENSE must carry the same name (or the year prefix is
    # acceptable; we only assert the holder name is present).
    assert "Khubaeb" in license_text


# ---------------------------------------------------------------------------
# Project URLs — fix the public remote
# ---------------------------------------------------------------------------


def test_pyproject_urls_point_to_khubaeb_remote() -> None:
    text = _read(PYPROJECT_PATH)
    # The legacy `anomalyco/...` URLs must not appear in package metadata.
    assert "anomalyco/MultipleBeadedAgents" not in text, (
        "pyproject.toml still references the legacy anomalyco remote"
    )
    # The intended remote must appear for both Homepage and Issues.
    assert "https://github.com/Khubaeb/MultipleBeadedAgents" in text
    assert "https://github.com/Khubaeb/MultipleBeadedAgents/issues" in text


# ---------------------------------------------------------------------------
# Package contents — pyproject + MANIFEST.in cover every public doc + LICENSE
# ---------------------------------------------------------------------------


def _declared_in_pyproject_data_files(path: str) -> bool:
    """True iff ``path`` appears (quoted) in the [tool.setuptools.data-files] block."""

    text = _read(PYPROJECT_PATH)
    # Isolate the data-files block; the body lists each path as a quoted
    # string on its own line.
    match = re.search(
        r"\[tool\.setuptools\.data-files\](.*?)(?=\n\[|\Z)", text, re.DOTALL
    )
    assert match is not None, "pyproject.toml missing [tool.setuptools.data-files]"
    body = match.group(1)
    return bool(re.search(rf'"{re.escape(path)}"', body))


def _declared_in_manifest_in(path: str) -> bool:
    """True iff ``path`` appears in MANIFEST.in (any include/recursive-include form)."""

    text = _read(MANIFEST_IN_PATH)
    if not MANIFEST_IN_PATH.is_file():
        return False
    # Quoted form or bare form, with optional recursive-include prefix.
    quoted = re.search(rf'\b(?:include|recursive-include)\s+"?{re.escape(path)}"?', text)
    bare = re.search(rf'\binclude\s+{re.escape(path)}\b', text)
    return bool(quoted or bare)


@pytest.mark.parametrize("relpath", PUBLIC_DOC_PATHS)
def test_public_doc_is_packaged(relpath: str) -> None:
    """Every public doc, LICENSE, and MBA skill appears in BOTH the
    pyproject data-files list AND MANIFEST.in — a fresh checkout must
    ship identical content via both metadata routes."""

    assert _declared_in_pyproject_data_files(relpath), (
        f"{relpath!r} missing from pyproject.toml [tool.setuptools.data-files]"
    )
    assert _declared_in_manifest_in(relpath), (
        f"{relpath!r} missing from MANIFEST.in"
    )


def test_mba_skill_is_in_pyproject_data_files() -> None:
    text = _read(PYPROJECT_PATH)
    assert '"share/multiple-beaded-agents/skills/mba"' in text, (
        "pyproject.toml missing the MBA skill data-files entry"
    )
    assert MBA_SKILL_SOURCE_RELPATH in text


def test_mba_skill_is_in_manifest_in() -> None:
    text = _read(MANIFEST_IN_PATH)
    assert MBA_SKILL_SOURCE_RELPATH in text


def test_pyproject_and_manifest_in_have_same_public_doc_set() -> None:
    """Both metadata files list the same set of public docs + LICENSE."""

    pyproject_set = {
        p
        for p in PUBLIC_DOC_PATHS
        if _declared_in_pyproject_data_files(p)
    }
    manifest_set = {
        p for p in PUBLIC_DOC_PATHS if _declared_in_manifest_in(p)
    }
    assert pyproject_set == manifest_set, (
        "pyproject and MANIFEST.in disagree on public-doc set: "
        f"only_in_pyproject={sorted(pyproject_set - manifest_set)}; "
        f"only_in_manifest={sorted(manifest_set - pyproject_set)}"
    )


def test_public_docs_all_exist_on_disk() -> None:
    """Every path declared in pyproject data-files actually exists."""

    for relpath in PUBLIC_DOC_PATHS:
        full = PROJECT_ROOT / relpath
        assert full.is_file(), f"declared public doc missing: {relpath}"


def test_public_facing_sources_do_not_expose_private_dev_trail() -> None:
    """The public release carries product docs and examples, not this
    dev repo's private Beads/worker trail."""

    scanned: list[Path] = [
        PROJECT_ROOT / "README.md",
        *(PROJECT_ROOT / "docs").rglob("*"),
        *(PROJECT_ROOT / "mba_foundation" / "resources").rglob("*"),
    ]
    private_bead = re.compile(r"\bmba-[a-z0-9]{3}(?:\.[0-9]+)?\b")
    leaks: list[str] = []
    for path in scanned:
        if not path.is_file():
            continue
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        text = _read(path)
        if "downstream test repo" in text:
            leaks.append(f"{rel}: downstream test repo")
        match = private_bead.search(text)
        if match:
            leaks.append(f"{rel}: {match.group(0)}")
        private_work_prefix = ".mba-work/" + "mba-"
        if private_work_prefix in text:
            leaks.append(f"{rel}: private .mba-work path")

    assert not leaks, "private dev trail leaked into public surface: " + "; ".join(leaks)


def test_install_manifest_sources_are_public_package_files() -> None:
    from mba_foundation.product_boundary import install_content_verbatim_copy_targets

    expected = {
        "docs/beads/capabilities.md",
        "docs/mba/charter.md",
        ".agents/skills/mba/SKILL.md",
        "opencode.json",
        ".opencode/agents/mba.md",
        ".opencode/agents/mba-worker.md",
    }
    assert set(install_content_verbatim_copy_targets()) == expected
    for relpath in expected - {
        ".agents/skills/mba/SKILL.md",
        "opencode.json",
        ".opencode/agents/mba.md",
        ".opencode/agents/mba-worker.md",
    }:
        assert relpath in PUBLIC_DOC_PATHS
        assert _declared_in_pyproject_data_files(relpath)
        assert _declared_in_manifest_in(relpath)
    assert MBA_SKILL_SOURCE_RELPATH in _read(MANIFEST_IN_PATH)
    assert MBA_SKILL_SOURCE_RELPATH in _read(PYPROJECT_PATH)
    # The OpenCode bootstrap sources ship as ``mba_foundation``
    # package data (resolved by ``importlib.resources`` at install
    # time) and are listed under ``[tool.setuptools.package-data]``,
    # not under the docs data-files block. The package-data entry is
    # repo-relative on the ``MANIFEST.in`` side and
    # package-relative (without the ``mba_foundation/`` prefix) on
    # the ``pyproject.toml`` side, so the assertions use the right
    # form for each metadata file.
    for pyproject_relpath, manifest_in_relpath in (
        (
            "resources/opencode/opencode.json",
            "mba_foundation/resources/opencode/opencode.json",
        ),
        (
            "resources/opencode/agents/mba.md",
            "mba_foundation/resources/opencode/agents/mba.md",
        ),
        (
            "resources/opencode/agents/mba-worker.md",
            "mba_foundation/resources/opencode/agents/mba-worker.md",
        ),
    ):
        assert pyproject_relpath in _read(PYPROJECT_PATH), (
            f"{pyproject_relpath!r} must be declared under "
            "[tool.setuptools.package-data] so a `pip install` consumer "
            "can resolve the source text via importlib.resources."
        )
        assert manifest_in_relpath in _read(MANIFEST_IN_PATH), (
            f"{manifest_in_relpath!r} must be declared in MANIFEST.in."
        )


def test_opencode_bootstrap_packaged_sources_exist_and_resolve() -> None:
    """The OpenCode bootstrap source files ship in the package
    and are readable via ``importlib.resources`` so an installed
    downstream can resolve the verbatim-copy text without a source
    checkout."""

    from mba_foundation import manifest as manifest_module

    config = manifest_module._read_opencode_config_source_text()
    agent = manifest_module._read_opencode_agent_source_text()
    worker_agent = manifest_module._read_opencode_worker_agent_source_text()
    # The bootstrap content pins the cold-start gate so an installed
    # OpenCode consumer cannot silently regress to bare
    # `mba-runtime first-contact`.
    assert '"default_agent": "mba"' in config
    assert '"AGENTS.md"' in config
    assert "python -m mba_runtime first-contact --cwd . --apply-setup" in agent
    assert "bd version" in agent
    assert "Do not run `python -m mba_runtime first-contact" in worker_agent
    assert "You are not the Orchestrator" in worker_agent


def test_opencode_bootstrap_agent_file_exists_and_is_packaged() -> None:
    """``mba_foundation/resources/opencode/agents/mba.md`` must
    physically exist on disk and be declared in both packaging
    metadata files so a wheel install surfaces the same content."""

    relpath = "mba_foundation/resources/opencode/agents/mba.md"
    assert (PROJECT_ROOT / relpath).is_file(), f"{relpath} missing on disk"
    assert relpath in _read(MANIFEST_IN_PATH)
    assert "resources/opencode/agents/mba.md" in _read(PYPROJECT_PATH)


def test_opencode_worker_agent_file_exists_and_is_packaged() -> None:
    """``mba_foundation/resources/opencode/agents/mba-worker.md``
    must physically exist and be package data so downstream worker
    launches can bypass the ``mba`` Orchestrator default agent."""

    relpath = "mba_foundation/resources/opencode/agents/mba-worker.md"
    assert (PROJECT_ROOT / relpath).is_file(), f"{relpath} missing on disk"
    assert relpath in _read(MANIFEST_IN_PATH)
    assert "resources/opencode/agents/mba-worker.md" in _read(PYPROJECT_PATH)


def test_opencode_bootstrap_root_config_exists_and_is_packaged() -> None:
    """``mba_foundation/resources/opencode/opencode.json`` must
    physically exist on disk and be declared in both packaging
    metadata files so a wheel install surfaces the same content."""

    relpath = "mba_foundation/resources/opencode/opencode.json"
    assert (PROJECT_ROOT / relpath).is_file(), f"{relpath} missing on disk"
    assert relpath in _read(MANIFEST_IN_PATH)
    assert "resources/opencode/opencode.json" in _read(PYPROJECT_PATH)


def test_opencode_bootstrap_packaged_sources_have_yaml_front_matter() -> None:
    """The agent file uses ``---`` YAML front-matter so downstream
    OpenCode tooling can detect the agent description. The root
    config is plain JSON. Both ship as UTF-8 with LF line endings so
    the recorded sha matches across platforms."""

    from mba_foundation import manifest as manifest_module

    config_text = manifest_module._read_opencode_config_source_text()
    agent_text = manifest_module._read_opencode_agent_source_text()
    worker_agent_text = manifest_module._read_opencode_worker_agent_source_text()
    assert "{" in config_text and "}" in config_text  # JSON object
    assert agent_text.startswith("---\n"), (
        "OpenCode bootstrap agent must start with YAML front-matter"
    )
    assert "default_agent" in config_text
    assert "instructions" in config_text
    # The bootstrap agent mandates the deterministic cold-start gate
    # before any project exploration or worker launch.
    assert "first-contact" in agent_text
    assert "blocked" in agent_text.lower()
    assert worker_agent_text.startswith("---\n"), (
        "OpenCode worker agent must start with YAML front-matter"
    )
    assert "mode: primary" in worker_agent_text
    assert "You are not the Orchestrator" in worker_agent_text


# ---------------------------------------------------------------------------
# Verbatim-copy sources — installed path preserves repo-relative layout
# ---------------------------------------------------------------------------


# Every setuptools data-files destination we use. Setuptools installs a
# source ``foo/bar.md`` listed under destination ``dest`` at
# ``<prefix>/dest/bar.md`` (basename only), so the destination must
# already include the parent directory the runtime expects
# (``share/doc/multiple-beaded-agents/<dir_of(relpath)>``).
PACKAGED_DOCS_ROOT = "share/doc/multiple-beaded-agents"


def _data_files_destinations() -> dict[str, list[str]]:
    """Parse ``pyproject.toml [tool.setuptools.data-files]``.

    Returns a ``{destination: [source, ...]}`` mapping reflecting the
    real layout. We parse by hand (rather than via ``tomllib``) because
    the test target runs on Python 3.10+ and ``tomllib`` is only stdlib
    from 3.11.
    """

    text = _read(PYPROJECT_PATH)
    block = re.search(
        r"\[tool\.setuptools\.data-files\](.*?)(?=\n\[|\Z)", text, re.DOTALL
    )
    assert block is not None, "pyproject.toml missing [tool.setuptools.data-files]"
    body = block.group(1)
    entries: dict[str, list[str]] = {}
    for dest_match in re.finditer(r'"([^"]+)"\s*=\s*\[(.*?)\]', body, re.DOTALL):
        dest = dest_match.group(1)
        inner = dest_match.group(2)
        sources = re.findall(r'"([^"]+)"', inner)
        entries.setdefault(dest, []).extend(sources)
    return entries


def test_packaged_docs_preserve_repo_relative_subdirectories() -> None:
    """Every public doc lands at ``<dest>/<repo_relpath>`` so the
    runtime's :func:`_read_verbatim_copy_source_text` lookup
    (``_packaged_data_root() / relpath``) resolves the right file.

    Setuptools flattens each data-files source to its basename, so a
    source ``docs/mba/charter.md`` only lands at the right place when
    its destination already ends in ``docs/mba``. Flattening (e.g. one
    single ``share/doc/multiple-beaded-agents`` block) silently makes
    the runtime raise ``verbatim-copy source not found`` on a
    packaged install (regression: Downstream dry-run failure on r3).
    """

    destinations = _data_files_destinations()
    for relpath in PUBLIC_DOC_PATHS:
        dest = next(
            (d for d, sources in destinations.items() if relpath in sources),
            None,
        )
        assert dest is not None, (
            f"{relpath!r} missing from pyproject data-files"
        )
        parent = relpath.rsplit("/", 1)[0] if "/" in relpath else ""
        expected_dest = (
            f"{PACKAGED_DOCS_ROOT}/{parent}" if parent else PACKAGED_DOCS_ROOT
        )
        assert dest == expected_dest, (
            f"{relpath!r} listed under destination {dest!r}; "
            f"runtime expects to find it at <prefix>/{expected_dest}/{relpath}. "
            f"Setuptools flattens the basename, so the destination must "
            f"already include {parent!r}."
        )


def test_packaged_verbatim_copy_targets_resolve_at_repo_relative_path() -> None:
    """The verbatim-copy sources the install copies byte-for-byte
    (``docs/mba/charter.md`` and ``docs/beads/capabilities.md``) must
    land at the exact path
    ``_read_verbatim_copy_source_text(relpath)`` will probe, both under
    the system prefix (``sysconfig.get_path('data')``) and the user
    base (``site.getuserbase()``).

    Combined with
    :func:`mba_foundation.manifest._packaged_data_root` and
    :func:`mba_foundation.manifest._user_site_data_root`, this pins
    that ``pip install`` (system or ``--user``) places every verbatim
    source at ``<prefix>/share/doc/multiple-beaded-agents/<relpath>``.

    The OpenCode bootstrap sources
    (``opencode.json``, ``.opencode/agents/mba.md``) use a separate
    resolution path — :func:`_read_packaged_resource_text` reads
    them via ``importlib.resources`` from the installed
    ``mba_foundation`` package data — so they are out of scope for
    this test and are covered separately by
    ``test_opencode_bootstrap_packaged_sources_exist_and_resolve``.
    """

    from mba_foundation import manifest as manifest_module
    from mba_foundation.product_boundary import install_content_verbatim_copy_targets

    doc_targets = tuple(
        relpath
        for relpath in install_content_verbatim_copy_targets()
        if relpath
        not in (
            ".agents/skills/mba/SKILL.md",
            "opencode.json",
            ".opencode/agents/mba.md",
            ".opencode/agents/mba-worker.md",
        )
    )
    assert doc_targets, "verbatim-copy targets missing"
    destinations = _data_files_destinations()

    system_prefix = manifest_module.Path(
        manifest_module.sysconfig.get_path("data")
    )
    user_prefix = manifest_module.Path(manifest_module.site.getuserbase())

    repo_root = PROJECT_ROOT
    for relpath in doc_targets:
        source = repo_root / relpath
        assert source.is_file(), (
            f"verbatim-copy source missing from repo: {relpath}"
        )
        parent = relpath.rsplit("/", 1)[0] if "/" in relpath else ""
        expected_dest = f"{PACKAGED_DOCS_ROOT}/{parent}" if parent else PACKAGED_DOCS_ROOT
        listed_under = [
            d for d, sources in destinations.items() if relpath in sources
        ]
        assert expected_dest in listed_under, (
            f"verbatim-copy source {relpath!r} must be listed under "
            f"destination {expected_dest!r} (found under: {listed_under!r}). "
            f"Runtime looks at <data>/{expected_dest}/{relpath}."
        )
        system_path = system_prefix / expected_dest / relpath.rsplit("/", 1)[-1]
        user_path = user_prefix / expected_dest / relpath.rsplit("/", 1)[-1]
        expected_system_path = system_prefix / PACKAGED_DOCS_ROOT / relpath
        expected_user_path = user_prefix / PACKAGED_DOCS_ROOT / relpath
        assert system_path == expected_system_path, (
            f"system-prefix install path mismatch for {relpath!r}"
        )
        assert user_path == expected_user_path, (
            f"user-site install path mismatch for {relpath!r}"
        )


# ---------------------------------------------------------------------------
# User docs — docs/USER_GUIDE.md covers the five required topics
# ---------------------------------------------------------------------------


def test_user_guide_exists_and_covers_required_topics() -> None:
    assert USER_GUIDE_PATH.is_file(), "docs/USER_GUIDE.md missing"
    text = _read(USER_GUIDE_PATH)
    required_topics = (
        ("install", ["install", "pip install"]),
        ("target-project use", ["initialise a target", "mba init"]),
        ("status", ["mba status"]),
        ("upgrade", ["mba upgrade"]),
        ("update path", ["upstream", "Khubaeb/MultipleBeadedAgents"]),
    )
    for label, needles in required_topics:
        for needle in needles:
            assert needle.lower() in text.lower(), (
                f"USER_GUIDE.md does not mention {needle!r} (topic: {label})"
            )


# ---------------------------------------------------------------------------
# Install surface — MBA RULES block + SKILL.md reference only packaged docs
# ---------------------------------------------------------------------------


# Every path the installed MBA RULES block + SKILL.md refer to, taken
# from the MBA RULES block (in mba_foundation.markers) and the SKILL.md.
INSTALL_BLOCK_REFERENCES: tuple[str, ...] = (
    "docs/mba/charter.md",
    "docs/beads/capabilities.md",
)


def test_installed_mba_rules_block_references_only_packaged_docs() -> None:
    """The MBA RULES block shipped in ``mba_foundation.markers`` is the
    literal text that ``mba init`` writes into the consumer's
    ``AGENTS.md`` / ``CLAUDE.md``. Every doc it references must be in
    the public-doc set shipped by both metadata files."""

    from mba_foundation.markers import MBA_RULES_BLOCK

    for ref in INSTALL_BLOCK_REFERENCES:
        assert ref in MBA_RULES_BLOCK, (
            f"MBA RULES block mentions {ref!r}; ensure the doc is shipped"
        )
        assert (PROJECT_ROOT / ref).is_file(), f"{ref!r} missing on disk"


def test_installed_skill_references_only_packaged_docs() -> None:
    """The MBA SKILL.md is the file ``mba init`` copies verbatim into the
    consumer's ``.agents/skills/mba/SKILL.md``. Every doc it links to
    must be in the public-doc set shipped by both metadata files."""

    assert MBA_SKILL_SOURCE_PATH.is_file()
    text = _read(MBA_SKILL_SOURCE_PATH)
    # Extract all `](<path>)` targets — keep only those that look like
    # repo-relative paths (start with one of the public-doc prefixes).
    link_targets = re.findall(r"\]\(([^)]+)\)", text)
    interesting = [
        tgt
        for tgt in link_targets
        if not tgt.startswith(("http://", "https://", "#", "mailto:"))
    ]
    for ref in interesting:
        # Normalise: the skill writes relative paths like
        # `../../../docs/mba/charter.md`; strip the upward prefix.
        normalised = re.sub(r"^(?:\.\./)+", "", ref)
        assert normalised in PUBLIC_DOC_PATHS, (
            f"SKILL.md references {ref!r} (normalised: {normalised!r}); "
            "the referenced path must be in the public-doc set shipped "
            "via both pyproject.toml data-files and MANIFEST.in"
        )


def test_installed_skill_contains_safe_beads_write_commands() -> None:
    """Freshly-installed agents must see exact Beads commands that avoid
    defaulting AI work to the current human / OS / Git identity."""

    text = _read(MBA_SKILL_SOURCE_PATH)
    assert 'bd --actor "Orchestrator" create' in text
    assert '--assignee "Orchestrator"' in text
    assert 'bd --actor "<Role>" comments add <id> -f' in text
    assert "Do not use `bd update --claim`" in text
    assert "bd comments add -t -" in text
    assert "`||`, `head`, `wc`, `ls -la`" in text
    assert 'bd --actor "Orchestrator" dep add' in text


def test_installed_skill_uses_module_form_for_first_contact() -> None:
    """The installed skill must not depend on console scripts being on PATH."""

    text = _read(MBA_SKILL_SOURCE_PATH)
    assert "python -m mba_runtime first-contact --cwd . --apply-setup" in text
    assert "mba-runtime first-contact --cwd ." not in text
    assert "optional shortcut" in text


def test_public_docs_do_not_require_bare_first_contact_console_script() -> None:
    """Cold-start docs may mention console scripts, but not as the required
    first-contact command.
    """

    checked_paths = (
        "README.md",
        "docs/USER_GUIDE.md",
        "docs/mba/README.md",
        "docs/mba/charter.md",
        "docs/mba/startup-setup.md",
    )
    for relpath in checked_paths:
        text = _read(PROJECT_ROOT / relpath)
        assert "python -m mba_runtime first-contact --cwd . --apply-setup" in text
        assert "mba-runtime first-contact --cwd ." not in text


def test_installed_skill_uses_safe_plain_worker_launch_pointer() -> None:
    text = _read(MBA_SKILL_SOURCE_PATH)
    assert "Write the full assignment to the session's `prompt.md`" in text
    assert (
        '"Read .mba-work/<bead-id>/<session-name>/prompt.md and follow it."'
        in text
    )
    assert "only a short plain pointer" in text
    assert "Do not put Markdown backticks, command examples, or rich assignment text" in text
    assert "PowerShell can interpret and corrupt backticked text" in text
    assert "Normally 4-16 non-blank structured Markdown lines" in text


def test_installed_skill_rejects_same_transcript_hat_switching() -> None:
    text = _read(MBA_SKILL_SOURCE_PATH)
    assert "Do not satisfy the Doer/Auditor pair by switching hats inside one transcript" in text
    assert "launch or resume a distinct worker session/process" in text.lower() or "Launch or resume a distinct worker session/process" in text
    assert "must never manufacture convergence from the Orchestrator transcript" in text


def test_installed_skill_requires_observable_worker_launch_receipt() -> None:
    """The copy-and-use skill must require an observable launch
    receipt for every Doer/Auditor worker session before its outputs
    are accepted. Downstream smoke test showed worker comments produced from
    the Orchestrator transcript could pass the §3 gate just by
    including a worker-internal delegation value of ``none``; the
    installed skill has to make the launch receipt the load-bearing
    proof, not the worker comment.
    """

    text = _read(MBA_SKILL_SOURCE_PATH)
    assert ".mba-work/<bead-id>/<session-name>/launch.md" in text, (
        "installed skill must point to the launch receipt path"
    )
    assert "Launch command shape" in text
    assert "PID or session id" in text
    assert "Start time (UTC)" in text
    assert "Model / AI resource" in text
    assert "Prompt path" in text
    assert "Log / result paths" in text
    # Forbidden pattern: do not call the worker comment proof of the
    # session when the receipt is missing.
    assert (
        "it does **not** treat the worker comment as proof" in text
        or "it does not treat the worker comment as proof" in text
        or "does not treat the worker comment as proof" in text
        or "instead of treating the worker comment as proof" in text
    )


def test_installed_skill_distinguishes_worker_internal_none_from_skipped_worker() -> None:
    """A worker comment of ``Worker-internal AI delegation: none`` is
    only honest when the worker session was actually launched. The
    installed skill must spell out the difference so a generated
    worker comment cannot be read as proof of a separate session.
    """

    text = _read(MBA_SKILL_SOURCE_PATH)
    assert "Worker-internal AI delegation" in text or "worker-internal AI delegation" in text
    assert "`none`" in text or "``none``" in text
    # The clarifying sentence lives in the assignment-contract paragraph.
    assert "no separate worker session was launched" in text or "no separate worker session was actually launched" in text


def test_installed_skill_requires_launch_receipt_in_same_launch_step() -> None:
    """Downstream smoke test acceptance: the copy-and-use skill must require
    the launch receipt to be written in the same launch step that
    captures the worker's PID or session id, before any wait/tail/
    read/accept. The bare "before accepting outputs" wording allowed
    workers to produce ``report.md`` before the receipt existed.
    """

    import re

    text = _read(MBA_SKILL_SOURCE_PATH)
    assert "same launch step" in text, (
        "installed skill must require the launch receipt to be written "
        "in the same launch step that captures the worker's PID or session id."
    )
    assert re.search(
        r"before[\s\S]{0,80}wait[\s\S]{0,80}tail[\s\S]{0,80}read[\s\S]{0,80}accept",
        text,
    ), (
        "installed skill must list wait/tail/read/accept as forbidden "
        "steps before the launch receipt is written."
    )


def test_installed_skill_requires_hidden_windows_launch() -> None:
    """Downstream smoke test acceptance: the copy-and-use skill must require
    hidden / background Windows launches by default and name
    ``-WindowStyle Hidden`` so a fresh Orchestrator cannot regress
    to a visible empty CMD window.
    """

    text = _read(MBA_SKILL_SOURCE_PATH)
    assert "-WindowStyle Hidden" in text, (
        "installed skill must pin the Windows hidden launch flag."
    )
    assert "Start-Process" in text, (
        "installed skill must name the Start-Process launcher pattern."
    )


def test_installed_skill_says_empty_cmd_windows_are_not_progress_surface() -> None:
    """Downstream smoke test acceptance: the skill must say the empty CMD
    window is not the progress surface; logs, Bead comments, and
    launch receipts are.
    """

    import re

    text = _read(MBA_SKILL_SOURCE_PATH)
    assert re.search(
        r"not\s+(?:the\s+)?progress\s+surface", text
    ), "installed skill must say the empty CMD window is not the progress surface"
    assert re.search(
        r"(?:logs|Bead\s+comments|launch\s+receipts)\s+are", text
    ), "installed skill must name the actual progress surfaces"


def test_installed_skill_forbids_writing_receipt_after_worker_output() -> None:
    """Downstream smoke test acceptance: the skill must explicitly forbid
    writing the launch receipt after any worker output is present.
    """

    import re

    text = _read(MBA_SKILL_SOURCE_PATH)
    for kind in ("Bead comment", "transcript", "result.md", "report.md"):
        assert kind in text, (
            f"installed skill must name '{kind}' as part of the worker "
            "outputs that must not exist before launch.md is written."
        )
    assert re.search(
        r"after[\s\S]{0,40}any\s+worker\s+output", text
    ), "installed skill must explicitly forbid writing launch.md after any worker output"
    assert re.search(
        r"refusal-grade\s+error", text
    ), "installed skill must call writing the receipt after worker output a refusal-grade error"


def test_user_guide_requires_observable_worker_launch_receipt() -> None:
    """The user guide must tell the operator that the Orchestrator
    writes a launch receipt before it consumes any worker output.
    A bare "launch a separate worker" instruction without the
    receipt is the gap the Downstream smoke test exposed.
    """

    text = _read(USER_GUIDE_PATH)
    assert ".mba-work/<bead-id>/<session-name>/launch.md" in text
    assert "Launch command shape" in text
    assert "PID or session id" in text
    assert "Start time (UTC)" in text
    assert "Model / AI resource" in text
    assert "Prompt path" in text
    assert "Log / result paths" in text


def test_user_guide_requires_launch_receipt_in_same_launch_step() -> None:
    """Downstream smoke test acceptance: the user guide must say the launch
    receipt is written **in the same launch step** that captures the
    worker's PID or session id, before any wait/tail/read/accept.
    The bare "before accepting outputs" wording allowed workers to
    produce ``report.md`` and Bead comments before the receipt
    existed; the user guide must forbid that timing.
    """

    import re

    text = _read(USER_GUIDE_PATH)
    assert "same launch step" in text, (
        "USER_GUIDE.md must require the launch receipt to be written in "
        "the same launch step that captures the worker's PID or session id."
    )
    assert re.search(
        r"before[\s\S]{0,80}wait[\s\S]{0,80}tail[\s\S]{0,80}read[\s\S]{0,80}accept",
        text,
    ), (
        "USER_GUIDE.md must list wait/tail/read/accept as forbidden "
        "steps before the launch receipt is written."
    )


def test_user_guide_requires_hidden_windows_launch() -> None:
    """Downstream smoke test shipped empty CMD / conhost windows on the user
    desktop. The user guide must say hidden / background Windows
    launches are the default and name ``-WindowStyle Hidden`` or
    the equivalent non-interactive background launcher.
    """

    text = _read(USER_GUIDE_PATH)
    assert "-WindowStyle Hidden" in text, (
        "USER_GUIDE.md must pin the Windows hidden launch flag."
    )
    assert "Start-Process" in text, (
        "USER_GUIDE.md must name the Start-Process launcher pattern."
    )
    assert re.search(
        r"empty\s+CMD", text
    ), "USER_GUIDE.md must mention the empty CMD window failure mode"


def test_user_guide_says_empty_cmd_windows_are_not_progress_surface() -> None:
    """Downstream smoke test acceptance: empty CMD windows are not the
    progress surface; logs, Bead comments, and launch receipts are.
    The user guide must say so explicitly so a fresh Orchestrator
    does not treat the visible empty window as evidence of work.
    """

    import re

    text = _read(USER_GUIDE_PATH)
    assert re.search(
        r"not\s+(?:the\s+)?progress\s+surface", text
    ), "USER_GUIDE.md must say the empty CMD window is not the progress surface"
    assert re.search(
        r"(?:logs|Bead\s+comments|launch\s+receipts)\s+are", text
    ), "USER_GUIDE.md must name the actual progress surfaces"


def test_user_guide_forbids_writing_receipt_after_worker_output() -> None:
    """Downstream smoke test shipped workers that produced ``report.md`` and
    Bead comments before the launch receipt existed. The user guide
    must explicitly forbid writing the launch receipt after worker
    output is present.
    """

    import re

    text = _read(USER_GUIDE_PATH)
    for kind in ("Bead comment", "transcript", "result.md", "report.md"):
        assert kind in text, (
            f"USER_GUIDE.md must name '{kind}' as part of the worker "
            "outputs that must not exist before launch.md is written."
        )
    assert re.search(
        r"after[\s\S]{0,40}any\s+worker\s+output", text
    ), "USER_GUIDE.md must explicitly forbid writing launch.md after any worker output"
    assert re.search(
        r"refusal-grade\s+error", text
    ), "USER_GUIDE.md must call writing the receipt after worker output a refusal-grade error"


def test_charter_requires_observable_worker_launch_receipt() -> None:
    """The normative charter (docs/mba/charter.md) is the source of
    truth for the launch-receipt requirement. Without this wording
    in the charter, §3 alone would let the Orchestrator skip the
    proof step; the acceptance criterion explicitly states the
    "charter, skill, and user docs" must all carry it.
    """

    from mba_foundation.markers import MBA_RULES_BLOCK

    charter_text = _read(PROJECT_ROOT / "docs" / "mba" / "charter.md")
    assert ".mba-work/<bead-id>/<session-name>/launch.md" in charter_text, (
        "charter.md must point to the launch receipt path"
    )
    assert "Launch command shape" in charter_text
    assert "PID or session id" in charter_text
    assert "Start time (UTC)" in charter_text
    assert "Model / AI resource" in charter_text
    assert "Prompt path" in charter_text
    assert "Log / result paths" in charter_text
    # The installed MBA RULES block (consumer copy) must carry the
    # same wording, otherwise ``mba init`` ships an outdated rule.
    assert "launch.md" in MBA_RULES_BLOCK
    assert ".mba-work/<bead-id>/<session-name>/launch.md" in MBA_RULES_BLOCK


def test_charter_requires_launch_receipt_in_same_launch_step() -> None:
    """Downstream smoke test acceptance: the charter must require the
    launch receipt to be written in the same launch step that
    captures the worker's PID or session id, before any wait/tail/
    read/accept. The bare "before accepting outputs" wording
    allowed workers to produce ``report.md`` before the receipt.
    """

    import re

    charter_text = _read(PROJECT_ROOT / "docs" / "mba" / "charter.md")
    assert "same launch step" in charter_text, (
        "charter.md must require the launch receipt to be written in "
        "the same launch step that captures the worker's PID or session id."
    )
    assert re.search(
        r"before[\s\S]{0,80}wait[\s\S]{0,80}tail[\s\S]{0,80}read[\s\S]{0,80}accept",
        charter_text,
    ), (
        "charter.md must list wait/tail/read/accept as forbidden "
        "steps before the launch receipt is written."
    )


def test_charter_requires_hidden_windows_launch() -> None:
    """The charter must require hidden / background Windows launches
    by default and name ``-WindowStyle Hidden`` (or equivalent) so a
    fresh Orchestrator cannot regress to a visible empty CMD window.
    """

    charter_text = _read(PROJECT_ROOT / "docs" / "mba" / "charter.md")
    assert "-WindowStyle Hidden" in charter_text, (
        "charter.md must pin the Windows hidden launch flag."
    )
    assert "Start-Process" in charter_text, (
        "charter.md must name the Start-Process launcher pattern."
    )
    assert "PassThru" in charter_text, (
        "charter.md must mention PassThru so the launch step captures "
        "the worker PID in the same step it writes the receipt."
    )
    assert re.search(
        r"empty\s+CMD", charter_text
    ), "charter.md must mention the empty CMD window failure mode"


def test_charter_says_empty_cmd_windows_are_not_progress_surface() -> None:
    """The charter must explicitly say the empty CMD window is not
    the progress surface; logs, Bead comments, and launch receipts are.
    """

    import re

    charter_text = _read(PROJECT_ROOT / "docs" / "mba" / "charter.md")
    assert re.search(
        r"not\s+(?:the\s+)?progress\s+surface", charter_text
    ), "charter.md must say the empty CMD window is not the progress surface"
    assert re.search(
        r"(?:logs|Bead\s+comments|launch\s+receipts)\s+are", charter_text
    ), "charter.md must name the actual progress surfaces"


def test_charter_forbids_writing_receipt_after_worker_output() -> None:
    """The charter must explicitly forbid writing ``launch.md`` after
    any worker output is present and call it a refusal-grade error.
    """

    import re

    charter_text = _read(PROJECT_ROOT / "docs" / "mba" / "charter.md")
    for kind in ("Bead comment", "transcript", "result.md", "report.md"):
        assert kind in charter_text, (
            f"charter.md must name '{kind}' as part of the worker "
            "outputs that must not exist before launch.md is written."
        )
    assert re.search(
        r"after[\s\S]{0,40}any\s+worker\s+output", charter_text
    ), "charter.md must explicitly forbid writing launch.md after any worker output"
    assert re.search(
        r"refusal-grade\s+error", charter_text
    ), "charter.md must call writing the receipt after worker output a refusal-grade error"


def test_charter_canonical_powershell_launch_pattern_is_documented() -> None:
    """The charter must include a safe copy-paste PowerShell launch
    pattern that hides the worker window and writes the receipt in
    the same launch step. The Downstream smoke test acceptance criterion
    requires the docs to be deep enough for a new user/project to
    avoid the failure without reading every test.
    """

    charter_text = _read(PROJECT_ROOT / "docs" / "mba" / "charter.md")
    technical_flow_text = _read(PROJECT_ROOT / "docs" / "mba" / "technical-flow.md")
    # The pattern must appear in either the charter or the technical
    # flow doc (the latter is the implementation reference).
    target_text = charter_text + "\n" + technical_flow_text
    assert "Start-Process" in target_text, (
        "charter.md or technical-flow.md must include the Start-Process launch pattern."
    )
    assert "-WindowStyle Hidden" in target_text, (
        "charter.md or technical-flow.md must pin -WindowStyle Hidden."
    )
    assert "RedirectStandardOutput" in target_text and "RedirectStandardError" in target_text, (
        "charter.md or technical-flow.md must require redirecting stdout / stderr."
    )
    assert "PassThru" in target_text, (
        "charter.md or technical-flow.md must require PassThru so PID is captured in the same step."
    )


def test_public_guidance_chain_covers_observable_worker_launch_receipt() -> None:
    """Acceptance gate (example-gate): the public guidance chain — charter,
    user guide, the installed MBA skill, and the MBA RULES block that
    ``mba init`` writes into ``AGENTS.md``/``CLAUDE.md`` — must all
    carry the launch-receipt requirement. If any of these only say
    "launch separate sessions" without the receipt fields, the
    downstream test repo-retest gap returns. This test fails when the wording is
    removed from any of the four locations.
    """

    sources: dict[str, str] = {
        "charter": _read(PROJECT_ROOT / "docs" / "mba" / "charter.md"),
        "user_guide": _read(USER_GUIDE_PATH),
        "skill": _read(MBA_SKILL_SOURCE_PATH),
        "mba_rules_block": __import__(
            "mba_foundation.markers", fromlist=["MBA_RULES_BLOCK"]
        ).MBA_RULES_BLOCK,
    }
    missing: list[str] = []
    for label, text in sources.items():
        if ".mba-work/<bead-id>/<session-name>/launch.md" not in text:
            missing.append(label)
    assert not missing, (
        "Launch-receipt wording is missing from: "
        + ", ".join(missing)
        + ". Public guidance must require the Orchestrator to write a "
        ".mba-work/<bead-id>/<session-name>/launch.md receipt before "
        "accepting any Doer/Auditor worker output."
    )


def test_legacy_setup_placeholder_cleanup_removes_only_generated_mba_target(
    tmp_path: Path,
) -> None:
    from mba_foundation.cli import _cleanup_legacy_setup_placeholder

    legacy = tmp_path / ".mba-work" / "mba-target" / "orchestrator"
    legacy.mkdir(parents=True)
    (legacy / "working.md").write_text(
        "<!-- auto-generated by mba_foundation.preflight.preflight -->\n"
        "```json\n"
        '{"bead_id":"mba-target","bd_version":"1.0.4"}\n'
        "```\n",
        encoding="utf-8",
    )
    assert _cleanup_legacy_setup_placeholder(tmp_path) is True
    assert not (tmp_path / ".mba-work" / "mba-target").exists()


def test_legacy_setup_placeholder_cleanup_preserves_user_content(
    tmp_path: Path,
) -> None:
    from mba_foundation.cli import _cleanup_legacy_setup_placeholder

    legacy = tmp_path / ".mba-work" / "mba-target" / "orchestrator"
    legacy.mkdir(parents=True)
    (legacy / "working.md").write_text(
        "<!-- auto-generated by mba_foundation.preflight.preflight -->\n"
        "```json\n"
        '{"bead_id":"mba-target","bd_version":"1.0.4"}\n'
        "```\n",
        encoding="utf-8",
    )
    extra = tmp_path / ".mba-work" / "mba-target" / "worker-note.md"
    extra.write_text("keep me\n", encoding="utf-8")
    assert _cleanup_legacy_setup_placeholder(tmp_path) is False
    assert extra.read_text(encoding="utf-8") == "keep me\n"


# ---------------------------------------------------------------------------
# Fresh target — `mba init` writes the canonical install surface
# ---------------------------------------------------------------------------


def _bd_available() -> bool:
    import shutil

    return shutil.which("bd") is not None


def test_fresh_target_init_writes_canonical_install_surface(tmp_path: Path) -> None:
    """End-to-end proof: run ``mba init`` against a clean tmp directory
    (no ``.beads``, no manifest, no agent files), then assert the
    canonical install surface exists with the expected content."""

    if not _bd_available():
        pytest.skip("`bd` binary not on PATH; skipping live init proof")
    proc = subprocess.run(
        [sys.executable, "-m", "mba_foundation", "init", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(PROJECT_ROOT),
    )
    if proc.returncode == 5:
        pytest.skip(
            f"mba init refused the preflight in this environment: {proc.stderr[:200]}"
        )
    assert proc.returncode == 0, (
        f"`mba init` failed (rc={proc.returncode}): {proc.stderr[:400]}"
    )

    # Canonical install surface exists.
    assert (tmp_path / "AGENTS.md").is_file()
    assert (tmp_path / "CLAUDE.md").is_file()
    assert not (tmp_path / ("MBA_" + "WORKFLOW.md")).exists()
    assert (tmp_path / "docs" / "beads" / "capabilities.md").is_file()
    assert (tmp_path / "docs" / "mba" / "charter.md").is_file()
    assert (tmp_path / ".agents" / "skills" / "mba" / "SKILL.md").is_file()
    assert (tmp_path / "opencode.json").is_file()
    assert (tmp_path / ".opencode" / "agents" / "mba.md").is_file()
    assert (tmp_path / ".opencode" / "agents" / "mba-worker.md").is_file()
    assert (tmp_path / ".mba" / "manifest.json").is_file()

    # The MBA RULES block markers are present in both agent files.
    from mba_foundation.markers import (
        MBA_RULES_BEGIN_MARKER,
        MBA_RULES_END_MARKER,
    )

    for fname in ("AGENTS.md", "CLAUDE.md"):
        body = (tmp_path / fname).read_text(encoding="utf-8")
        assert MBA_RULES_BEGIN_MARKER in body
        assert MBA_RULES_END_MARKER in body

    # The skill is non-empty and starts with the YAML front-matter
    # (so downstream tooling can detect the `name: mba` description).
    skill_text = (tmp_path / ".agents" / "skills" / "mba" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert skill_text.startswith("---\n"), "skill must begin with YAML front-matter"
    assert "name: mba" in skill_text

    # The OpenCode bootstrap root config pins the MBA default agent
    # so a fresh consumer activates the MBA Orchestrator before any
    # project exploration.
    opencode_config_text = (tmp_path / "opencode.json").read_text(encoding="utf-8")
    assert '"default_agent": "mba"' in opencode_config_text
    assert '"AGENTS.md"' in opencode_config_text

    # The OpenCode bootstrap agent file mandates the cold-start
    # ``bd version`` + ``first-contact --apply-setup`` gate so a
    # fresh OpenCode session cannot bypass the MBA preflight.
    opencode_agent_text = (
        tmp_path / ".opencode" / "agents" / "mba.md"
    ).read_text(encoding="utf-8")
    assert opencode_agent_text.startswith("---\n"), (
        "OpenCode bootstrap agent must start with YAML front-matter"
    )
    assert "python -m mba_runtime first-contact --cwd . --apply-setup" in opencode_agent_text
    assert "bd version" in opencode_agent_text
    opencode_worker_text = (
        tmp_path / ".opencode" / "agents" / "mba-worker.md"
    ).read_text(encoding="utf-8")
    assert "You are not the Orchestrator" in opencode_worker_text
    assert "Do not run `python -m mba_runtime first-contact" in opencode_worker_text

    # The manifest records every install target (MBA RULES blocks,
    # skill, the docs referenced by both, and the OpenCode bootstrap
    # entries).
    import json

    manifest = json.loads((tmp_path / ".mba" / "manifest.json").read_text(encoding="utf-8"))
    relpaths = {entry["relpath"] for entry in manifest["files"]}
    assert relpaths == {
        "AGENTS.md",
        "CLAUDE.md",
        "docs/beads/capabilities.md",
        "docs/mba/charter.md",
        ".agents/skills/mba/SKILL.md",
        "opencode.json",
        ".opencode/agents/mba.md",
        ".opencode/agents/mba-worker.md",
    }
    assert (tmp_path / ".mba-work" / "_setup" / "orchestrator" / "working.md").is_file()
    assert not (tmp_path / ".mba-work" / "mba-target").exists()


def test_fresh_target_status_reports_installed_no_drift(tmp_path: Path) -> None:
    """After ``mba init``, ``mba status`` reports installed, no drift, no conflict."""

    if not _bd_available():
        pytest.skip("`bd` binary not on PATH; skipping live status proof")
    init = subprocess.run(
        [sys.executable, "-m", "mba_foundation", "init", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(PROJECT_ROOT),
    )
    if init.returncode == 5:
        pytest.skip(
            f"mba init refused the preflight in this environment: {init.stderr[:200]}"
        )
    assert init.returncode == 0, init.stderr

    proc = subprocess.run(
        [sys.executable, "-m", "mba_foundation", "status", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(PROJECT_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    import json

    payload = json.loads(proc.stdout)
    assert payload["installed"] is True
    assert payload["has_drift"] is False
    assert payload["has_conflicts"] is False
    assert payload["upgrade_available"] is False


def test_fresh_target_installed_content_references_packaged_docs(tmp_path: Path) -> None:
    """On the freshly-installed target repo, every doc the MBA RULES
    block + skill mention must be discoverable as a path on disk
    relative to the consumer (i.e. the MBA skill's upward ``../../../``
    prefix resolves under the original repo's docs/)."""

    if not _bd_available():
        pytest.skip("`bd` binary not on PATH; skipping live install-content proof")
    init = subprocess.run(
        [sys.executable, "-m", "mba_foundation", "init", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(PROJECT_ROOT),
    )
    if init.returncode == 5:
        pytest.skip(
            f"mba init refused the preflight in this environment: {init.stderr[:200]}"
        )
    assert init.returncode == 0, init.stderr

    skill_text = (
        tmp_path / ".agents" / "skills" / "mba" / "SKILL.md"
    ).read_text(encoding="utf-8")
    # Extract link targets relative to the skill's location
    # (``.agents/skills/mba/``), so ``../../../docs/mba/charter.md``
    # resolves to ``<repo>/docs/mba/charter.md``.
    link_targets = re.findall(r"\]\(([^)]+)\)", skill_text)
    for ref in link_targets:
        if ref.startswith(("http://", "https://", "#", "mailto:")):
            continue
        resolved = (tmp_path / ".agents" / "skills" / "mba" / ref).resolve()
        assert resolved.is_file(), (
            f"installed skill references {ref!r}; expected file at {resolved}"
        )
