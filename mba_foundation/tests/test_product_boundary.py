"""MBA product / install / excluded boundary (Bead ``example-018.1``).

The boundary has three buckets — :data:`PRODUCT_PATTERNS`,
:data:`INSTALL_CONTENT_PATTERNS`, :data:`EXCLUDED_PATTERNS` — and a
filesystem walker that classifies a path against all three. The
walker must work on a fresh checkout with no Git history and on
synthetic workspaces in tests. No test in this file may invoke
``git``; the boundary is intentionally Git-independent.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from mba_foundation import product_boundary as pb


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_paths(paths: list[Path]) -> set[Path]:
    """Resolve a list of paths to ``set[Path]`` for membership checks."""

    return {p.resolve() for p in paths}


# ---------------------------------------------------------------------------
# Constants shape
# ---------------------------------------------------------------------------


def test_pattern_tuples_are_non_empty() -> None:
    """All three pattern lists must contain at least one entry."""

    assert pb.PRODUCT_PATTERNS, "PRODUCT_PATTERNS must not be empty"
    assert pb.INSTALL_CONTENT_PATTERNS, "INSTALL_CONTENT_PATTERNS must not be empty"
    assert pb.EXCLUDED_PATTERNS, "EXCLUDED_PATTERNS must not be empty"


def test_pattern_tuples_are_frozen() -> None:
    """Pattern lists are module-level constants. A consumer must not
    be able to mutate them; the test mutates a local copy to confirm
    the public surface is read-only."""

    for value in (pb.PRODUCT_PATTERNS, pb.INSTALL_CONTENT_PATTERNS, pb.EXCLUDED_PATTERNS):
        assert isinstance(value, tuple), (
            f"expected tuple, got {type(value).__name__}"
        )


def test_bucket_constants_are_strings() -> None:
    """Bucket names are part of the public string vocabulary."""

    assert pb.BUCKET_PRODUCT == "product"
    assert pb.BUCKET_INSTALL_CONTENT == "install_content"
    assert pb.BUCKET_EXCLUDED == "excluded"
    assert pb.BUCKET_UNCLASSIFIED == "unclassified"


def test_patterns_by_bucket_matches_named_tuples() -> None:
    assert pb.PATTERNS_BY_BUCKET[pb.BUCKET_PRODUCT] == pb.PRODUCT_PATTERNS
    assert pb.PATTERNS_BY_BUCKET[pb.BUCKET_INSTALL_CONTENT] == pb.INSTALL_CONTENT_PATTERNS
    assert pb.PATTERNS_BY_BUCKET[pb.BUCKET_EXCLUDED] == pb.EXCLUDED_PATTERNS


def test_install_content_is_subset_of_product() -> None:
    """Every install-content source must also be a product file
    (install content is a policy annotation on a product file, not
    a separate location)."""

    for pattern in pb.INSTALL_CONTENT_PATTERNS:
        assert pattern in pb.PRODUCT_PATTERNS, (
            f"install-content pattern {pattern!r} must appear in "
            f"PRODUCT_PATTERNS"
        )


def test_no_install_content_is_also_excluded() -> None:
    """A pattern in the install-content set must not also be
    excluded — install content is shipped, not suppressed."""

    for pattern in pb.INSTALL_CONTENT_PATTERNS:
        assert pattern not in pb.EXCLUDED_PATTERNS, (
            f"install-content pattern {pattern!r} must not appear in "
            f"EXCLUDED_PATTERNS"
        )


# ---------------------------------------------------------------------------
# Classification (single path)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel_path,expected",
    [
        # Product surface — runtime packages.
        ("mba_foundation/__init__.py", pb.BUCKET_PRODUCT),
        ("mba_foundation/markers.py", pb.BUCKET_INSTALL_CONTENT),
        ("mba_primitives/cli.py", pb.BUCKET_PRODUCT),
        ("mba_runtime/lifecycle.py", pb.BUCKET_PRODUCT),
        # Test subpackages — MBA-owned by transitivity.
        ("mba_foundation/tests/test_workspace.py", pb.BUCKET_PRODUCT),
        ("mba_primitives/tests/test_bead_write.py", pb.BUCKET_PRODUCT),
        ("mba_runtime/tests/test_user_authority.py", pb.BUCKET_PRODUCT),
            # Public package metadata, version, and test harness.
            ("README.md", pb.BUCKET_PRODUCT),
            ("LICENSE", pb.BUCKET_PRODUCT),
            ("pyproject.toml", pb.BUCKET_PRODUCT),
            ("MANIFEST.in", pb.BUCKET_PRODUCT),
            (".gitattributes", pb.BUCKET_PRODUCT),
            (".gitignore", pb.BUCKET_PRODUCT),
            ("mba_version.py", pb.BUCKET_PRODUCT),
            ("conftest.py", pb.BUCKET_PRODUCT),
            ("pytest.ini", pb.BUCKET_PRODUCT),
            # Public docs.
            ("docs/mba/charter.md", pb.BUCKET_PRODUCT),
            ("docs/mba/README.md", pb.BUCKET_PRODUCT),
            ("docs/mba/roadmap.md", pb.BUCKET_PRODUCT),
            ("docs/mba/assets/minimax-diagram.svg", pb.BUCKET_PRODUCT),
            ("docs/beads/capabilities.md", pb.BUCKET_PRODUCT),
            ("docs/beads/evaluation.md", pb.BUCKET_PRODUCT),
            # Pointer doc.
            ("docs/mba/charter.md", pb.BUCKET_PRODUCT),
            # Install content source.
            ("mba_foundation/resources/skills/mba/SKILL.md", pb.BUCKET_INSTALL_CONTENT),
            # Project-owned (MBA neither ships nor installs the whole file).
            ("AGENTS.md", pb.BUCKET_UNCLASSIFIED),
            ("CLAUDE.md", pb.BUCKET_UNCLASSIFIED),
        # Local dogfooding state — always excluded.
        (".beads/metadata.json", pb.BUCKET_EXCLUDED),
        (".mba-work/.mba-mode", pb.BUCKET_EXCLUDED),
        (".mba-work/.ai-resources.json", pb.BUCKET_EXCLUDED),
        (".mba-work/example-018.1/orchestrator/working.md", pb.BUCKET_EXCLUDED),
        # VCS + tooling cache.
        (".git/HEAD", pb.BUCKET_EXCLUDED),
        (".git/config", pb.BUCKET_EXCLUDED),
        ("__pycache__/foo.pyc", pb.BUCKET_EXCLUDED),
        ("mba_foundation/__pycache__/constants.cpython-313.pyc", pb.BUCKET_EXCLUDED),
        ("mba_primitives/tests/__pycache__/x.pyc", pb.BUCKET_EXCLUDED),
        (".pytest_cache/CACHEDIR.TAG", pb.BUCKET_EXCLUDED),
        # Beads-generated surfaces.
        (".claude/settings.json", pb.BUCKET_EXCLUDED),
        (".agents/skills/beads/SKILL.md", pb.BUCKET_EXCLUDED),
        (".agents/skills/mba/SKILL.md", pb.BUCKET_EXCLUDED),
        # Defensive — build artefacts.
        ("build/lib/foo.py", pb.BUCKET_EXCLUDED),
        ("dist/mba-1.0.tar.gz", pb.BUCKET_EXCLUDED),
        ("mba_foundation.egg-info/PKG-INFO", pb.BUCKET_EXCLUDED),
    ],
)
def test_classify_path_against_three_buckets(tmp_path: Path, rel_path: str, expected: str) -> None:
    """Classification works on a synthetic empty repo (no Git, no
    real ``.mba-work``). The :func:`classify_path` helper does not
    read the filesystem; it is pure string matching against the
    pattern lists."""

    assert pb.classify_path(rel_path, tmp_path) == expected


def test_classify_path_precedence_excluded_wins(tmp_path: Path) -> None:
    """If a path is both product and excluded, the excluded bucket
    wins. ``.pyc`` is the canonical case: even a file under
    ``mba_foundation/__pycache__/`` is tooling cache, not product."""

    rel = "mba_foundation/__pycache__/x.pyc"
    assert pb.classify_path(rel, tmp_path) == pb.BUCKET_EXCLUDED


def test_classify_path_accepts_native_separators(tmp_path: Path) -> None:
    """A Windows-style backslash path normalises to POSIX before
    matching, so the same path string is portable across platforms."""

    rel = Path("mba_foundation") / "markers.py"
    assert pb.classify_path(str(rel), tmp_path) == pb.BUCKET_INSTALL_CONTENT


# ---------------------------------------------------------------------------
# Iteration (filesystem walk)
# ---------------------------------------------------------------------------


def _populate_synthetic_repo(root: Path) -> dict[str, list[Path]]:
    """Build a synthetic repo that exercises every bucket.

    Returns a mapping of bucket label → list of paths the
    population step created. The test asserts the iteration
    functions find every one of them.
    """

    created: dict[str, list[Path]] = {
        "product": [],
        "install_content": [],
        "excluded": [],
        "unclassified": [],
    }

    # Product: a Python package + a top-level test file + a doc.
    pkg = root / "mba_foundation"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("__all__ = []\n", encoding="utf-8")
    (pkg / "cli.py").write_text("def cli() -> None: pass\n", encoding="utf-8")
    (pkg / "README.md").write_text("# pkg\n", encoding="utf-8")
    tests_pkg = pkg / "tests"
    tests_pkg.mkdir()
    (tests_pkg / "__init__.py").write_text("", encoding="utf-8")
    (tests_pkg / "test_foo.py").write_text("def test_x(): pass\n", encoding="utf-8")
    created["product"].extend(
        [pkg / "__init__.py", pkg / "cli.py", pkg / "README.md",
         tests_pkg / "__init__.py", tests_pkg / "test_foo.py"]
    )

    # Version identity.
    (root / "mba_version.py").write_text('__version__ = "0.0.0"\n', encoding="utf-8")
    created["product"].append(root / "mba_version.py")

    # Test harness at repo root.
    (root / "conftest.py").write_text("", encoding="utf-8")
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    created["product"].extend([root / "conftest.py", root / "pytest.ini"])

    # Normative doc.
    docs = root / "docs" / "mba"
    docs.mkdir(parents=True)
    (docs / "charter.md").write_text("# charter\n", encoding="utf-8")
    created["product"].append(docs / "charter.md")

    # Install content — the markers module + the skill.
    (pkg / "markers.py").write_text("X = 1\n", encoding="utf-8")
    created["install_content"].append(pkg / "markers.py")
    skill = pkg / "resources" / "skills" / "mba"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# mba\n", encoding="utf-8")
    created["install_content"].append(skill / "SKILL.md")

    # Excluded: .beads/, .mba-work/, .git/, __pycache__/, .pytest_cache/.
    beads = root / ".beads"
    beads.mkdir()
    (beads / "metadata.json").write_text('{"database":"dolt"}\n', encoding="utf-8")
    (beads / "issues.jsonl").write_text("", encoding="utf-8")
    created["excluded"].extend([beads / "metadata.json", beads / "issues.jsonl"])

    work = root / ".mba-work"
    work.mkdir()
    (work / ".mba-mode").write_text("local\n", encoding="utf-8")
    (work / ".ai-resources.json").write_text('{"resources":[]}\n', encoding="utf-8")
    created["excluded"].extend([work / ".mba-mode", work / ".ai-resources.json"])

    cache = pkg / "__pycache__"
    cache.mkdir()
    (cache / "cli.cpython-313.pyc").write_text("cached\n", encoding="utf-8")
    created["excluded"].append(cache / "cli.cpython-313.pyc")

    pytest_cache = root / ".pytest_cache"
    pytest_cache.mkdir()
    (pytest_cache / "CACHEDIR.TAG").write_text("tag\n", encoding="utf-8")
    created["excluded"].append(pytest_cache / "CACHEDIR.TAG")

    # Project-owned (unclassified).
    (root / "AGENTS.md").write_text("# project-owned\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("# project-owned\n", encoding="utf-8")
    (root / "README.md").write_text("# project\n", encoding="utf-8")
    created["unclassified"].extend([root / "AGENTS.md", root / "CLAUDE.md", root / "README.md"])

    return created


def test_iter_product_files_finds_synthetic_product(tmp_path: Path) -> None:
    expected_paths = _populate_synthetic_repo(tmp_path)["product"]
    expected = {p.resolve() for p in expected_paths}
    actual = {p.resolve() for p in pb.iter_product_files(tmp_path)}
    # Every created product path is found. The test does not assert
    # equality because the synthetic repo also matches some
    # patterns that may pick up other generated artefacts (e.g.,
    # ``__init__.py`` is also matched by the ``mba_foundation/**/*.py``
    # pattern).
    missing = expected - actual
    assert not missing, f"product iteration missed: {sorted(missing)}"
    # Crucially, the install-content marker module is in the product
    # set too.
    assert (tmp_path / "mba_foundation" / "markers.py").resolve() in actual


def test_iter_install_content_files_finds_synthetic_install(tmp_path: Path) -> None:
    expected_paths = _populate_synthetic_repo(tmp_path)["install_content"]
    expected = {p.resolve() for p in expected_paths}
    actual = {p.resolve() for p in pb.iter_install_content_files(tmp_path)}
    assert actual == expected


def test_iter_excluded_paths_finds_synthetic_excluded(tmp_path: Path) -> None:
    expected_paths = _populate_synthetic_repo(tmp_path)["excluded"]
    expected = {p.resolve() for p in expected_paths}
    actual = {p.resolve() for p in pb.iter_excluded_paths(tmp_path)}
    missing = expected - actual
    assert not missing, f"excluded iteration missed: {sorted(missing)}"


def test_iteration_deduplicates(tmp_path: Path) -> None:
    """A file that matches more than one product pattern is yielded
    exactly once."""

    _populate_synthetic_repo(tmp_path)
    seen: list[Path] = list(pb.iter_product_files(tmp_path))
    # Set membership is set equality; len must match.
    assert len(seen) == len(set(seen))


# ---------------------------------------------------------------------------
# Summary + overlap report
# ---------------------------------------------------------------------------


def test_summarize_returns_three_ints(tmp_path: Path) -> None:
    _populate_synthetic_repo(tmp_path)
    summary = pb.summarize(tmp_path)
    assert summary.product > 0
    assert summary.install_content == 2  # markers.py + SKILL.md
    assert summary.excluded > 0
    # install_content is a subset of product by design; total must
    # not double-count the install-content overlap. See
    # `BoundarySummary.total` and the `summarize` docstring contract.
    assert summary.total == summary.product + summary.excluded


def test_summarize_total_equals_distinct_union(tmp_path: Path) -> None:
    """Regression pin for the `BoundarySummary.total` contract.

    The Auditor round 1 of Bead ``example-018.1`` found that the
    previous `return self.product + self.install_content + self.excluded`
    over-reported by exactly the install-content size, contradicting
    the `summarize` docstring. The agreed fix (round 2, preferred
    option) makes `total` the distinct-union size
    (`product + excluded`). This test pins that invariant on a
    synthetic repo where install-content files are guaranteed to
    also match a product pattern (mirroring the live repo, where
    ``markers.py`` and ``mba_foundation/resources/skills/mba/SKILL.md`` match both
    ``INSTALL_CONTENT_PATTERNS`` and ``PRODUCT_PATTERNS``).
    """
    _populate_synthetic_repo(tmp_path)
    summary = pb.summarize(tmp_path)
    distinct = set(pb.iter_product_files(tmp_path)) | set(
        pb.iter_excluded_paths(tmp_path)
    )
    assert summary.total == len(distinct)
    # And the install-content overlap is real on this fixture (proves
    # the union-vs-sum distinction is exercised, not vacuously true).
    assert summary.install_content > 0
    install_overlap = set(pb.iter_install_content_files(tmp_path)) & set(
        pb.iter_product_files(tmp_path)
    )
    assert len(install_overlap) == summary.install_content


def test_assert_no_overlap_passes_on_synthetic_repo(tmp_path: Path) -> None:
    _populate_synthetic_repo(tmp_path)
    report = pb.assert_no_overlap(tmp_path)
    assert report.has_overlap is False
    assert report.excluded_and_product == ()
    assert report.excluded_and_install == ()


def test_overlap_report_dataclass_is_well_formed() -> None:
    """The :class:`OverlapReport` dataclass is the boundary's
    contract for a "leak found" scenario. A future ``mba upgrade``
    that mutates the pattern lists can construct a report
    directly; this test pins the shape so the contract cannot
    drift silently."""

    leak = Path("/synthetic/mba_foundation/markers.py")
    report = pb.OverlapReport(
        excluded_and_product=(leak,),
        excluded_and_install=(),
    )
    assert report.has_overlap is True
    assert report.excluded_and_product == (leak,)
    assert report.excluded_and_install == ()


def test_overlap_report_empty_when_no_leak() -> None:
    report = pb.OverlapReport(
        excluded_and_product=(),
        excluded_and_install=(),
    )
    assert report.has_overlap is False


# ---------------------------------------------------------------------------
# install_content_targets + assert_install_set_safe
# ---------------------------------------------------------------------------


def test_install_content_targets_lists_all_surfaces() -> None:
    """The canonical install surface now carries the OpenCode bootstrap
    in addition to the legacy MBA-managed-block + skill + docs set.

    A consumer ``mba init`` writes the full install surface:

    * ``AGENTS.md`` and ``CLAUDE.md`` — the MBA RULES blocks.
    * ``docs/mba/charter.md`` and ``docs/beads/capabilities.md`` —
      the docs the installed skill and MBA RULES block reference.
    * ``.agents/skills/mba/SKILL.md`` — the MBA skill.
    * ``opencode.json``, ``.opencode/agents/mba.md`` and
      ``.opencode/agents/mba-worker.md`` — the OpenCode bootstrap
      root config, primary-mode MBA Orchestrator agent, and worker
      agent so a fresh consumer activates as the MBA Orchestrator and
      launches Doer/Auditor sessions through a non-Orchestrator
      surface.
    """

    targets = pb.install_content_targets()
    assert "AGENTS.md" in targets
    assert "CLAUDE.md" in targets
    assert ".agents/skills/mba/SKILL.md" in targets
    assert "opencode.json" in targets
    assert ".opencode/agents/mba.md" in targets
    assert ".opencode/agents/mba-worker.md" in targets


def test_install_content_verbatim_copy_targets_include_opencode_bootstrap() -> None:
    """The OpenCode bootstrap files are verbatim-copy targets; the
    agent is not a managed-block target."""

    targets = pb.install_content_verbatim_copy_targets()
    assert "opencode.json" in targets
    assert ".opencode/agents/mba.md" in targets
    assert ".opencode/agents/mba-worker.md" in targets
    # They are NOT managed-block targets.
    assert "opencode.json" not in pb.install_content_managed_block_targets()
    assert ".opencode/agents/mba.md" not in pb.install_content_managed_block_targets()
    assert (
        ".opencode/agents/mba-worker.md"
        not in pb.install_content_managed_block_targets()
    )


def test_install_content_opencode_targets_pin_exact_paths() -> None:
    """Pin the exact consumer relpaths (no leading slash, no trailing
    separator) so a future typo is caught at the test boundary."""

    assert "opencode.json" in pb.install_content_targets()
    assert ".opencode/agents/mba.md" in pb.install_content_targets()
    assert ".opencode/agents/mba-worker.md" in pb.install_content_targets()
    # The agent path uses POSIX separators because the manifest's
    # drift classifier and the install write paths use POSIX
    # normalisation everywhere.
    assert ".opencode/agents/mba.md".replace("\\", "/") in [
        rel.replace("\\", "/") for rel in pb.install_content_targets()
    ]
    assert ".opencode/agents/mba-worker.md".replace("\\", "/") in [
        rel.replace("\\", "/") for rel in pb.install_content_targets()
    ]


def test_classify_path_recognises_opencode_bootstrap_sources() -> None:
    """The packaged source files for the OpenCode bootstrap are
    install-content product files, never excluded. A future edit to
    EXCLUDED_PATTERNS that accidentally catches them would break the
    install copy."""

    # Repo-relative source paths the consumer never sees; they live
    # in the MBA source checkout / installed package and resolve via
    # ``importlib.resources``.
    assert pb.classify_path(
        "mba_foundation/resources/opencode/opencode.json", PROJECT_ROOT
    ) == pb.BUCKET_INSTALL_CONTENT
    assert pb.classify_path(
        "mba_foundation/resources/opencode/agents/mba.md", PROJECT_ROOT
    ) == pb.BUCKET_INSTALL_CONTENT
    assert pb.classify_path(
        "mba_foundation/resources/opencode/agents/mba-worker.md", PROJECT_ROOT
    ) == pb.BUCKET_INSTALL_CONTENT


def test_iter_install_content_files_includes_opencode_sources(tmp_path: Path) -> None:
    """The boundary's filesystem walker surfaces the packaged
    OpenCode sources on a synthetic repo that mirrors the live
    layout. This pins that the product/install-content bucket
    classification carries the OpenCode bootstrap entries.

    The test physically writes the two source files into the
    synthetic repo because :func:`iter_install_content_files` walks
    the filesystem (the boundary is intentionally Git-independent).
    """

    _populate_synthetic_repo(tmp_path)
    opencode_root = tmp_path / "mba_foundation" / "resources" / "opencode"
    opencode_root.mkdir(parents=True, exist_ok=True)
    (opencode_root / "opencode.json").write_text('{"default_agent": "mba"}\n')
    agents_root = opencode_root / "agents"
    agents_root.mkdir(parents=True, exist_ok=True)
    (agents_root / "mba.md").write_text("---\ndescription: MBA\n---\n\n")
    (agents_root / "mba-worker.md").write_text(
        "---\ndescription: MBA worker\n---\n\n"
    )

    expected_paths = _resolve_paths(
        [
            opencode_root / "opencode.json",
            agents_root / "mba.md",
            agents_root / "mba-worker.md",
        ]
    )
    actual_paths = set(
        p.resolve() for p in pb.iter_install_content_files(tmp_path)
    )
    missing = expected_paths - actual_paths
    assert not missing, f"install-content iteration missed: {sorted(missing)}"
    # And every other expected install-content path is still
    # surfaced.
    assert (tmp_path / "mba_foundation" / "markers.py").resolve() in actual_paths
    assert (
        tmp_path
        / "mba_foundation"
        / "resources"
        / "skills"
        / "mba"
        / "SKILL.md"
    ).resolve() in actual_paths


def test_assert_install_set_safe_passes_for_product_paths(tmp_path: Path) -> None:
    _populate_synthetic_repo(tmp_path)
    plan = [
        tmp_path / "mba_foundation" / "markers.py",
        tmp_path / "mba_foundation" / "resources" / "skills" / "mba" / "SKILL.md",
    ]
    ok, reason = pb.assert_install_set_safe(plan, tmp_path)
    assert ok is True, reason


def test_assert_install_set_safe_refuses_excluded_paths(tmp_path: Path) -> None:
    _populate_synthetic_repo(tmp_path)
    plan = [
        tmp_path / "mba_foundation" / "markers.py",     # product
        tmp_path / ".mba-work" / ".ai-resources.json",   # excluded
    ]
    ok, reason = pb.assert_install_set_safe(plan, tmp_path)
    assert ok is False
    assert ".ai-resources" in reason


# ---------------------------------------------------------------------------
# Git-independence
# ---------------------------------------------------------------------------


def test_boundary_works_without_dot_git(tmp_path: Path) -> None:
    """The boundary must work on a fresh checkout with no Git
    history. ``git log`` on this repo currently fails with
    "does not have any commits yet" — the boundary must be
    insensitive to that."""

    assert not (tmp_path / ".git").exists()
    _populate_synthetic_repo(tmp_path)
    summary = pb.summarize(tmp_path)
    # Counts must be the same as a repo with ``.git/`` present; the
    # ``.git/`` pattern is excluded but ``.git/`` does not exist
    # here so the excluded set is identical.
    assert summary.product > 0
    assert summary.install_content == 2
    assert summary.excluded > 0


def test_boundary_works_in_isolated_workspace(tmp_path: Path) -> None:
    """A workspace completely outside the project — typical of CI
    and ``conftest.py``'s ``authorized_workspace`` fixture — sees
    the same product / install / excluded counts on the synthetic
    fixture the tests populate."""

    _populate_synthetic_repo(tmp_path)
    # No project-level imports; the module is imported once and
    # reused.
    summary = pb.summarize(tmp_path)
    assert summary.product >= 5
    assert summary.install_content == 2
    assert summary.excluded >= 6


# ---------------------------------------------------------------------------
# No-Git-ls-files guarantee
# ---------------------------------------------------------------------------


def test_module_does_not_invoke_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A monkey-patched :mod:`subprocess` raises if the boundary
    ever shells out to ``git``. This is a regression guard: the
    boundary is filesystem-only by contract, and a future edit
    must not quietly reintroduce a ``git ls-files`` dependency.
    """

    import subprocess

    def _fail_on_git(*args, **kwargs):  # type: ignore[no-untyped-def]
        cmd = args[0] if args else kwargs.get("args", [])
        if cmd and isinstance(cmd, (list, tuple)) and cmd[0] == "git":
            raise AssertionError(
                f"product_boundary must not invoke git; got {cmd!r}"
            )
        if cmd and isinstance(cmd, str) and cmd.startswith("git"):
            raise AssertionError(
                f"product_boundary must not invoke git; got {cmd!r}"
            )
        return subprocess.CompletedProcess(
            args=cmd or [], returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", _fail_on_git)
    # Walk the full boundary on the synthetic repo.
    _populate_synthetic_repo(tmp_path)
    list(pb.iter_product_files(tmp_path))
    list(pb.iter_install_content_files(tmp_path))
    list(pb.iter_excluded_paths(tmp_path))
    pb.summarize(tmp_path)
    pb.assert_no_overlap(tmp_path)
    pb.classify_path("mba_foundation/__init__.py", tmp_path)


# ---------------------------------------------------------------------------
# Re-importability (the module has no side effects)
# ---------------------------------------------------------------------------


def test_module_is_reimportable(tmp_path: Path) -> None:
    """The module must be safe to re-import: every public symbol is
    a constant or a pure function. This test ensures a downstream
    test harness (e.g. a ``pytest`` plugin that reloads the
    Foundation) can ``importlib.reload`` without side effects."""

    module = importlib.import_module("mba_foundation.product_boundary")
    reloaded = importlib.reload(module)
    assert reloaded is module
    # Constants survive a reload.
    assert reloaded.PRODUCT_PATTERNS == module.PRODUCT_PATTERNS
    # Remove the module from ``sys.modules`` so a later import works
    # the same way for subsequent tests.
    sys.modules.pop("mba_foundation.product_boundary", None)
