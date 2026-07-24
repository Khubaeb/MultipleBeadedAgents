"""MBA product / install / excluded boundary.

Bead `example-018.1` — define and implement the copy/install boundary
between MBA-the-product and this repository's dogfooding state.

The module is intentionally small and stdlib-only so a future
``mba init`` / ``mba status`` / ``mba upgrade`` CLI can import it
without adding any optional dependency.

## Three buckets

* **Product** — files that ship with the MBA package. The Python
  runtime packages, the version identity, the normative docs, the
  test infrastructure, and the normative charter. Without these,
  MBA is not installable.
* **Install content** — a *subset* of the product that a future
  ``mba init`` actively writes into a consumer project. The MBA
  skill (``SKILL.md``) and the canonical MBA RULES block (lives as
  the ``MBA_RULES_BLOCK`` constant in ``mba_foundation.markers``)
  are the two install surfaces today; the marker block is
  reinstalled/refreshed in place on every ``mba upgrade``,
  ``SKILL.md`` is copied verbatim from the packaged resource under
  ``mba_foundation/resources/skills/mba/``.
* **Excluded** — paths that are NEVER shipped and NEVER installed.
  Local Beads state, working evidence, tooling-generated cache,
  VCS data, Beads-managed skills/hooks, common secrets. A copy
  that includes any of these has copied the wrong things.

## Why three buckets, not two

A flat product/excluded split would force every consumer to
re-discover "what is this file for?" by reading the file. The
install-content subset makes the runtime answer explicit:
*Product files are what ships; install-content files are what
``mba init`` writes into a consumer repo; excluded files are never
touched.* The install-content subset is also where future policy
gates (e.g. "this file is allowed to be touched by a re-install")
will live.

## Why no ``git ls-files``

This repository currently has no commits (``git log`` fails with
"does not have any commits yet"), so ``git ls-files`` is empty.
The boundary MUST work on a fresh checkout before the first commit,
on a partially-committed worktree, and on a fully-committed
worktree. The implementation walks the filesystem directly via
:mod:`pathlib`; it never invokes ``git`` and never reads
``.git/index``.

## Where the boundary lives in the public surface

* The pattern lists below are the **single source of truth** for
  what the boundary says. Every consumer of this module reads them
  rather than re-deriving the rules.
* The iteration functions (``iter_product_files``,
  ``iter_install_content_files``, ``iter_excluded_paths``) walk the
  filesystem and return the matching absolute paths. They are
  stable for a given filesystem state and idempotent on re-entry.
* The classification helper (``classify_path``) classifies a single
  relative path against the three buckets. ``excluded`` wins over
  ``install_content`` wins over ``product`` when patterns overlap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


# ---------------------------------------------------------------------------
# Pattern lists (POSIX-style relative globs, evaluated against
# ``<repo_root>/<relpath>``).
# ---------------------------------------------------------------------------


#: Path patterns (POSIX-style relative globs) that ship with the MBA
#: product. The list is the single source of truth — a future
#: ``mba init`` / ``mba status`` / ``mba upgrade`` reads it, never
#: re-derives it.
PRODUCT_PATTERNS: tuple[str, ...] = (
    # Public package / repository metadata.
    "README.md",
    "LICENSE",
    "pyproject.toml",
    "MANIFEST.in",
    ".gitattributes",
    ".gitignore",
    # Version identity (the single source-of-truth per
    # ``docs/mba/README.md``).
    "mba_version.py",
    # Runtime packages + their ``README.md`` and ``tests/`` subpackages.
    "mba_foundation/**/*.py",
    "mba_foundation/README.md",
    "mba_primitives/**/*.py",
    "mba_primitives/README.md",
    "mba_runtime/**/*.py",
    # Public docs and generated docs assets.
    "docs/USER_GUIDE.md",
    "docs/mba/**",
    "docs/beads/**",
    # Test infrastructure (project-root pytest harness consumed by
    # every package's tests).
    "conftest.py",
    "pytest.ini",
    # The MBA RULES block carrier — the literal ``MBA_RULES_BLOCK``
    # constant lives in ``mba_foundation.markers`` and is what
    # ``mba init`` writes into a consumer's ``AGENTS.md`` /
    # ``CLAUDE.md``. Shipping the module ships the install content
    # for that surface.
    "mba_foundation/markers.py",
    # The normative workflow charter referenced by generated rules,
    # source documentation, and the installed skill.
    "docs/mba/charter.md",
    # The MBA skill source. ``mba init`` copies this packaged resource
    # into the consumer's ``.agents/skills/mba/SKILL.md`` path.
    "mba_foundation/resources/skills/mba/SKILL.md",
    # The OpenCode bootstrap root-config source. ``mba init`` copies
    # this verbatim-copy resource into the consumer's ``opencode.json``
    # so a fresh OpenCode session activates as the MBA Orchestrator.
    "mba_foundation/resources/opencode/opencode.json",
    # The OpenCode bootstrap primary-mode MBA Orchestrator agent
    # source. ``mba init`` copies this verbatim-copy resource into
    # the consumer's ``.opencode/agents/mba.md`` so the agent mandates
    # ``bd version`` and ``python -m mba_runtime first-contact --cwd
    # . --apply-setup`` before any project exploration or worker
    # launch.
    "mba_foundation/resources/opencode/agents/mba.md",
    # The OpenCode MBA worker agent source. ``mba init`` copies this
    # verbatim-copy resource into the consumer's
    # ``.opencode/agents/mba-worker.md`` so Doer/Auditor launches can
    # target a worker surface instead of the ``mba`` Orchestrator
    # default agent.
    "mba_foundation/resources/opencode/agents/mba-worker.md",
)


#: Install content — a *subset* of the product whose purpose is to
#: be actively written into a consumer project by ``mba init`` (or
#: refreshed by ``mba upgrade``). The install surfaces today are:
#:
#: 1. The MBA RULES block — written by
#:    :func:`mba_foundation.markers.install_block` into the
#:    consumer's ``AGENTS.md`` and ``CLAUDE.md``. The block
#:    content lives in the ``MBA_RULES_BLOCK`` constant in
#:    :mod:`mba_foundation.markers`; the file that ships that
#:    constant is :file:`mba_foundation/markers.py`.
#: 2. The MBA skill — copied verbatim to the consumer's
#:    ``.agents/skills/mba/SKILL.md``. The file that ships that
#:    content is
#:    :file:`mba_foundation/resources/skills/mba/SKILL.md`.
#: 3. The OpenCode bootstrap root config — copied verbatim to
#:    ``opencode.json``. The source file is
#:    :file:`mba_foundation/resources/opencode/opencode.json`.
#: 4. The OpenCode bootstrap primary-mode MBA Orchestrator agent —
#:    copied verbatim to ``.opencode/agents/mba.md``. The source
#:    file is
#:    :file:`mba_foundation/resources/opencode/agents/mba.md`.
#:
#: 5. The OpenCode MBA worker agent is copied verbatim to
#:    ``.opencode/agents/mba-worker.md`` so workers can run on a
#:    role-specific surface instead of the ``mba`` Orchestrator agent.
#:
#: All five source files are also members of
#: :data:`PRODUCT_PATTERNS`; the install-content set is a *policy*
#: annotation, not a separate filesystem location.
INSTALL_CONTENT_PATTERNS: tuple[str, ...] = (
    "mba_foundation/markers.py",
    "mba_foundation/resources/skills/mba/SKILL.md",
    "mba_foundation/resources/opencode/opencode.json",
    "mba_foundation/resources/opencode/agents/mba.md",
    "mba_foundation/resources/opencode/agents/mba-worker.md",
)


#: Path patterns that are NEVER shipped with the MBA product and
#: NEVER installed into a consumer project. A copy that picks up
#: any of these has copied the wrong things.
EXCLUDED_PATTERNS: tuple[str, ...] = (
    # Local Beads dogfooding state (database, metadata, issues
    # export, hooks).
    ".beads/**",
    # Local working evidence (per-bead session directories, mode
    # file, AI-resource record). The mode file and AI-resource
    # record are subsumed by the broad ``.mba-work/`` rule; the
    # explicit entries are defensive in case a future
    # ``shared``-mode migration lifts the carrier.
    ".mba-work/**",
    ".mba-work/.ai-resources*",
    ".mba-work/.mba-mode",
    ".mba-work/.marker-baseline.json",
    # VCS metadata. The boundary must work before the first commit
    # and after; ``.git/`` is never product.
    ".git/**",
    # Python tooling-generated cache (Foundation F5).
    "__pycache__/**",
    "**/*.pyc",
    "**/*.pyo",
    "**/*.pyd",
    # pytest runtime cache.
    ".pytest_cache/**",
    # Beads-managed surfaces (skill + Claude hooks). Per
    # ``docs/beads/capabilities.md`` Setup integration row, MBA
    # uses the Beads-generated surfaces unchanged and does not
    # ship or edit them.
    ".claude/**",
    ".agents/**",
    # Standard Python packaging artefacts.
    "**/*.egg-info/**",
    "build/**",
    "dist/**",
    # OS / editor noise.
    "**/.DS_Store",
    "**/Thumbs.db",
)


#: Bucket names — the public string vocabulary of
#: :func:`classify_path`. Use these constants instead of literals so
#: refactors stay typed.
BUCKET_PRODUCT: str = "product"
BUCKET_INSTALL_CONTENT: str = "install_content"
BUCKET_EXCLUDED: str = "excluded"
BUCKET_UNCLASSIFIED: str = "unclassified"

#: Mapping of bucket name → tuple of patterns. Convenient for
#: callers that want to iterate "all categories at once" (e.g. the
#: audit surface) without re-listing the pattern tuples.
PATTERNS_BY_BUCKET: dict[str, tuple[str, ...]] = {
    BUCKET_PRODUCT: PRODUCT_PATTERNS,
    BUCKET_INSTALL_CONTENT: INSTALL_CONTENT_PATTERNS,
    BUCKET_EXCLUDED: EXCLUDED_PATTERNS,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_posix_relpath(path: Path, repo_root: Path) -> str:
    """Return ``path`` relative to ``repo_root`` as a POSIX string.

    The product / install / excluded patterns are POSIX-style globs
    and Python's :class:`pathlib.Path.glob` accepts both POSIX and
    native separators on Windows; classification is done with
    :func:`fnmatch.fnmatch`, which is separator-sensitive on
    Windows. We normalise to POSIX so a single match function works
    everywhere.
    """

    rel = path.resolve().relative_to(repo_root.resolve())
    return rel.as_posix()


def _iter_matching(repo_root: Path, pattern: str) -> Iterator[Path]:
    """Yield absolute paths under ``repo_root`` that match ``pattern``.

    Uses :meth:`pathlib.Path.glob`, which natively supports the
    ``**`` recursive syntax. Patterns that contain literal segments
    (``mba_foundation/``) are anchored at ``repo_root``; patterns
    that begin with ``**/`` match at any depth. The implementation
    never invokes ``git`` — a fresh checkout with no commits is
    supported.
    """

    base = repo_root.resolve()
    for path in base.glob(pattern):
        if not path.is_file():
            continue
        yield path


def _matches_any(rel_posix: str, patterns: tuple[str, ...]) -> bool:
    """Return True iff ``rel_posix`` matches any of the glob
    ``patterns`` (anchored at the repo root)."""

    for pattern in patterns:
        if _glob_to_regex(pattern).match(rel_posix):
            return True
    return False


def _glob_to_regex(pattern: str) -> "re.Pattern[str]":
    """Convert a POSIX-style glob with ``**`` support into an
    anchored :mod:`re` pattern.

    Rules:

    * ``**`` — match zero or more directory segments. The form
      ``**/`` (followed by a separator) is rewritten so the trailing
      segments match at any depth; the bare form ``**`` matches any
      remaining characters (including separators).
    * ``*`` — match any non-separator characters within a segment.
    * ``?`` — match a single non-separator character.
    * ``[...]`` — character class, passed through unchanged.
    * Any other character is matched literally.

    The result is anchored at both ends (``^...$``) so a partial
    match cannot succeed.
    """

    import re

    i = 0
    out: list[str] = []
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                # ``**/`` — match zero or more directory segments
                # followed by a separator. Consume the trailing
                # slash so the next pattern segment does not have
                # to match an empty separator.
                if i + 2 < n and pattern[i + 2] == "/":
                    out.append(r"(?:.*/)?")
                    i += 3
                else:
                    out.append(r".*")
                    i += 2
            else:
                out.append(r"[^/]*")
                i += 1
        elif c == "?":
            out.append(r"[^/]")
            i += 1
        elif c == "[":
            end = pattern.find("]", i)
            if end == -1:
                out.append(re.escape("["))
                i += 1
            else:
                out.append(pattern[i : end + 1])
                i = end + 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


# ---------------------------------------------------------------------------
# Public iteration surface
# ---------------------------------------------------------------------------


def iter_product_files(repo_root: Path) -> Iterator[Path]:
    """Yield absolute paths of every product file under ``repo_root``.

    The iteration deduplicates by absolute path: a file that matches
    more than one product pattern is yielded exactly once. The order
    is filesystem-walk order and is not otherwise specified.
    """

    seen: set[Path] = set()
    for pattern in PRODUCT_PATTERNS:
        for path in _iter_matching(repo_root, pattern):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield resolved


def iter_install_content_files(repo_root: Path) -> Iterator[Path]:
    """Yield absolute paths of every install-content file under
    ``repo_root``.

    Install content is a subset of the product; the iteration is
    deduplicated and ordered by install-content pattern (not by
    filesystem walk).
    """

    seen: set[Path] = set()
    for pattern in INSTALL_CONTENT_PATTERNS:
        for path in _iter_matching(repo_root, pattern):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield resolved


def iter_excluded_paths(repo_root: Path) -> Iterator[Path]:
    """Yield absolute paths of every excluded file under ``repo_root``.

    Used by future audit surfaces ("what would a copy-paste of the
    repo include if the boundary didn't exist?") and by the
    acceptance check in :func:`assert_no_overlap`. Order is
    filesystem-walk order.
    """

    seen: set[Path] = set()
    for pattern in EXCLUDED_PATTERNS:
        for path in _iter_matching(repo_root, pattern):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield resolved


# ---------------------------------------------------------------------------
# Classification + diagnostics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoundarySummary:
    """Per-bucket file counts for a single ``repo_root``.

    A future ``mba status`` surface consumes this; tests assert on
    the shape (keys + integer values) rather than exact counts so a
    new file in a product pattern does not silently break the test
    suite.
    """

    product: int
    install_content: int
    excluded: int

    @property
    def total(self) -> int:
        """Total number of distinct files covered by the boundary.

        Install-content files are also product files by design (the
        install-content set is a policy annotation, not a separate
        filesystem location), so the ``install_content`` count does
        not add to the ``total``. The ``total`` is therefore the
        distinct-union size: ``product + excluded`` for any repo in
        which ``INSTALL_CONTENT_PATTERNS ⊆ PRODUCT_PATTERNS`` (which
        :func:`summarize` always assumes — see
        :func:`assert_no_overlap` for the cross-bucket check).
        """
        return self.product + self.excluded


def classify_path(rel_path: str, repo_root: Path) -> str:
    """Classify a single relative path against the three buckets.

    Precedence (highest to lowest):

    1. :data:`BUCKET_EXCLUDED` — local state wins. A ``.pyc`` file
       under ``mba_foundation/`` is still excluded; a future
       ``.pyc`` accidentally produced inside a consumer's project
       must not become part of any install.
    2. :data:`BUCKET_INSTALL_CONTENT` — a marker of intent. A file
       in the install-content set is a source for ``mba init`` /
       ``mba upgrade`` to write into a consumer's repo.
    3. :data:`BUCKET_PRODUCT` — the catch-all "this ships" bucket.
    4. :data:`BUCKET_UNCLASSIFIED` — neither bucket matched. The
       file may be project-owned (e.g. ``AGENTS.md``, ``CLAUDE.md``,
       ``docs/archive/``) or research material. A copy should
       preserve it byte-for-byte but not include it in any install
       operation.

    The path is normalised to POSIX before matching so Windows
    backslashes and POSIX forward slashes both work.
    """

    rel = Path(rel_path).as_posix()
    if _matches_any(rel, EXCLUDED_PATTERNS):
        return BUCKET_EXCLUDED
    if _matches_any(rel, INSTALL_CONTENT_PATTERNS):
        return BUCKET_INSTALL_CONTENT
    if _matches_any(rel, PRODUCT_PATTERNS):
        return BUCKET_PRODUCT
    return BUCKET_UNCLASSIFIED


def summarize(repo_root: Path) -> BoundarySummary:
    """Return the per-bucket file counts for ``repo_root``.

    Counts every product / install-content / excluded file
    independently. Install-content files are *also* product files by
    design (the install-content set is a policy annotation, not a
    separate filesystem location), so the ``install_content`` count
    does not add to the ``product`` count in the ``total`` field.
    Use :func:`assert_no_overlap` for the cross-bucket consistency
    check.
    """

    product = sum(1 for _ in iter_product_files(repo_root))
    install = sum(1 for _ in iter_install_content_files(repo_root))
    excluded = sum(1 for _ in iter_excluded_paths(repo_root))
    return BoundarySummary(product=product, install_content=install, excluded=excluded)


@dataclass(frozen=True)
class OverlapReport:
    """Cross-bucket consistency report for a single ``repo_root``."""

    excluded_and_product: tuple[Path, ...]
    excluded_and_install: tuple[Path, ...]

    @property
    def has_overlap(self) -> bool:
        return bool(self.excluded_and_product) or bool(self.excluded_and_install)


def assert_no_overlap(repo_root: Path) -> OverlapReport:
    """Return a report flagging any path that belongs to the
    excluded bucket AND the product or install-content bucket.

    The configuration is correct-by-construction — the EXCLUDED
    patterns are a superset of the Python cache / Beads / VCS
    directories and a product pattern rarely overlaps. The check is
    provided so a future edit to the pattern lists cannot silently
    introduce a violation.
    """

    excluded_set = {p.resolve() for p in iter_excluded_paths(repo_root)}
    product_set = {p.resolve() for p in iter_product_files(repo_root)}
    install_set = {p.resolve() for p in iter_install_content_files(repo_root)}

    excluded_and_product = tuple(sorted(excluded_set & product_set))
    excluded_and_install = tuple(sorted(excluded_set & install_set))
    return OverlapReport(
        excluded_and_product=excluded_and_product,
        excluded_and_install=excluded_and_install,
    )


def assert_install_set_safe(install_paths: list[Path], repo_root: Path) -> tuple[bool, str]:
    """Return ``(ok, reason)`` confirming ``install_paths`` contains
    no excluded paths.

    A future ``mba upgrade`` will pass its planned install set
    through this helper. The check is exact — every planned
    destination must be either a product file in the source repo or
    a target install path in the consumer repo, never an excluded
    path. ``repo_root`` is used to compute relative paths for the
    error message.
    """

    excluded_set = {p.resolve() for p in iter_excluded_paths(repo_root)}
    leaks: list[str] = []
    for candidate in install_paths:
        resolved = candidate.resolve()
        if resolved in excluded_set:
            try:
                rel = resolved.relative_to(repo_root.resolve()).as_posix()
            except ValueError:
                rel = str(resolved)
            leaks.append(rel)
    if not leaks:
        return True, ""
    return False, (
        "refuse: install set contains excluded paths: "
        + ", ".join(sorted(leaks))
    )


def install_content_targets() -> tuple[str, ...]:
    """Return the consumer-project target paths for the install
    content.

    The mapping is intentionally static today: there is exactly one
    canonical MBA RULES block, exactly one canonical MBA skill, the
    docs/assets referenced by the installed MBA RULES block or skill,
    and the OpenCode-bootstrap agent and root config so an OpenCode
    cold start acts as the MBA Orchestrator before any project
    exploration or work. A future Bead that adds an install surface
    (e.g. a default ``AGENTS.md`` template) appends a new entry here.

    The function is the single source of truth for *where* the
    install content lands in a consumer repo. ``mba init`` reads
    it; ``mba upgrade`` compares it against the consumer's current
    state.
    """

    return (
        "AGENTS.md",                  # The MBA RULES block (inside the
                                      # literal marker pair).
        "CLAUDE.md",                  # Same MBA RULES block in the
                                      # project-local copy.
        "docs/mba/charter.md",        # Normative workflow charter
                                      # referenced by the MBA RULES
                                      # block and the MBA skill.
        "docs/beads/capabilities.md", # Beads capability record
                                      # referenced by both the MBA
                                      # RULES block and the MBA skill.
        ".agents/skills/mba/SKILL.md",  # The MBA skill (verbatim copy).
        "opencode.json",              # OpenCode bootstrap root config:
                                      # ``default_agent=mba`` plus
                                      # ``instructions=["AGENTS.md"]``
                                      # so a fresh consumer activates
                                      # as the MBA Orchestrator.
        ".opencode/agents/mba.md",    # OpenCode bootstrap primary-mode
                                      # MBA Orchestrator agent that
                                      # mandates ``bd version`` +
                                      # ``python -m mba_runtime
                                      # first-contact --cwd .
                                      # --apply-setup`` before
                                      # exploration or worker launches.
        ".opencode/agents/mba-worker.md",  # OpenCode MBA Doer/Auditor
                                      # worker agent launched with
                                      # ``--agent mba-worker``.
    )


def install_content_managed_block_targets() -> tuple[str, ...]:
    """Return the subset of :func:`install_content_targets` that are
    managed-block targets (the consumer receives the MBA RULES block
    inside the marker pair)."""

    return tuple(
        relpath for relpath in install_content_targets()
        if relpath in MANAGED_BLOCK_TARGETS
    )


def install_content_verbatim_copy_targets() -> tuple[str, ...]:
    """Return the subset of :func:`install_content_targets` that are
    verbatim-copy targets (the consumer receives the source text
    byte-for-byte)."""

    return tuple(
        relpath for relpath in install_content_targets()
        if relpath not in MANAGED_BLOCK_TARGETS
    )


#: The consumer targets that receive a managed MBA RULES block.
MANAGED_BLOCK_TARGETS: frozenset[str] = frozenset({"AGENTS.md", "CLAUDE.md"})


__all__ = [
    "PRODUCT_PATTERNS",
    "INSTALL_CONTENT_PATTERNS",
    "EXCLUDED_PATTERNS",
    "BUCKET_PRODUCT",
    "BUCKET_INSTALL_CONTENT",
    "BUCKET_EXCLUDED",
    "BUCKET_UNCLASSIFIED",
    "PATTERNS_BY_BUCKET",
    "MANAGED_BLOCK_TARGETS",
    "BoundarySummary",
    "OverlapReport",
    "iter_product_files",
    "iter_install_content_files",
    "iter_excluded_paths",
    "classify_path",
    "summarize",
    "assert_no_overlap",
    "assert_install_set_safe",
    "install_content_targets",
    "install_content_managed_block_targets",
    "install_content_verbatim_copy_targets",
]
