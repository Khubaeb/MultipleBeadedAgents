"""``.mba/manifest.json`` schema + drift / upgrade planning.

The "target-repo flow" pinned in :file:`docs/mba/README.md:23-25`:

* ``mba init`` records the installed content + checksums in
  ``.mba/manifest.json``.
* ``mba status`` reports the recorded install + drift vs. the current
  MBA source.
* ``mba upgrade [--dry-run]`` previews then applies a refresh from the
  current MBA source.

This module is the single source of truth for the manifest's shape and
for the drift / upgrade logic. The three CLI subcommands in
:mod:`mba_foundation.cli` call into it.

## Three install surfaces

For the current MBA package, the install surface (the set of files a
``mba init`` writes into a target repo) is exactly the
:func:`mba_foundation.product_boundary.install_content_targets` set:

* ``AGENTS.md`` and ``CLAUDE.md`` — each gets the MBA RULES block
  installed via :func:`mba_foundation.markers.install_block`. The block
  is a ``managed_block`` record: the manifest records the SHA256 of the
  block body captured between the ``<!-- BEGIN MBA RULES -->`` /
  ``<!-- END MBA RULES -->`` marker pair at install time. Drift is
  detected by comparing the on-disk block body's SHA256 to the
  recorded one.
* ``.agents/skills/mba/SKILL.md`` — the MBA skill is copied verbatim
  from ``mba_foundation/resources/skills/mba/SKILL.md``. The manifest
  records the source file's SHA256 at install time. Drift is detected
  by comparing the on-disk file's SHA256 to the recorded one.

A future install surface (e.g. a default ``AGENTS.md`` template) appends
a new entry to :func:`product_boundary.install_content_targets` and
(separately) an ``InstalledFileEntry`` kind here; that mapping keeps the
two lists in lock-step.

## Drift rule

For each managed file the consumer repo can be in one of three states
at the time of a status / upgrade call:

* **not_installed** — target file is missing OR the marker pair is
  missing (for ``managed_block``).
* **unchanged** — on-disk state equals the **recorded** install state
  (block body's SHA256 equals the recorded ``block_sha256``; verbatim
  file's SHA256 equals the recorded ``file_sha256``).
* **user_edited** — on-disk state differs from the recorded state.

The rule reads as: **if the on-disk state equals what we last installed,
it isn't a user edit; otherwise it is.** An MBA upstream change is *not*
a user edit and is reflected by ``upgrade_available`` in the status
report (the on-disk content matches a *new* MBA version). User-edited
content is **always** a conflict — the Manager refuses to overwrite and
demands an explicit user decision per Charter §11.

## Why "recorded" sha, not "current upstream" sha

The on-disk equals recorded check is intentionally weaker than the
on-disk equals current-upstream check. Reasons:

1. ``mba status`` must work without consulting upstream (an offline
   state check is part of the value). Reading the recorded manifest is
   sufficient.
2. ``mba upgrade`` must distinguish between
   "the user edited the block" and "MBA shipped a new block" — both
   make on-disk ≠ old_upstream. Only the recorded-sha check reliably
   tells them apart: if on-disk ≠ recorded → user edited.
3. The Beads-version preflight gate (per
   :mod:`mba_foundation.preflight`) is the gate, not the manifest — so
   a stale recorded SHA is not a security issue; it is at worst a "you
   haven't upgraded in a while" hint surfaced in the status report.

## No silent `bd init`

The Manifest module never invokes ``bd init``. Beads install is gated
by :func:`mba_foundation.detect.install_or_initialize` which requires
explicit user authority. This module's ``install_or_initialize_target``
helper just records what was written; it does not materialise Beads.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import shutil
import site
import sysconfig
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Iterable

from .markers import (
    MBA_RULES_BEGIN_MARKER,
    MBA_RULES_END_MARKER,
    MBA_RULES_BLOCK,
)

# Reuse the marker module's regexes — F5 consolidation. Importing them
# avoids a duplicate ``re.compile`` here that could silently desync
# from :mod:`mba_foundation.markers`.
from .markers import _BEGIN_LINE_PATTERN, _END_LINE_PATTERN  # noqa: F401  (re-exported)


__all__ = [
    "SCHEMA_VERSION",
    "MANIFEST_RELPATH",
    "KIND_MANAGED_BLOCK",
    "KIND_VERBATIM_COPY",
    "SOURCE_PACKAGED",
    "SOURCE_SOURCE",
    "STATE_NOT_INSTALLED",
    "STATE_UNCHANGED",
    "STATE_USER_EDITED",
    "PreflightEvidence",
    "ManagedFileEntry",
    "Manifest",
    "DriftEntry",
    "DriftReport",
    "UpgradePlanEntry",
    "UpgradePlan",
    "RemovePlanEntry",
    "RemovePlan",
    "ManifestConflictError",
    "UpgradeBlockedError",
    "manifest_path",
    "sha256_text",
    "extract_block_body",
    "current_block_body",
    "build_manifest",
    "write_manifest",
    "read_manifest",
    "install_content_root",
    "detect_drift",
    "plan_upgrade",
    "apply_upgrade",
    "plan_remove",
    "apply_remove",
]


SCHEMA_VERSION: int = 1
MANIFEST_RELPATH: str = ".mba/manifest.json"
MANIFEST_DIR: str = ".mba"

KIND_MANAGED_BLOCK: str = "managed_block"
KIND_VERBATIM_COPY: str = "verbatim_copy"

SOURCE_PACKAGED: str = "packaged"
SOURCE_SOURCE: str = "source"

STATE_NOT_INSTALLED: str = "not_installed"
STATE_UNCHANGED: str = "unchanged"
STATE_USER_EDITED: str = "user_edited"


def sha256_text(text: str) -> str:
    """Return the SHA256 hex digest of ``text`` (UTF-8 encoded)."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_path(path: Path) -> str:
    """Return the SHA256 hex digest of the file at ``path``.

    Reads in text mode with UTF-8 decoding so the digest matches the
    digest of an identical byte sequence reconstructed from a Python
    string. The manifest is text-only and the install/upgrade cycles
    never carry binary content, so binary-mode hashing would only add
    portability noise.
    """

    return sha256_text(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Manifest schema (dataclasses + JSON round-trip)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreflightEvidence:
    """A recorded `bd version` preflight result.

    The shape is intentionally narrow: bd_version, whether it matched
    the validated set, and the raw ``bd version`` stdout verbatim. The
    manifest carries the same data so a future manager can replay the
    evidence without re-running the preflight.
    """

    bd_version: str | None
    matches_record: bool
    raw_output: str

    def to_dict(self) -> dict[str, object]:
        return {
            "bd_version": self.bd_version,
            "matches_record": self.matches_record,
            "raw_output": self.raw_output,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "PreflightEvidence":
        return cls(
            bd_version=data.get("bd_version") if isinstance(data.get("bd_version"), str) else None,
            matches_record=bool(data.get("matches_record")),
            raw_output=str(data.get("raw_output", "")),
        )


@dataclass(frozen=True)
class ManagedFileEntry:
    """One row of the manifest's ``files`` list."""

    relpath: str
    kind: str
    # managed_block: SHA256 of the block body captured between the MBA
    # marker pair at install time.
    # verbatim_copy: SHA256 of the source file content at install time.
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "relpath": self.relpath,
            "kind": self.kind,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ManagedFileEntry":
        relpath = str(data.get("relpath", ""))
        kind = str(data.get("kind", ""))
        sha = str(data.get("sha256", ""))
        if not relpath or not kind or not sha:
            raise ValueError(
                f"ManagedFileEntry missing required field: relpath={relpath!r}, "
                f"kind={kind!r}, sha256={sha!r}"
            )
        return cls(relpath=relpath, kind=kind, sha256=sha)


@dataclass(frozen=True)
class Manifest:
    """The ``.mba/manifest.json`` shape.

    The dataclass carries both the **persisted** fields (schema, version,
    source, timestamp, preflight, files) and an **in-memory** mapping of
    ``(relpath, body)`` pairs the manifest was built with. The bodies
    live only in memory — they never round-trip through JSON —
    because the persisted schema already carries the
    content-fingerprint (sha256) the drift check needs, and the
    bodies are easily re-derivable from the live MBA package the next
    time ``mba upgrade`` runs. The bodies exist so :func:`apply_upgrade`
    can write the upstream content into a consumer repo without
    having to re-import the underlying MBA package.
    """

    schema: int
    mba_version: str
    source: str
    installed_at: str  # ISO-8601 UTC timestamp
    preflight: PreflightEvidence
    files: tuple[ManagedFileEntry, ...] = ()
    # In-memory only — not persisted by :meth:`to_dict`, never read by
    # :meth:`from_dict`. A tuple-of-pairs keeps the dataclass frozen
    # so :func:`dataclasses.replace` works.
    upstream_bodies: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "mba_version": self.mba_version,
            "source": self.source,
            "installed_at": self.installed_at,
            "preflight": self.preflight.to_dict(),
            "files": [entry.to_dict() for entry in self.files],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Manifest":
        schema = int(data.get("schema", 0))  # type: ignore[arg-type]
        if schema != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported manifest schema: {schema!r} "
                f"(expected {SCHEMA_VERSION}); refusing to read"
            )
        mba_version = str(data.get("mba_version", ""))
        source = str(data.get("source", ""))
        installed_at = str(data.get("installed_at", ""))
        preflight_data = data.get("preflight")
        if not isinstance(preflight_data, dict):
            raise ValueError("manifest.preflight must be an object")
        files_data = data.get("files") or []
        if not isinstance(files_data, list):
            raise ValueError("manifest.files must be a list")
        files = tuple(ManagedFileEntry.from_dict(f) for f in files_data)  # type: ignore[arg-type]
        return cls(
            schema=schema,
            mba_version=mba_version,
            source=source,
            installed_at=installed_at,
            preflight=PreflightEvidence.from_dict(preflight_data),
            files=files,
        )

    def file_for(self, relpath: str) -> ManagedFileEntry | None:
        for entry in self.files:
            if entry.relpath == relpath:
                return entry
        return None

    def body_for(self, relpath: str) -> str | None:
        """Return the in-memory upstream body for ``relpath`` or ``None``.

        Reads from :attr:`upstream_bodies`. The bodies are populated
        by :func:`build_manifest` for upstream manifests that were
        freshly built (e.g. during ``mba upgrade``). Installed
        manifests reloaded from disk have no bodies; the apply path
        computes the body from the live MBA package in that case.
        """

        for path, body in self.upstream_bodies:
            if path == relpath:
                return body
        return None


# ---------------------------------------------------------------------------
# Block extraction — single source of truth: the markers module's regexes.
# ---------------------------------------------------------------------------


def extract_block_body(text: str) -> tuple[int, int, str] | None:
    """Return ``(begin_idx, end_idx, body)`` for the MBA block in ``text``.

    ``begin_idx`` and ``end_idx`` are byte offsets into ``text`` for the
    positions **after** the marker lines. ``body`` is the text between
    the two markers, exactly as recorded by the installer (the
    ``MBA_RULES_BLOCK`` literal from :mod:`mba_foundation.markers`).

    Returns ``None`` if the file has no MBA block, or ``(begin_idx,
    end_idx, body)`` for the first begin/end pair found. Files with
    multiple pairs are treated as malformed by an upstream caller (see
    :func:`mba_foundation.markers.assert_beads_markers_untouched`); the
    manager only needs to read the canonical block, so the "first pair"
    rule is consistent.
    """

    m_begin = _BEGIN_LINE_PATTERN.search(text)
    m_end = _END_LINE_PATTERN.search(text)
    if not m_begin or not m_end:
        return None
    if m_end.start() <= m_begin.end():
        return None
    body = text[m_begin.end():m_end.start()]
    return m_begin.end(), m_end.start(), body


def current_block_body(file_path: Path) -> tuple[str, str] | tuple[None, None]:
    """Return ``(body, error)``.

    On success: the body text between the MBA BEGIN/END marker pair in
    ``file_path``. On failure: a non-empty error string explaining
    why no body could be extracted (``None, "..."``).

    A missing file or a file with no MBA block is treated as
    "not installed" and reported with an explanatory error string.
    """

    if not file_path.exists():
        return None, f"file does not exist: {file_path}"
    text = file_path.read_text(encoding="utf-8")
    extracted = extract_block_body(text)
    if extracted is None:
        return None, (
            f"no MBA RULES block in {file_path} "
            f"(expected one BEGIN/END MBA RULES pair)"
        )
    _, _, body = extracted
    return body, ""


# ---------------------------------------------------------------------------
# Manifest build / write / read
# ---------------------------------------------------------------------------


def manifest_path(root: Path) -> Path:
    """Return the canonical ``<root>/.mba/manifest.json`` path."""

    return (root / MANIFEST_RELPATH).resolve()


def _now_iso() -> str:
    """ISO-8601 UTC timestamp, second precision.

    Using ``datetime.now(timezone.utc)`` (not ``datetime.utcnow()``,
    which is deprecated in 3.12+) and stripping the microseconds keeps
    the manifest diff-friendly.
    """

    now = datetime.now(timezone.utc).replace(microsecond=0)
    return now.isoformat()


def _current_mba_version() -> str:
    """``mba_version.__version__`` at the time of the call.

    Imported lazily so tests can monkeypatch the version. The function
    raises ``RuntimeError`` (not ``ImportError``) so the failure mode is
    explicit at the call site.
    """

    try:
        from mba_version import __version__
    except ImportError as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "mba_version module is not importable; the manifest cannot "
            "record an installed version"
        ) from exc
    return __version__


def _make_managed_block_entry(
    relpath: str,
    block_source: str,
) -> ManagedFileEntry:
    """Build a managed_block entry whose sha256 hashes the on-disk body.

    The recorded sha must equal the on-disk block body extracted by
    :func:`current_block_body`, otherwise every consumer that has not
    edited the block looks like a "user edit". The on-disk body is the
    text between the BEGIN/END MBA RULES marker lines — not the full
    :data:`mba_foundation.markers.MBA_RULES_BLOCK` literal (which
    includes the marker lines themselves).

    ``block_source`` is the canonical MBA RULES source (the
    :data:`MBA_RULES_BLOCK` literal). We run the same extraction that
    :func:`current_block_body` will later run against the on-disk file
    so the two are byte-identical.
    """

    extracted = extract_block_body(block_source)
    if extracted is not None:
        _, _, body = extracted
    else:
        # Defensive: caller passed a string that has no marker pair.
        # The install will still produce a BEGIN/END pair on disk (the
        # installer prepends those lines), so the on-disk body will
        # differ from any sha we record here. Treat as an empty body
        # hash; the drift will surface as ``not_installed``.
        body = ""
    return ManagedFileEntry(
        relpath=relpath,
        kind=KIND_MANAGED_BLOCK,
        sha256=sha256_text(body),
    )


def _make_verbatim_copy_entry(
    relpath: str,
    source_text: str,
) -> ManagedFileEntry:
    """Build a verbatim_copy entry whose sha256 hashes ``source_text``."""

    return ManagedFileEntry(
        relpath=relpath,
        kind=KIND_VERBATIM_COPY,
        sha256=sha256_text(source_text),
    )


def build_manifest(
    *,
    source: str = SOURCE_PACKAGED,
    preflight_evidence: PreflightEvidence | None = None,
    installed_at: str | None = None,
    mba_version: str | None = None,
    managed_block_targets: tuple[str, ...] = ("AGENTS.md", "CLAUDE.md"),
    verbatim_copy_targets: tuple[str, ...] = (
        ".agents/skills/mba/SKILL.md",
        "opencode.json",
        ".opencode/agents/mba.md",
        ".opencode/agents/mba-worker.md",
    ),
    source_block_body: str | None = None,
    source_skill_text: str | None = None,
    verbatim_copy_sources: dict[str, str] | None = None,
) -> Manifest:
    """Return a :class:`Manifest` describing a fresh install.

    The default arguments match the current install surface
    (AGENTS.md, CLAUDE.md, ``.agents/skills/mba/SKILL.md``). Tests
    can substitute ``source_block_body`` and ``source_skill_text`` to
    pin the upstream content; in production both default to the
    canonical :data:`mba_foundation.markers.MBA_RULES_BLOCK` and the
    on-disk skill text under the active Python import path.

    The ``verbatim_copy_sources`` mapping lets callers pin the
    source text for any verbatim-copy target by relpath. Production
    code lets the function read each target's source text from the
    repo (see :func:`_read_verbatim_copy_source_text`); tests use the
    mapping to substitute deterministic content per target.

    The returned manifest also carries an in-memory ``upstream_bodies``
    mapping so :func:`apply_upgrade` can write the upstream content
    into a consumer repo without re-importing MBA. The mapping is
    never persisted; :meth:`Manifest.to_dict` omits it and
    :meth:`Manifest.from_dict` always returns ``upstream_bodies=()``.
    """

    block_source = (
        source_block_body if source_block_body is not None else MBA_RULES_BLOCK
    )

    files: list[ManagedFileEntry] = []
    bodies: list[tuple[str, str]] = []

    for relpath in managed_block_targets:
        # Compute the body we will write between markers + its sha.
        # The body is the slice of the block source between the
        # BEGIN/END marker lines, identical to what
        # :func:`current_block_body` will read on disk after install.
        extracted = extract_block_body(block_source)
        if extracted is not None:
            _, _, body = extracted
        else:
            # Caller passed raw text without markers. Treat the entire
            # text as the body. This branch is rare (only relevant for
            # tests) — the canonical install always passes a full
            # ``MBA_RULES_BLOCK``.
            body = block_source
        bodies.append((relpath, body))
        files.append(_make_managed_block_entry(relpath, block_source))

    verbatim_copy_sources = verbatim_copy_sources or {}
    for relpath in verbatim_copy_targets:
        text = _resolve_verbatim_copy_source(
            relpath,
            source_skill_text=source_skill_text,
            verbatim_copy_sources=verbatim_copy_sources,
        )
        bodies.append((relpath, text))
        files.append(_make_verbatim_copy_entry(relpath, text))

    return Manifest(
        schema=SCHEMA_VERSION,
        mba_version=mba_version if mba_version is not None else _current_mba_version(),
        source=source,
        installed_at=installed_at if installed_at is not None else _now_iso(),
        preflight=preflight_evidence
        if preflight_evidence is not None
        else PreflightEvidence(bd_version=None, matches_record=False, raw_output=""),
        files=tuple(files),
        upstream_bodies=tuple(bodies),
    )


def _resolve_verbatim_copy_source(
    relpath: str,
    *,
    source_skill_text: str | None,
    verbatim_copy_sources: dict[str, str],
) -> str:
    """Return the source text for a verbatim-copy consumer target.

    Resolution order:

    1. ``verbatim_copy_sources[relpath]`` if the caller pinned it.
    2. ``source_skill_text`` when ``relpath`` is the MBA skill.
    3. The packaged-resource helpers when ``relpath`` is an OpenCode
       bootstrap target (``opencode.json``,
       ``.opencode/agents/mba.md`` or
       ``.opencode/agents/mba-worker.md``).
    4. :func:`_read_verbatim_copy_source_text` — reads the
       canonical source for any other relpath from the active MBA
       repo (docs + the MBA skill).
    """

    if relpath in verbatim_copy_sources:
        return verbatim_copy_sources[relpath]
    if relpath == ".agents/skills/mba/SKILL.md" and source_skill_text is not None:
        return source_skill_text
    if relpath == "opencode.json":
        return _read_opencode_config_source_text()
    if relpath == ".opencode/agents/mba.md":
        return _read_opencode_agent_source_text()
    if relpath == ".opencode/agents/mba-worker.md":
        return _read_opencode_worker_agent_source_text()
    return _read_verbatim_copy_source_text(relpath)


def _read_skill_source_text() -> str:
    """Read the MBA skill source text from the active Python import.

    The skill ships in the installed package as package data under
    ``mba_foundation/resources/skills/mba/SKILL.md``. ``mba init`` then
    writes that text into the consumer repo at
    ``.agents/skills/mba/SKILL.md``.
    """

    resource_path = "resources/skills/mba/SKILL.md"
    try:
        return (
            resources.files("mba_foundation")
            .joinpath(resource_path)
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError):
        here = Path(__file__).resolve()
        candidates = [
            here.parent / resource_path,
            here.parents[1] / "mba_foundation" / resource_path,
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
    raise FileNotFoundError(
        "MBA skill source not found; tried: "
        + ", ".join(str(c) for c in candidates)
    )


def _read_packaged_resource_text(package_relative_path: str) -> str:
    """Read a packaged resource from ``mba_foundation`` package data.

    The helper is the OpenCode-bootstrap analogue of
    :func:`_read_skill_source_text`: the file ships as package data
    under ``mba_foundation/resources/...`` so an installed package
    (``pip install multiple-beaded-agents``) can resolve it without a
    filesystem layout assumption. The fallback chain mirrors the
    skill's: source checkout, ``mba_foundation`` parent, then raise.
    """

    try:
        return (
            resources.files("mba_foundation")
            .joinpath(package_relative_path)
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError):
        here = Path(__file__).resolve()
        candidates = [
            here.parent / package_relative_path,
            here.parents[1] / "mba_foundation" / package_relative_path,
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
    raise FileNotFoundError(
        f"packaged resource not found: {package_relative_path!r}; "
        f"tried: " + ", ".join(str(c) for c in candidates)
    )


def _read_opencode_config_source_text() -> str:
    """Return the source text for the consumer-side ``opencode.json``.

    The OpenCode bootstrap root config (``default_agent=mba``,
    ``instructions=["AGENTS.md"]``) ships as package data under
    ``mba_foundation/resources/opencode/opencode.json``. ``mba init``
    writes that text into the consumer repo at ``opencode.json``.
    """

    return _read_packaged_resource_text("resources/opencode/opencode.json")


def _read_opencode_agent_source_text() -> str:
    """Return the source text for the consumer-side
    ``.opencode/agents/mba.md``.

    The MBA Orchestrator primary-mode agent ships as package data
    under ``mba_foundation/resources/opencode/agents/mba.md``.
    ``mba init`` writes that text into the consumer repo at
    ``.opencode/agents/mba.md``.
    """

    return _read_packaged_resource_text("resources/opencode/agents/mba.md")


def _read_opencode_worker_agent_source_text() -> str:
    """Return the source text for the consumer-side
    ``.opencode/agents/mba-worker.md``.

    The MBA Doer/Auditor worker agent ships as package data under
    ``mba_foundation/resources/opencode/agents/mba-worker.md``.
    ``mba init`` writes that text into the consumer repo at
    ``.opencode/agents/mba-worker.md``.
    """

    return _read_packaged_resource_text(
        "resources/opencode/agents/mba-worker.md"
    )


def _repo_root() -> Path:
    """Return the active MBA repository root.

    The root is the parent of the ``mba_foundation`` package. In a
    source checkout that is the repository root; in an installed
    package it is the on-disk location where ``mba_foundation``
    ships (typically ``site-packages/..``), which is enough for the
    verbatim-copy source lookup in this module.
    """

    # ``manifest.py`` lives at ``<repo>/mba_foundation/manifest.py``;
    # ``parents[1]`` is the repo root.
    return Path(__file__).resolve().parents[1]


def _packaged_data_root() -> Path:
    """Return the system-prefix shared MBA documentation root.

    ``sysconfig.get_path("data")`` is the data directory for the
    system-wide Python installation (``sys.base_prefix`` on POSIX,
    ``sys.prefix`` on Windows). When the package is installed via
    ``pip install`` (no flags) the data-files declared in
    ``pyproject.toml`` land here under ``share/doc/multiple-beaded-agents``.
    """

    return Path(sysconfig.get_path("data")) / "share" / "doc" / "multiple-beaded-agents"


def _user_site_data_root() -> Path:
    """Return the user-site shared MBA documentation root.

    When the package is installed via ``pip install --user`` the data
    files declared in ``pyproject.toml`` land under
    ``site.getuserbase() + "/share/doc/multiple-beaded-agents"`` rather
    than the system prefix. ``sysconfig.get_path("data")`` returns the
    system prefix even when ``pip --user`` is in effect, so the lookup
    must enumerate the user base as a separate candidate. The user
    base directory is stable across POSIX and Windows (``site`` returns
    the same string on each platform).
    """

    return Path(site.getuserbase()) / "share" / "doc" / "multiple-beaded-agents"


def _read_verbatim_copy_source_text(relpath: str) -> str:
    """Read the source text for any verbatim-copy consumer target.

    The function enumerates every plausible on-disk location for the
    relpath and returns the first hit. The lookup order is:

    1. The active MBA repository root (source checkout, editable
       install, or a checkout whose ``mba_foundation`` happens to live
       next to the docs).
    2. The system-prefix data-files root (``sysconfig.get_path("data")``
       + ``share/doc/multiple-beaded-agents``) — the canonical
       destination for ``pip install`` with no flags.
    3. The user-site data-files root (``site.getuserbase()`` +
       ``share/doc/multiple-beaded-agents``) — the canonical destination
       for ``pip install --user`` (e.g. the downstream test repo downstream install,
       which fails with missing ``docs/mba/charter.md`` when only the
       system prefix is checked).
    """

    if relpath == ".agents/skills/mba/SKILL.md":
        return _read_skill_source_text()
    candidates = (
        _repo_root() / relpath,
        _packaged_data_root() / relpath,
        _user_site_data_root() / relpath,
    )
    seen: set[Path] = set()
    unique_candidates: tuple[Path, ...] = tuple(
        c for c in candidates if not (c in seen or seen.add(c))
    )
    for candidate in unique_candidates:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    raise FileNotFoundError(
        f"verbatim-copy source not found for {relpath!r}; "
        f"tried {', '.join(str(candidate) for candidate in unique_candidates)}"
    )


def write_manifest(root: Path, manifest: Manifest) -> Path:
    """Atomically write ``manifest`` to ``<root>/.mba/manifest.json``.

    The atomic write (write to a sibling ``.tmp`` file, then rename)
    prevents a partial manifest from corrupting a future
    ``read_manifest`` call. The function creates ``.mba/`` if it does
    not exist and returns the resolved manifest path.
    """

    path = manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = manifest.to_dict()
    body = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    # ``newline="\n"`` keeps the JSON file portable: the manifest
    # uses LF-only line endings regardless of the host OS so a sha
    # computed on Windows matches the sha on POSIX. The default
    # ``newline=None`` writes ``\r\n`` on Windows and would make the
    # on-disk file's sha diverge from the recorded sha on a Unix host.
    tmp_path.write_text(body, encoding="utf-8", newline="\n")
    # ``shutil.move`` is atomic across filesystems on POSIX and rename-
    # on-top within the same directory on Windows. The tmp lives next
    # to the target so the rename is always within the same FS.
    shutil.move(str(tmp_path), str(path))
    return path


def read_manifest(root: Path) -> Manifest | None:
    """Return the manifest at ``<root>/.mba/manifest.json`` or ``None``.

    A manifest file that does not exist is the "not installed" case
    (status reports this). A manifest file with an unparseable body
    raises :class:`ValueError` so the manager can decide whether to
    refuse outright or treat it as a corrupted install requiring user
    attention. The function deliberately does not auto-repair.
    """

    path = manifest_path(root)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"manifest root must be an object, got {type(data).__name__}")
    return Manifest.from_dict(data)


def install_content_root() -> Path:
    """Return the ``<root>/.mba`` directory for a fresh install.

    Used by the manager to ensure the directory exists before writing
    the manifest. Other install content (the skill file) goes under
    ``<root>/.agents/skills/mba/``.
    """

    return Path(MANIFEST_DIR)


# ---------------------------------------------------------------------------
# Drift detection + upgrade planning
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DriftEntry:
    """One row of a :class:`DriftReport`."""

    relpath: str
    kind: str
    state: str                           # not_installed / unchanged / user_edited
    installed_sha: str | None            # recorded sha (None if state == not_installed and not in manifest)
    current_sha: str | None              # on-disk sha (None if state == not_installed)


@dataclass(frozen=True)
class DriftReport:
    """The full drift verdict against a manifest."""

    root: Path
    installed: Manifest | None           # None ⇢ not installed at all.
    entries: tuple[DriftEntry, ...] = ()

    @property
    def is_installed(self) -> bool:
        return self.installed is not None

    @property
    def has_conflicts(self) -> bool:
        """``True`` iff any on-disk state is ``user_edited``."""

        return any(entry.state == STATE_USER_EDITED for entry in self.entries)

    @property
    def user_edited_paths(self) -> tuple[str, ...]:
        return tuple(
            entry.relpath
            for entry in self.entries
            if entry.state == STATE_USER_EDITED
        )

    @property
    def has_drift(self) -> bool:
        """``True`` iff at least one entry is ``not_installed`` or ``user_edited``."""

        return any(
            entry.state != STATE_UNCHANGED
            for entry in self.entries
        )


def _target_safety_error(root: Path, relpath: str) -> str | None:
    """Return a refusal reason when a manifest target is unsafe to touch.

    Manifest paths are data from a previous install, not authority to escape
    the target repository.  Refuse absolute/non-canonical paths, traversal,
    symlinks (including symlinked parents), and non-file targets.  This is
    especially important for retirement: an obsolete manifest row must never
    turn into permission to delete user content outside the managed surface.
    """

    relative = Path(relpath)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        return "manifest relpath is absolute or non-canonical"

    base = root.resolve()
    target = base / relative
    try:
        target.resolve(strict=False).relative_to(base)
    except ValueError:
        return "manifest relpath resolves outside the target root"

    current = target
    while current != base:
        if current.is_symlink():
            return "manifest target or one of its parents is a symlink"
        current = current.parent
    if target.exists() and not target.is_file():
        return "manifest target exists but is not a regular file"
    return None


def _classify_one(
    relpath: str,
    root: Path,
    recorded_sha: str | None,
    kind: str,
) -> DriftEntry:
    """Classify a single managed file's on-disk state vs. ``recorded_sha``.

    Helper that hides the per-kind "how do I compute the current sha?"
    policy from :func:`detect_drift`. For ``managed_block`` the current
    sha is the block body between the MBA markers; if there's no MBA
    block on disk the file is ``not_installed``. For ``verbatim_copy``
    the current sha is the file's full content; a missing file is
    ``not_installed``.
    """

    target = root / relpath
    safety_error = _target_safety_error(root, relpath)
    if safety_error is not None:
        # Unsafe manifest rows are conflicts.  ``current_sha=None`` avoids
        # following a symlink or reading outside the target root merely to
        # improve diagnostics.
        return DriftEntry(
            relpath=relpath,
            kind=kind,
            state=STATE_USER_EDITED,
            installed_sha=recorded_sha,
            current_sha=None,
        )
    if kind == KIND_MANAGED_BLOCK:
        body, err = current_block_body(target)
        if body is None:
            # Missing or no MBA block → not installed (regardless of any
            # recorded sha — re-install is safe).
            return DriftEntry(
                relpath=relpath,
                kind=kind,
                state=STATE_NOT_INSTALLED,
                installed_sha=recorded_sha,
                current_sha=None,
            )
        current_sha = sha256_text(body)
    elif kind == KIND_VERBATIM_COPY:
        if not target.exists():
            return DriftEntry(
                relpath=relpath,
                kind=kind,
                state=STATE_NOT_INSTALLED,
                installed_sha=recorded_sha,
                current_sha=None,
            )
        current_sha = sha256_path(target)
    else:
        # An unknown kind is treated as "not installed" so the upgrade
        # path can still re-create it from the canonical install
        # surface; the report carries the kind for diagnostics.
        return DriftEntry(
            relpath=relpath,
            kind=kind,
            state=STATE_NOT_INSTALLED,
            installed_sha=recorded_sha,
            current_sha=None,
        )

    state = STATE_UNCHANGED if current_sha == recorded_sha else STATE_USER_EDITED
    return DriftEntry(
        relpath=relpath,
        kind=kind,
        state=state,
        installed_sha=recorded_sha,
        current_sha=current_sha,
    )


def detect_drift(root: Path, manifest: Manifest | None) -> DriftReport:
    """Classify every managed file's on-disk state vs. ``manifest``.

    A ``None`` manifest is treated as "not installed at all"; every
    target relpath derived from the install surface is reported as
    ``not_installed`` with ``installed_sha=None``. Use
    :func:`mba_foundation.product_boundary.install_content_targets`
    when you need the canonical target list; the manager wires the two
    together.
    """

    from .product_boundary import install_content_targets

    if manifest is None:
        # No manifest — every canonical target is "not installed".
        entries = tuple(
            DriftEntry(
                relpath=relpath,
                # Without a manifest we don't know the kind; flag as the
                # union-kind so the caller can re-derive it from the
                # install surface. This branch is rare in practice
                # because the manager always pairs the manifest with
                # the install-content surface at install time, but
                # keeping the report complete makes downstream code
                # (status) uniform.
                kind=KIND_MANAGED_BLOCK,  # default; install_content_root picks first
                state=STATE_NOT_INSTALLED,
                installed_sha=None,
                current_sha=None,
            )
            for relpath in install_content_targets()
        )
        return DriftReport(root=root, installed=None, entries=entries)

    entries: list[DriftEntry] = []
    for file_entry in manifest.files:
        entries.append(
            _classify_one(
                relpath=file_entry.relpath,
                root=root,
                recorded_sha=file_entry.sha256,
                kind=file_entry.kind,
            )
        )
    return DriftReport(root=root, installed=manifest, entries=tuple(entries))


# ---------------------------------------------------------------------------
# Upgrade planning + apply
# ---------------------------------------------------------------------------


ACTION_INSTALL: str = "install"            # add a fresh target relpath
ACTION_REPLACE: str = "replace"            # overwrite with new upstream content
ACTION_RETIRE: str = "retire"              # remove/forget a target retired upstream
ACTION_CONFLICT: str = "conflict"          # user-edited/unsafe; require decision
ACTION_UP_TO_DATE: str = "up_to_date"      # on-disk matches new upstream already
ACTION_REMOVE_BLOCK: str = "remove_block"  # remove MBA block, keep surrounding text
ACTION_DELETE_FILE: str = "delete_file"    # delete unchanged verbatim-copy target
ACTION_SKIP: str = "skip"                  # absent / already removed


class ManifestConflictError(RuntimeError):
    """Raised by :func:`apply_upgrade` when a user-edited block refuses
    to be overwritten.

    The error message names the offending paths and the on-disk
    discrepancy so the manager can present the conflict to the user
    and require an explicit decision (Charter §11).
    """


class UpgradeBlockedError(RuntimeError):
    """Raised by :func:`apply_upgrade` when an upstream prerequisite
    gate (preflight) refuses to proceed."""


@dataclass(frozen=True)
class UpgradePlanEntry:
    """One row of an :class:`UpgradePlan`."""

    relpath: str
    kind: str
    state: str                           # not_installed / unchanged / user_edited
    action: str                          # install / replace / retire / conflict / up_to_date
    installed_sha: str | None            # recorded sha (None if state == not_installed)
    current_sha: str | None              # on-disk sha (None if state == not_installed)
    upstream_sha: str | None             # None when the target was retired upstream
    reason: str = ""                     # human-readable explanation / refusal text


@dataclass(frozen=True)
class UpgradePlan:
    """The full upgrade plan against a manifest + a new upstream manifest."""

    root: Path
    installed: Manifest | None
    upstream_manifest: Manifest
    entries: tuple[UpgradePlanEntry, ...] = ()

    @property
    def has_conflicts(self) -> bool:
        return any(entry.action == ACTION_CONFLICT for entry in self.entries)

    @property
    def conflict_paths(self) -> tuple[str, ...]:
        return tuple(
            entry.relpath for entry in self.entries if entry.action == ACTION_CONFLICT
        )

    @property
    def is_noop(self) -> bool:
        """True iff every entry is ``up_to_date`` (or ``install`` was
        already applied — i.e. the manifest matches the upstream)."""

        if self.installed is None:
            return False  # not installed at all → not a noop
        return all(entry.action == ACTION_UP_TO_DATE for entry in self.entries)


@dataclass(frozen=True)
class RemovePlanEntry:
    """One row of a safe MBA-removal plan."""

    relpath: str
    kind: str
    state: str
    action: str
    reason: str


@dataclass(frozen=True)
class RemovePlan:
    """The full plan for removing MBA-managed install content."""

    root: Path
    installed: Manifest | None
    entries: tuple[RemovePlanEntry, ...] = ()
    remove_manifest: bool = False
    preserves: tuple[str, ...] = (".beads/", ".mba-work/")

    @property
    def has_conflicts(self) -> bool:
        return any(entry.action == ACTION_CONFLICT for entry in self.entries)

    @property
    def conflict_paths(self) -> tuple[str, ...]:
        return tuple(
            entry.relpath for entry in self.entries if entry.action == ACTION_CONFLICT
        )

    @property
    def is_noop(self) -> bool:
        return self.installed is None or all(
            entry.action == ACTION_SKIP for entry in self.entries
        )


def _upstream_target_sha(upstream: Manifest, relpath: str) -> str | None:
    for entry in upstream.files:
        if entry.relpath == relpath:
            return entry.sha256
    return None


def plan_upgrade(
    root: Path,
    installed: Manifest | None,
    upstream: Manifest,
    drift: DriftReport | None = None,
) -> UpgradePlan:
    """Compute the actions :func:`apply_upgrade` would take.

    The function never mutates state. It is the answer to "what would
    ``mba upgrade --dry-run`` do?". The plan classifies every entry
    into one of:

    * ``install``  — target file is missing or lacks the managed block;
                    installing new content is safe.
    * ``replace``  — target file is in ``unchanged`` state relative to
                    the recorded manifest; the new upstream content can
                    safely overwrite the recorded content.
    * ``retire``   — target was installed previously but is absent from
                    the new upstream install set; remove it only when it
                    is unchanged, or stop tracking it when already absent.
    * ``conflict`` — target file is ``user_edited`` or unsafe; refuse
                    and surface the path without modifying user work.
    * ``up_to_date`` — on-disk already equals the new upstream content
                    (no action needed).

    A ``None`` installed manifest means "MBA not installed at all".
    Managed blocks may still be inserted into existing instruction
    files, but pre-existing verbatim-copy targets are treated as
    user-owned files and become ``conflict`` rows instead of being
    overwritten.
    """

    # If the caller did not pass a drift report, compute one. The
    # upgrade plan reuses drift state, so passing the report avoids a
    # second filesystem walk.
    if drift is None:
        drift = detect_drift(root, installed)

    entries: list[UpgradePlanEntry] = []
    installed_paths = (
        {entry.relpath for entry in installed.files} if installed is not None else set()
    )
    upstream_paths = {entry.relpath for entry in upstream.files}

    # Walk the union of installed + upstream paths so the plan surfaces
    # both "new file upstream that we never installed" and "installed
    # file that no longer ships upstream" (the latter is a future bug
    # to handle; today the two sets are identical).
    all_paths = sorted(installed_paths | upstream_paths)

    for relpath in all_paths:
        drift_entry = next((e for e in drift.entries if e.relpath == relpath), None)
        upstream_sha = _upstream_target_sha(upstream, relpath)
        if upstream_sha is None:
            # A path recorded by the installed manifest but omitted by the
            # new install set has been retired upstream.  Retirement is
            # explicit in dry-run output.  Only an unchanged regular target
            # may be removed; an already-absent target is safe to stop
            # tracking, while edited or unsafe targets fail closed.
            installed_entry = installed.file_for(relpath) if installed is not None else None
            kind = (
                installed_entry.kind
                if installed_entry is not None
                else (drift_entry.kind if drift_entry is not None else KIND_VERBATIM_COPY)
            )
            safety_error = _target_safety_error(root, relpath)
            if drift_entry is None or safety_error is not None:
                entries.append(
                    UpgradePlanEntry(
                        relpath=relpath,
                        kind=kind,
                        state=STATE_USER_EDITED,
                        action=ACTION_CONFLICT,
                        installed_sha=(installed_entry.sha256 if installed_entry else None),
                        current_sha=None,
                        upstream_sha=None,
                        reason=(
                            "retired upstream, but the installed target cannot be "
                            "safely classified; refusing to remove or forget it: "
                            + (safety_error or "missing from drift report")
                        ),
                    )
                )
            elif drift_entry.state == STATE_USER_EDITED:
                entries.append(
                    UpgradePlanEntry(
                        relpath=relpath,
                        kind=kind,
                        state=drift_entry.state,
                        action=ACTION_CONFLICT,
                        installed_sha=drift_entry.installed_sha,
                        current_sha=drift_entry.current_sha,
                        upstream_sha=None,
                        reason=(
                            "target was retired upstream, but on-disk content differs "
                            "from the recorded install; preserving user work and "
                            "refusing retirement without a manual decision"
                        ),
                    )
                )
            else:
                entries.append(
                    UpgradePlanEntry(
                        relpath=relpath,
                        kind=kind,
                        state=drift_entry.state,
                        action=ACTION_RETIRE,
                        installed_sha=drift_entry.installed_sha,
                        current_sha=drift_entry.current_sha,
                        upstream_sha=None,
                        reason=(
                            "target was retired upstream and is already absent; "
                            "safe to stop tracking"
                            if drift_entry.state == STATE_NOT_INSTALLED
                            else "target was retired upstream and still matches the "
                            "recorded install; safe to remove"
                        ),
                    )
                )
            continue

        # Prefer the upstream's kind for the per-row kind. The drift
        # report's kind is only authoritative when its source is the
        # installed manifest; for a fresh install the drift entries
        # lack a clear per-relpath kind, so the upstream is the
        # authoritative source.
        upstream_entry = upstream.file_for(relpath)
        kind = upstream_entry.kind if upstream_entry is not None else (
            drift_entry.kind if drift_entry is not None else KIND_MANAGED_BLOCK
        )

        if drift_entry is None:
            # The installed manifest has this path but drift did not
            # surface it — defensive. Treat as install.
            entries.append(
                UpgradePlanEntry(
                    relpath=relpath,
                    kind=kind,
                    state=STATE_NOT_INSTALLED,
                    action=ACTION_INSTALL,
                    installed_sha=None,
                    current_sha=None,
                    upstream_sha=upstream_sha,
                    reason="not in drift report; treating as fresh install",
                )
            )
            continue

        if drift_entry.state == STATE_NOT_INSTALLED:
            target = root / relpath
            if installed is None and kind == KIND_VERBATIM_COPY and target.exists():
                entries.append(
                    UpgradePlanEntry(
                        relpath=relpath,
                        kind=kind,
                        state=STATE_USER_EDITED,
                        action=ACTION_CONFLICT,
                        installed_sha=None,
                        current_sha=sha256_path(target),
                        upstream_sha=upstream_sha,
                        reason=(
                            "pre-existing target file is not tracked by an MBA "
                            "manifest; refusing to overwrite a user-owned file. "
                            "Manual decision required per docs/mba/charter.md §11."
                        ),
                    )
                )
                continue
            entries.append(
                UpgradePlanEntry(
                    relpath=relpath,
                    kind=kind,
                    state=drift_entry.state,
                    action=ACTION_INSTALL,
                    installed_sha=drift_entry.installed_sha,
                    current_sha=drift_entry.current_sha,
                    upstream_sha=upstream_sha,
                    reason="target is not installed",
                )
            )
        elif drift_entry.state == STATE_USER_EDITED:
            entries.append(
                UpgradePlanEntry(
                    relpath=relpath,
                    kind=kind,
                    state=drift_entry.state,
                    action=ACTION_CONFLICT,
                    installed_sha=drift_entry.installed_sha,
                    current_sha=drift_entry.current_sha,
                    upstream_sha=upstream_sha,
                    reason=(
                        f"on-disk sha {drift_entry.current_sha} differs from "
                        f"recorded sha {drift_entry.installed_sha}; refusing "
                        f"to overwrite a user-edited managed block. "
                        f"Manual decision required per docs/mba/charter.md §11."
                    ),
                )
            )
        else:  # STATE_UNCHANGED
            # If the on-disk content already matches the new upstream,
            # there is nothing to do; record it as up_to_date.
            if drift_entry.current_sha == upstream_sha:
                entries.append(
                    UpgradePlanEntry(
                        relpath=relpath,
                        kind=kind,
                        state=drift_entry.state,
                        action=ACTION_UP_TO_DATE,
                        installed_sha=drift_entry.installed_sha,
                        current_sha=drift_entry.current_sha,
                        upstream_sha=upstream_sha,
                        reason="on-disk already matches the new upstream",
                    )
                )
            else:
                entries.append(
                    UpgradePlanEntry(
                        relpath=relpath,
                        kind=kind,
                        state=drift_entry.state,
                        action=ACTION_REPLACE,
                        installed_sha=drift_entry.installed_sha,
                        current_sha=drift_entry.current_sha,
                        upstream_sha=upstream_sha,
                        reason=(
                            "on-disk sha matches the recorded install; "
                            "upstream content changed; safe to overwrite"
                        ),
                    )
                )

    return UpgradePlan(
        root=root,
        installed=installed,
        upstream_manifest=upstream,
        entries=tuple(entries),
    )


def apply_upgrade(
    root: Path,
    installed: Manifest | None,
    upstream: Manifest,
    *,
    plan: UpgradePlan | None = None,
    dry_run: bool = False,
) -> UpgradePlan:
    """Apply ``upstream`` content into ``root`` per the upgrade plan.

    For every planned action:

    * ``install``  — write the new upstream content into a fresh
      managed file (the file may not exist yet, or the MBA block is
      absent; both are safe).
    * ``replace``  — overwrite the on-disk block content with the new
      upstream MBA RULES block; preserve surrounding user content.
    * ``retire``   — remove an unchanged target retired by upstream (or
      simply omit an already-absent target from the new manifest).
    * ``conflict`` — refuse. No writes happen; the function raises
      :class:`ManifestConflictError` after the plan is returned.
    * ``up_to_date`` — no write.

    ``dry_run=True`` performs every classification and check but never
    touches the filesystem; the caller (typically ``mba upgrade
    --dry-run``) reads the returned plan and reports.

    The function returns the plan in either case so the caller can
    report actions / refusals uniformly.
    """

    if plan is None:
        plan = plan_upgrade(root, installed, upstream)

    if plan.has_conflicts and not dry_run:
        # Refuse BEFORE any write so partial-application is impossible.
        # The plan is still returned so the caller can present the
        # per-path refusal list.
        raise ManifestConflictError(
            "refuse: user-edited or unsafe managed targets detected; manual "
            "decision required per docs/mba/charter.md §11. Conflicting paths: "
            + ", ".join(plan.conflict_paths)
        )

    if dry_run:
        return plan

    # Revalidate every retirement immediately before the first write.  A file
    # edited, replaced by a symlink, or removed after planning must not be
    # deleted on the strength of a stale checksum.
    for entry in plan.entries:
        if entry.action != ACTION_RETIRE:
            continue
        safety_error = _target_safety_error(root, entry.relpath)
        if (
            safety_error is not None
            or entry.kind not in (KIND_MANAGED_BLOCK, KIND_VERBATIM_COPY)
            or entry.state not in (STATE_UNCHANGED, STATE_NOT_INSTALLED)
        ):
            raise ManifestConflictError(
                f"refuse: retired managed target is unsafe to apply: "
                f"{entry.relpath}; preserving on-disk content"
            )
        current = _classify_one(
            entry.relpath,
            root,
            entry.installed_sha,
            entry.kind,
        )
        if current.state != entry.state or current.current_sha != entry.current_sha:
            raise ManifestConflictError(
                f"refuse: retired managed target changed after planning: "
                f"{entry.relpath}; preserving on-disk content"
            )

    # Walk the plan and apply each non-conflict, non-noop row.
    for entry in plan.entries:
        if entry.action == ACTION_UP_TO_DATE:
            continue
        if entry.action == ACTION_CONFLICT:
            # If we reach here in a non-dry-run call it means the
            # caller suppressed the conflict check above; refuse anyway.
            raise ManifestConflictError(
                f"refuse: user-edited managed block at {entry.relpath} "
                f"(on-disk sha {entry.current_sha} != recorded "
                f"{entry.installed_sha})"
            )
        target = root / entry.relpath
        if entry.action == ACTION_RETIRE:
            if entry.state == STATE_NOT_INSTALLED:
                continue
            if entry.kind == KIND_MANAGED_BLOCK:
                _remove_managed_block(target)
            elif entry.kind == KIND_VERBATIM_COPY:
                target.unlink()
                _prune_empty_parents(target.parent, stop=root)
            else:
                raise ManifestConflictError(
                    f"refuse: unknown retired managed-file kind "
                    f"{entry.kind!r} for {entry.relpath}"
                )
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        if entry.kind == KIND_MANAGED_BLOCK:
            _apply_managed_block(target, upstream, entry.relpath)
        elif entry.kind == KIND_VERBATIM_COPY:
            _apply_verbatim_copy(target, upstream, entry.relpath)
        else:
            # Unknown kind — refuse to guess the install shape.
            raise ManifestConflictError(
                f"refuse: unknown managed-file kind {entry.kind!r} for {entry.relpath}"
            )

    # Persist the new manifest with the post-upgrade state.
    write_manifest(root, upstream)
    return plan


def plan_remove(
    root: Path,
    installed: Manifest | None,
    *,
    drift: DriftReport | None = None,
    force: bool = False,
) -> RemovePlan:
    """Compute how MBA-managed install content would be removed.

    Removal is scoped to files recorded in ``.mba/manifest.json``:

    * managed blocks in ``AGENTS.md`` / ``CLAUDE.md`` are removed while
      preserving surrounding project text;
    * unchanged verbatim-copy files are deleted;
    * user-edited managed content is a conflict unless ``force=True``.

    Beads state and ``.mba-work`` are intentionally preserved. They are
    project history / working evidence, not disposable install content.
    """

    if installed is None:
        return RemovePlan(root=root, installed=None, entries=(), remove_manifest=False)
    if drift is None:
        drift = detect_drift(root, installed)

    entries: list[RemovePlanEntry] = []
    for file_entry in installed.files:
        drift_entry = next(
            (entry for entry in drift.entries if entry.relpath == file_entry.relpath),
            None,
        )
        state = drift_entry.state if drift_entry is not None else STATE_NOT_INSTALLED
        if state == STATE_NOT_INSTALLED:
            entries.append(
                RemovePlanEntry(
                    relpath=file_entry.relpath,
                    kind=file_entry.kind,
                    state=state,
                    action=ACTION_SKIP,
                    reason="target already absent or lacks the MBA managed content",
                )
            )
            continue
        if state == STATE_USER_EDITED and not force:
            entries.append(
                RemovePlanEntry(
                    relpath=file_entry.relpath,
                    kind=file_entry.kind,
                    state=state,
                    action=ACTION_CONFLICT,
                    reason=(
                        "on-disk content differs from the recorded MBA install; "
                        "refusing removal without an explicit force decision"
                    ),
                )
            )
            continue
        action = (
            ACTION_REMOVE_BLOCK
            if file_entry.kind == KIND_MANAGED_BLOCK
            else ACTION_DELETE_FILE
        )
        entries.append(
            RemovePlanEntry(
                relpath=file_entry.relpath,
                kind=file_entry.kind,
                state=state,
                action=action,
                reason=(
                    "force requested; removing user-edited MBA-managed content"
                    if state == STATE_USER_EDITED
                    else "recorded MBA install content can be removed"
                ),
            )
        )

    return RemovePlan(
        root=root,
        installed=installed,
        entries=tuple(entries),
        remove_manifest=True,
    )


def apply_remove(
    root: Path,
    installed: Manifest | None,
    *,
    plan: RemovePlan | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> RemovePlan:
    """Apply a safe MBA removal plan.

    The command removes only MBA-managed install content and the
    manifest. It does not delete ``.beads`` or ``.mba-work``; users can
    archive or delete those project-history folders manually if they
    truly want to discard them.
    """

    if plan is None:
        plan = plan_remove(root, installed, force=force)

    if plan.has_conflicts and not dry_run:
        raise ManifestConflictError(
            "refuse: user-edited MBA managed content detected; manual "
            "decision required before removal. Conflicting paths: "
            + ", ".join(plan.conflict_paths)
        )
    if dry_run:
        return plan

    for entry in plan.entries:
        target = root / entry.relpath
        if entry.action == ACTION_SKIP:
            continue
        if entry.action == ACTION_CONFLICT:
            raise ManifestConflictError(
                f"refuse: user-edited MBA managed content at {entry.relpath}"
            )
        if entry.action == ACTION_REMOVE_BLOCK:
            _remove_managed_block(target)
        elif entry.action == ACTION_DELETE_FILE:
            if target.exists():
                target.unlink()
                _prune_empty_parents(target.parent, stop=root)
        else:
            raise ManifestConflictError(
                f"refuse: unknown remove action {entry.action!r} for {entry.relpath}"
            )

    mpath = manifest_path(root)
    if mpath.exists():
        mpath.unlink()
        _prune_empty_parents(mpath.parent, stop=root)
    return plan


def _remove_managed_block(target: Path) -> None:
    """Remove the MBA marker block from ``target`` if present."""

    if not target.exists():
        return
    text = target.read_text(encoding="utf-8")
    m_begin = _BEGIN_LINE_PATTERN.search(text)
    m_end = _END_LINE_PATTERN.search(text)
    if not m_begin or not m_end or m_end.start() <= m_begin.end():
        return
    updated = text[: m_begin.start()] + text[m_end.end() :]
    default_header = (
        "# Project Instructions\n\n"
        "This file is populated and maintained by the MBA Foundation.\n"
    )
    if not updated.strip() or updated.strip() == default_header.strip():
        target.unlink()
        return
    target.write_text(updated, encoding="utf-8")


def _prune_empty_parents(path: Path, *, stop: Path) -> None:
    """Remove empty directories up to but not including ``stop``."""

    stop = stop.resolve()
    current = path.resolve()
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _apply_managed_block(target: Path, upstream: Manifest, relpath: str) -> None:
    """Install / replace the MBA RULES block in ``target``.

    Delegates the actual block-body write to
    :func:`mba_foundation.markers.install_block`, which preserves the
    surrounding file content (e.g. the Beads-generated blocks) and is
    idempotent on the block body itself.

    The block body comes from the upstream manifest's
    ``upstream_bodies`` mapping when present (a freshly built upstream
    carries the body in memory). When the upstream was reloaded from
    disk the mapping is empty and we fall back to the canonical
    :data:`MBA_RULES_BLOCK` — the same body the original install
    produced — so the on-disk state remains consistent with the
    manifest's recorded sha.
    """

    from .markers import install_block

    body = upstream.body_for(relpath)
    if body is not None:
        # Wrap the body in the BEGIN/END marker pair so
        # :func:`install_block` writes byte-identical content to disk
        # as the recorded sha expects.
        block = MBA_RULES_BEGIN_MARKER + "\n" + body + MBA_RULES_END_MARKER + "\n"
        install_block(target, block=block)
    else:
        install_block(target)


def _apply_verbatim_copy(target: Path, upstream: Manifest, relpath: str) -> None:
    """Write the source verbatim-copy text into ``target``.

    The verbatim-copy targets include the MBA skill (legacy special
    case) plus the docs referenced by the installed MBA RULES block
    or skill (``docs/mba/charter.md``, ``docs/beads/capabilities.md``).
    The source text comes from the upstream
    manifest's ``upstream_bodies`` mapping when present, or from the
    installed package via :func:`_read_verbatim_copy_source_text`
    otherwise. The mapping is populated for freshly-built upstream
    manifests (every ``mba upgrade`` call); manifests reloaded from
    disk fall back to reading the live source content.
    """

    body = upstream.body_for(relpath)
    text = body if body is not None else _read_verbatim_copy_source_text(relpath)
    # ``newline="\n"`` keeps the on-disk file byte-identical to the
    # upstream body — see the same rationale in
    # :func:`write_manifest`. Default ``newline=None`` would translate
    # ``\n`` to ``os.linesep`` on Windows, making the on-disk sha
    # diverge from the upstream sha the manifest recorded.
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# Status helpers — small wrappers around detect_drift + plan_upgrade that
# compose the answers the status CLI surface needs.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatusSummary:
    """The JSON-friendly summary that ``mba status`` prints."""

    root: Path
    installed: bool
    installed_version: str | None
    installed_at: str | None
    upstream_version: str
    upgrade_available: bool
    has_drift: bool
    has_conflicts: bool
    user_edited_paths: tuple[str, ...]
    files: tuple[dict[str, object], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "installed": self.installed,
            "installed_version": self.installed_version,
            "installed_at": self.installed_at,
            "upstream_version": self.upstream_version,
            "upgrade_available": self.upgrade_available,
            "has_drift": self.has_drift,
            "has_conflicts": self.has_conflicts,
            "user_edited_paths": list(self.user_edited_paths),
            "files": list(self.files),
        }


def build_status_summary(
    root: Path,
    drift: DriftReport,
    upstream_version: str | None = None,
) -> StatusSummary:
    """Return the JSON-friendly summary ``mba status`` prints.

    The summary combines the drift report with a comparison of the
    installed MBA version (from the manifest) against the current
    upstream MBA version (read from ``mba_version.__version__`` by
    default). ``upgrade_available`` is ``True`` iff the two differ and
    no conflict blocks the upgrade.
    """

    upstream = upstream_version if upstream_version is not None else _current_mba_version()
    installed_version = drift.installed.mba_version if drift.installed is not None else None
    upgrade_available = bool(
        drift.installed is not None
        and installed_version != upstream
        and not drift.has_conflicts
    )

    files = [
        {
            "relpath": entry.relpath,
            "kind": entry.kind,
            "state": entry.state,
            "installed_sha": entry.installed_sha,
            "current_sha": entry.current_sha,
        }
        for entry in drift.entries
    ]
    return StatusSummary(
        root=root,
        installed=drift.is_installed,
        installed_version=installed_version,
        installed_at=drift.installed.installed_at if drift.installed is not None else None,
        upstream_version=upstream,
        upgrade_available=upgrade_available,
        has_drift=drift.has_drift,
        has_conflicts=drift.has_conflicts,
        user_edited_paths=drift.user_edited_paths,
        files=tuple(files),
    )


# ---------------------------------------------------------------------------
# Misc helpers — exposed for the CLI subcommands and tests.
# ---------------------------------------------------------------------------


def plan_entries_to_rows(
    entries: Iterable[UpgradePlanEntry],
) -> list[dict[str, object]]:
    """Project an iterable of plan entries into JSON-friendly rows."""

    return [
        {
            "relpath": e.relpath,
            "kind": e.kind,
            "state": e.state,
            "action": e.action,
            "installed_sha": e.installed_sha,
            "current_sha": e.current_sha,
            "upstream_sha": e.upstream_sha,
            "reason": e.reason,
        }
        for e in entries
    ]


def drift_entries_to_rows(
    entries: Iterable[DriftEntry],
) -> list[dict[str, object]]:
    """Project an iterable of drift entries into JSON-friendly rows."""

    return [
        {
            "relpath": e.relpath,
            "kind": e.kind,
            "state": e.state,
            "installed_sha": e.installed_sha,
            "current_sha": e.current_sha,
        }
        for e in entries
    ]


# Suppress unused-import lints; ``re`` is here because the marker module's
# pattern constants are the regex primitives we deliberately reuse, and a
# future maintainer might want to add a re-export (e.g. for the manager).
_ = re
_ = dataclasses
