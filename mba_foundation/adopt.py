"""``mba adopt`` — fail-closed adoption of byte-identical tracked content.

A consumer repository can already track the complete MBA install
surface (the MBA RULES blocks in ``AGENTS.md`` / ``CLAUDE.md``, the
copied docs, the skill) — for example a fresh clone of a repo whose
maintainer committed the ``mba init`` output — while the **private**
install manifest (``.mba/manifest.json``) is absent, because ``.mba/``
is local state that is never published. On such a clone ``mba init``
correctly refuses: pre-existing verbatim-copy targets are user-owned
files it must not overwrite (see
:func:`mba_foundation.manifest.plan_upgrade`).

``mba adopt`` closes that gap without weakening the fail-closed rule:

* Every managed target that the repo already carries must be
  **raw-byte identical** to the packaged MBA content — the
  managed-block body between the MBA RULES markers, and the full
  content of every verbatim-copy target. Identity is decided over
  ``read_bytes()`` with **no newline normalization**: the packaged
  content is LF, so a CRLF checkout of a managed file refuses (with a
  reason that names the newline difference explicitly). This is
  deliberately *stricter* than the drift machinery, whose
  universal-newline text digests would call CRLF bytes "unchanged";
  adoption is the one moment MBA claims pre-existing files as its
  own, so it must not claim bytes it would not itself have written.
  Because everything MBA writes is LF (:func:`write_manifest`,
  :func:`_apply_verbatim_copy`), an adopted repo satisfies the
  normalized drift check too and is *by construction* drift-free for
  ``mba status``.
* Any mismatch — a missing tracked target, an edited block, a stray
  second marker pair, an unsafe path, undecodable bytes — refuses the
  whole adoption with **no writes at all** (all-or-nothing).
* On success the command creates **only** the private manifest and,
  when explicitly selected, the private OpenCode launch files. It
  never touches the tracked content it verified.
* :func:`apply_adopt` never trusts a previously computed plan: it
  re-verifies every target from disk immediately before the first
  write, bound to the same root, upstream, and launch-file choice the
  plan was computed with, and refuses on **any** divergence — so a
  file edited (or created) between planning and applying can neither
  be silently recorded nor overwritten. Selected launch files are
  written with OS-exclusive creation (``open(..., "x")``) as a final
  belt against a file appearing after revalidation — and when a later
  create refuses, the launch files this invocation already created
  are rolled back (guarded: only while the path still holds the exact
  regular file this invocation created — the identity token captured
  from the exclusive-create descriptor itself, re-verified without
  following symlinks up to the moment of the unlink — with raw bytes
  still equal to the packaged bytes; any replacement, symlink,
  non-file, identity change, or unreadable state is preserved and
  named in the escalated refusal), so a caught refusal leaves the
  tree exactly as the call found it.

## The OpenCode launch files

``opencode.json`` and the two ``.opencode/agents/*.md`` agents are
machine-local launch configuration in most consumer policies, so a
publishing repo typically does **not** track them. The
``opencode`` choice controls what adoption does when they are absent:

* ``omit`` (default) — leave them absent and leave them out of the
  manifest. The repo simply has no OpenCode launch surface.
* ``create`` — write them from the packaged bytes (they are private,
  Git-ignored launch files) and record them in the manifest.

A launch file that is already present is always verified like every
other target: byte-identical → adopted into the manifest; different →
the whole adoption refuses. The choice only governs **absent** launch
files.

An ``omit`` choice is **durable**: the adopted manifest records the
omitted relpaths in its ``omitted`` field, and
:func:`mba_foundation.manifest.plan_upgrade` preserves the omission on
every later ``mba upgrade`` (the target is skipped while absent, and a
file the user later creates at an omitted path is treated as
user-owned — a conflict, never an overwrite). ``mba remove`` deletes
the manifest and the recorded omission with it.

## Idempotence

Adoption of an already-adopted repo is a no-op: when
``.mba/manifest.json`` exists and the drift report is clean the plan
returns ``already_installed=True`` and :func:`apply_adopt` writes
nothing. An existing manifest **with** drift refuses — adoption is not
a repair path; ``mba status`` / ``mba upgrade`` own that state.

## Write ordering and rollback

On a successful apply the selected launch files are written first and
the manifest last (atomically, via
:func:`mba_foundation.manifest.write_manifest`).

When a launch-file creation refuses partway (a file appeared at a
later target after revalidation), the files this invocation already
created are rolled back before the refusal propagates — guarded by
**ownership proof**, not byte equality alone: the identity of the
exact regular file (device, inode, size, nanosecond mtime, object
type) is captured with ``os.fstat`` on the exclusive-create
descriptor itself — flushed, before close, with no path lookup a
concurrent replacement could poison — and rollback deletes a path
only when a non-following check shows a regular file, the descriptor
actually opened for hashing carries that same recorded identity
(``os.fstat``), the raw bytes still equal the packaged digest, and a
final non-following ``os.lstat`` immediately before the unlink still
matches the token. Directories the rollback empties are pruned. Any
replacement — including a same-content file or symlink someone put at
the path — plus any identity change, non-file, or unreadable state is
preserved and named with its reason in the escalated refusal instead
of being deleted. A **caught** refusal therefore leaves the tree
exactly as the call found it (plus whatever a concurrent writer put
there).

The uncatchable case — the process dies between the writes — cannot
roll anything back; the repo is then still "not installed" and any
created files are byte-identical packaged content, so re-running
``mba adopt`` adopts them: the command converges instead of wedging.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .manifest import (
    KIND_MANAGED_BLOCK,
    KIND_VERBATIM_COPY,
    Manifest,
    ManifestConflictError,
    detect_drift,
    extract_block_body,
    read_manifest,
    sha256_text,
    write_manifest,
)

# Intra-package reuse of the manifest module's target-safety,
# verbatim-source, and directory-pruning helpers — the same
# consolidation rule as the marker regexes (one implementation, no
# silent desync).
from .manifest import (  # noqa: F401
    _prune_empty_parents,
    _read_verbatim_copy_source_text,
    _target_safety_error,
)
from .markers import count_markers


__all__ = [
    "ACTION_ADOPT",
    "ACTION_CREATE",
    "ACTION_OMIT",
    "ACTION_MISMATCH",
    "OPENCODE_CHOICE_OMIT",
    "OPENCODE_CHOICE_CREATE",
    "OPENCODE_CHOICES",
    "OPENCODE_LAUNCH_TARGETS",
    "AdoptPlanEntry",
    "AdoptPlan",
    "plan_adopt",
    "apply_adopt",
    "adopt_plan_entries_to_rows",
]


#: Per-target adoption verdicts.
ACTION_ADOPT: str = "adopt"          # present + byte-identical → record in manifest
ACTION_CREATE: str = "create"        # absent launch file selected for creation
ACTION_OMIT: str = "omit"            # absent launch file left out of the manifest
ACTION_MISMATCH: str = "mismatch"    # refusal row — blocks the whole adoption

#: The explicit choice for absent private OpenCode launch files.
OPENCODE_CHOICE_OMIT: str = "omit"
OPENCODE_CHOICE_CREATE: str = "create"
OPENCODE_CHOICES: tuple[str, ...] = (OPENCODE_CHOICE_OMIT, OPENCODE_CHOICE_CREATE)

#: The private OpenCode launch files the ``opencode`` choice governs.
#: Kept in lock-step with
#: :func:`mba_foundation.product_boundary.install_content_targets`
#: (a subset of its verbatim-copy targets; pinned by test).
OPENCODE_LAUNCH_TARGETS: tuple[str, ...] = (
    "opencode.json",
    ".opencode/agents/mba.md",
    ".opencode/agents/mba-worker.md",
)


@dataclass(frozen=True)
class AdoptPlanEntry:
    """One per-target verdict of an :class:`AdoptPlan`."""

    relpath: str
    kind: str
    action: str
    current_sha: str | None      # on-disk digest (None when absent/unreadable)
    packaged_sha: str | None     # packaged upstream digest
    reason: str = ""


@dataclass(frozen=True)
class AdoptPlan:
    """The full all-or-nothing adoption verdict for one root."""

    root: Path
    upstream: Manifest
    entries: tuple[AdoptPlanEntry, ...] = ()
    already_installed: bool = False
    blocking_reason: str = ""    # non-empty ⇒ refuse before classification
    #: The launch-file choice this plan was computed with. Recorded so
    #: :func:`apply_adopt` can bind its pre-write revalidation to the
    #: exact same choice and refuse a conflicting request.
    opencode: str = OPENCODE_CHOICE_OMIT

    @property
    def has_mismatches(self) -> bool:
        return any(entry.action == ACTION_MISMATCH for entry in self.entries)

    @property
    def mismatch_paths(self) -> tuple[str, ...]:
        return tuple(
            entry.relpath for entry in self.entries if entry.action == ACTION_MISMATCH
        )

    @property
    def adopted_paths(self) -> tuple[str, ...]:
        return tuple(
            entry.relpath for entry in self.entries if entry.action == ACTION_ADOPT
        )

    @property
    def created_paths(self) -> tuple[str, ...]:
        return tuple(
            entry.relpath for entry in self.entries if entry.action == ACTION_CREATE
        )

    @property
    def omitted_paths(self) -> tuple[str, ...]:
        return tuple(
            entry.relpath for entry in self.entries if entry.action == ACTION_OMIT
        )

    @property
    def ok(self) -> bool:
        """True iff the adoption may be applied (or is already done)."""

        if self.blocking_reason:
            return False
        return not self.has_mismatches


def _sha256_raw(path: Path) -> str:
    """SHA256 over the file's **raw bytes** — no newline translation.

    The deliberate contrast with :func:`mba_foundation.manifest.
    sha256_path` (universal-newline text digest) is the whole point:
    adoption claims pre-existing files as MBA-managed, so it accepts
    only the exact bytes MBA itself would have written (LF).
    """

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _newline_hint(raw_text: str | None, packaged_text: str | None) -> str:
    """Return a diagnostic suffix when only newlines differ.

    ``raw_text`` is the un-normalized on-disk text; ``packaged_text``
    the packaged LF source. When normalizing ``\\r\\n``/``\\r`` to
    ``\\n`` makes them equal, the mismatch is (almost certainly) a
    CRLF checkout — say so, because the fix (checkout with LF, e.g.
    via ``.gitattributes``) is otherwise hard to guess from a digest.
    """

    if raw_text is None or packaged_text is None:
        return ""
    normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized == packaged_text:
        return (
            " (the content matches after newline normalization only — "
            "likely a CRLF checkout of LF-managed content; adopt "
            "requires raw byte identity)"
        )
    return ""


def _classify_managed_block(
    root: Path, relpath: str, packaged_sha: str, packaged_body: str | None
) -> AdoptPlanEntry:
    """Verify a pre-existing MBA RULES block is raw-byte identical.

    Adoption requires the block to already be present — a missing file
    or a missing marker pair is a mismatch, not an install opportunity
    (that is ``mba init``'s job on a repo that consents to writes).
    A stray second marker pair is malformed content and also refuses:
    :func:`extract_block_body` would silently read only the first pair,
    so accepting it would adopt a file the verifier never fully read.

    The file is decoded from raw bytes **without** newline translation
    (undecodable bytes refuse), so a CRLF block body hashes to a
    different digest than the packaged LF body and refuses with a
    newline-specific reason.
    """

    target = root / relpath
    if not target.is_file():
        return AdoptPlanEntry(
            relpath=relpath,
            kind=KIND_MANAGED_BLOCK,
            action=ACTION_MISMATCH,
            current_sha=None,
            packaged_sha=packaged_sha,
            reason=(
                "adopt requires a pre-existing MBA RULES block: file does "
                f"not exist: {target}"
            ),
        )
    try:
        raw_text = target.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        return AdoptPlanEntry(
            relpath=relpath,
            kind=KIND_MANAGED_BLOCK,
            action=ACTION_MISMATCH,
            current_sha=None,
            packaged_sha=packaged_sha,
            reason=f"file is not valid UTF-8: {exc}",
        )
    extracted = extract_block_body(raw_text)
    if extracted is None:
        return AdoptPlanEntry(
            relpath=relpath,
            kind=KIND_MANAGED_BLOCK,
            action=ACTION_MISMATCH,
            current_sha=None,
            packaged_sha=packaged_sha,
            reason=(
                "adopt requires a pre-existing MBA RULES block: no "
                "BEGIN/END MBA RULES pair found"
            ),
        )
    counts = count_markers(raw_text)
    if not counts.exactly_one_pair:
        return AdoptPlanEntry(
            relpath=relpath,
            kind=KIND_MANAGED_BLOCK,
            action=ACTION_MISMATCH,
            current_sha=None,
            packaged_sha=packaged_sha,
            reason=(
                f"expected exactly one BEGIN/END MBA RULES pair, found "
                f"{counts.begin_count} BEGIN / {counts.end_count} END"
            ),
        )
    _, _, body = extracted
    current_sha = sha256_text(body)
    if current_sha != packaged_sha:
        return AdoptPlanEntry(
            relpath=relpath,
            kind=KIND_MANAGED_BLOCK,
            action=ACTION_MISMATCH,
            current_sha=current_sha,
            packaged_sha=packaged_sha,
            reason=(
                "on-disk MBA RULES block body differs from the packaged "
                "content; adopt accepts raw byte-identical content only"
                + _newline_hint(body, packaged_body)
            ),
        )
    return AdoptPlanEntry(
        relpath=relpath,
        kind=KIND_MANAGED_BLOCK,
        action=ACTION_ADOPT,
        current_sha=current_sha,
        packaged_sha=packaged_sha,
        reason="pre-existing block is raw byte-identical to the packaged content",
    )


def _classify_verbatim(
    root: Path,
    relpath: str,
    packaged_sha: str,
    packaged_text: str | None,
    *,
    opencode: str,
) -> AdoptPlanEntry:
    """Verify a verbatim-copy target's **raw bytes**, honouring the
    launch-file choice."""

    target = root / relpath
    is_launch_file = relpath in OPENCODE_LAUNCH_TARGETS
    if not target.exists():
        if is_launch_file:
            if opencode == OPENCODE_CHOICE_CREATE:
                return AdoptPlanEntry(
                    relpath=relpath,
                    kind=KIND_VERBATIM_COPY,
                    action=ACTION_CREATE,
                    current_sha=None,
                    packaged_sha=packaged_sha,
                    reason="absent private launch file selected for creation",
                )
            return AdoptPlanEntry(
                relpath=relpath,
                kind=KIND_VERBATIM_COPY,
                action=ACTION_OMIT,
                current_sha=None,
                packaged_sha=packaged_sha,
                reason="absent private launch file omitted by choice",
            )
        return AdoptPlanEntry(
            relpath=relpath,
            kind=KIND_VERBATIM_COPY,
            action=ACTION_MISMATCH,
            current_sha=None,
            packaged_sha=packaged_sha,
            reason=(
                "tracked managed target is absent; adopt requires the "
                "complete pre-existing install surface"
            ),
        )
    current_sha = _sha256_raw(target)
    if current_sha != packaged_sha:
        try:
            raw_text: str | None = target.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            raw_text = None
        return AdoptPlanEntry(
            relpath=relpath,
            kind=KIND_VERBATIM_COPY,
            action=ACTION_MISMATCH,
            current_sha=current_sha,
            packaged_sha=packaged_sha,
            reason=(
                "on-disk content differs from the packaged content; adopt "
                "accepts raw byte-identical content only"
                + _newline_hint(raw_text, packaged_text)
            ),
        )
    return AdoptPlanEntry(
        relpath=relpath,
        kind=KIND_VERBATIM_COPY,
        action=ACTION_ADOPT,
        current_sha=current_sha,
        packaged_sha=packaged_sha,
        reason="pre-existing file is raw byte-identical to the packaged content",
    )


def plan_adopt(
    root: Path,
    upstream: Manifest,
    *,
    opencode: str = OPENCODE_CHOICE_OMIT,
) -> AdoptPlan:
    """Classify every upstream install target for adoption. No writes.

    ``upstream`` is a freshly built packaged manifest (the same object
    ``mba init`` builds), so the target list and packaged digests come
    from the single canonical install surface.
    """

    if opencode not in OPENCODE_CHOICES:
        raise ValueError(
            f"invalid opencode choice {opencode!r}; expected one of "
            f"{', '.join(OPENCODE_CHOICES)}"
        )

    try:
        installed = read_manifest(root)
    except ValueError as exc:
        return AdoptPlan(
            root=root,
            upstream=upstream,
            blocking_reason=(
                f"existing .mba/manifest.json is unreadable ({exc}); refusing "
                f"to adopt over a corrupted install record"
            ),
            opencode=opencode,
        )
    if installed is not None:
        drift = detect_drift(root, installed)
        if not drift.has_drift:
            return AdoptPlan(
                root=root,
                upstream=upstream,
                already_installed=True,
                opencode=opencode,
            )
        return AdoptPlan(
            root=root,
            upstream=upstream,
            blocking_reason=(
                "an .mba/manifest.json already exists and the managed state "
                "has drift; adopt is not a repair path — inspect `mba status` "
                "and resolve via `mba upgrade` or a manual decision per "
                "docs/mba/charter.md §11"
            ),
            opencode=opencode,
        )

    entries: list[AdoptPlanEntry] = []
    for file_entry in upstream.files:
        safety_error = _target_safety_error(root, file_entry.relpath)
        if safety_error is not None:
            entries.append(
                AdoptPlanEntry(
                    relpath=file_entry.relpath,
                    kind=file_entry.kind,
                    action=ACTION_MISMATCH,
                    current_sha=None,
                    packaged_sha=file_entry.sha256,
                    reason=f"unsafe target: {safety_error}",
                )
            )
        elif file_entry.kind == KIND_MANAGED_BLOCK:
            entries.append(
                _classify_managed_block(
                    root,
                    file_entry.relpath,
                    file_entry.sha256,
                    upstream.body_for(file_entry.relpath),
                )
            )
        elif file_entry.kind == KIND_VERBATIM_COPY:
            entries.append(
                _classify_verbatim(
                    root,
                    file_entry.relpath,
                    file_entry.sha256,
                    upstream.body_for(file_entry.relpath),
                    opencode=opencode,
                )
            )
        else:
            entries.append(
                AdoptPlanEntry(
                    relpath=file_entry.relpath,
                    kind=file_entry.kind,
                    action=ACTION_MISMATCH,
                    current_sha=None,
                    packaged_sha=file_entry.sha256,
                    reason=f"unknown managed-file kind {file_entry.kind!r}",
                )
            )
    return AdoptPlan(
        root=root, upstream=upstream, entries=tuple(entries), opencode=opencode
    )


def _create_new_launch_file(
    target: Path, upstream: Manifest, relpath: str
) -> tuple[int, int, int, int, int]:
    """Write a selected launch file with OS-exclusive creation and
    return the created file's identity token.

    ``open(..., "x")`` refuses atomically when the file already exists
    — the final belt against a user file appearing at the target path
    after the pre-write revalidation. The identity token is captured
    with ``os.fstat`` on the **exclusive-create descriptor itself**,
    after the content is flushed and before the descriptor closes: it
    therefore names the exact filesystem object that won the exclusive
    creation, and no later path lookup — which a concurrent
    replacement could poison — is ever involved in recording
    ownership. The body resolution mirrors
    :func:`mba_foundation.manifest._apply_verbatim_copy` (in-memory
    upstream body first, packaged source fallback); the ``newline``
    pin keeps the created bytes LF on every platform, matching the
    recorded digest.
    """

    body = upstream.body_for(relpath)
    text = body if body is not None else _read_verbatim_copy_source_text(relpath)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            return _identity_token(os.fstat(handle.fileno()))
    except FileExistsError:
        raise ManifestConflictError(
            f"refuse: a file appeared at {relpath} after revalidation; "
            f"preserving the on-disk content"
        ) from None


def _identity_token(st: os.stat_result) -> tuple[int, int, int, int, int]:
    """Filesystem identity of a created file.

    ``(st_dev, st_ino, st_size, st_mtime_ns, S_IFMT(st_mode))``,
    captured with ``os.fstat`` on the **exclusive-create descriptor**
    (content flushed, descriptor still open — no path lookup a
    concurrent replacement could poison), re-checked with ``os.fstat``
    on the descriptor actually opened at rollback, and checked one
    final time with non-following ``os.lstat`` immediately before the
    unlink. ``dev`` + ``ino`` name the exact filesystem object —
    Python fills them on Windows too (volume serial + file index); on
    the rare filesystem reporting ``st_ino == 0`` the remaining fields
    still bind size, nanosecond mtime, and object type — and
    ``S_IFMT`` pins "regular file".
    """

    return (
        st.st_dev,
        st.st_ino,
        st.st_size,
        st.st_mtime_ns,
        stat.S_IFMT(st.st_mode),
    )


def _rollback_created(
    root: Path,
    upstream: Manifest,
    created: list[tuple[str, tuple[int, int, int, int, int] | None]],
) -> tuple[str, ...]:
    """Undo this invocation's launch-file creations after a refusal.

    Guarded, newest-first, and **ownership-proving**: a created path is
    deleted only when ALL of the following still hold —

    1. ``os.lstat`` (non-following) shows a regular file: a symlink or
       any other object now at the path is someone else's and is
       preserved, never followed;
    2. the object actually opened for hashing (verified via
       ``os.fstat`` on the open descriptor, immune to a swap between
       the ``lstat`` and the ``open``) carries the exact identity
       token captured from the exclusive-create descriptor when this
       invocation created the file — same device, inode, size,
       mtime (ns), and type;
    3. its raw bytes equal the packaged digest this invocation wrote;
    4. a final non-following ``os.lstat`` immediately before the
       unlink still shows a regular file with that same token — the
       unlink acts on the path, so the descriptor-to-unlink window is
       closed too.

    Anything else — a replacement object with identical bytes, a
    symlink, a non-file, an identity change, an unreadable state, or a
    missing recorded token — is **preserved** (deleting it would
    destroy someone's work on the strength of a stale assumption) and
    reported back with its reason so the caller can escalate the
    refusal honestly. A path that no longer exists needs no rollback.
    Directories left empty by a rollback are pruned (never past
    ``root``), so a caught refusal leaves the tree exactly as the call
    found it.

    Returns ``"relpath (reason)"`` strings for everything preserved.
    """

    preserved: list[str] = []
    for relpath, token in reversed(created):
        target = root / relpath
        try:
            current = os.lstat(target)
        except FileNotFoundError:
            continue  # already gone — nothing left to roll back
        except OSError as exc:
            preserved.append(f"{relpath} (unreadable state: {exc})")
            continue
        if token is None:
            preserved.append(f"{relpath} (creation identity was not recorded)")
            continue
        if not stat.S_ISREG(current.st_mode):
            preserved.append(
                f"{relpath} (replaced by a symlink or non-regular file)"
            )
            continue
        entry = upstream.file_for(relpath)
        packaged_sha = entry.sha256 if entry is not None else None
        if packaged_sha is None:
            preserved.append(f"{relpath} (no packaged digest to verify against)")
            continue
        try:
            with target.open("rb") as handle:
                if _identity_token(os.fstat(handle.fileno())) != token:
                    preserved.append(
                        f"{relpath} (replaced or modified since creation — "
                        f"filesystem identity differs)"
                    )
                    continue
                data = handle.read()
        except OSError as exc:
            preserved.append(f"{relpath} (unreadable state: {exc})")
            continue
        if hashlib.sha256(data).hexdigest() != packaged_sha:
            preserved.append(
                f"{relpath} (content no longer equals the packaged bytes)"
            )
            continue
        # Final non-following identity/type check immediately before
        # the unlink: the descriptor verification above proved the
        # opened object, but the unlink acts on the *path* — an object
        # swapped in between the two must be preserved, never deleted.
        try:
            final = os.lstat(target)
        except FileNotFoundError:
            continue  # vanished after verification — nothing to unlink
        except OSError as exc:
            preserved.append(f"{relpath} (unreadable state at unlink: {exc})")
            continue
        if not stat.S_ISREG(final.st_mode) or _identity_token(final) != token:
            preserved.append(
                f"{relpath} (replaced between verification and unlink — "
                f"preserving the current object)"
            )
            continue
        target.unlink()
        _prune_empty_parents(target.parent, stop=root)
    return tuple(reversed(preserved))


def apply_adopt(
    root: Path,
    upstream: Manifest,
    *,
    plan: AdoptPlan | None = None,
    opencode: str | None = None,
    dry_run: bool = False,
) -> AdoptPlan:
    """Apply an all-or-nothing adoption with a pre-write revalidation.

    Refuses — with :class:`ManifestConflictError`, before any write —
    when the (re)computed plan carries a blocking reason or any
    mismatch row.

    A caller-supplied ``plan`` is never trusted as a licence to write:
    it must bind to the same ``root`` and ``upstream`` it was computed
    for (and to the same launch-file choice, when ``opencode`` is also
    given), and the whole classification is recomputed from disk
    immediately before the first write. **Any** divergence between the
    supplied plan and the fresh state — a tracked file edited after
    planning, a launch file created after planning, anything — refuses
    with zero writes; re-run the adoption against the current state.

    ``opencode=None`` means "the plan's recorded choice" when a plan
    is supplied, and the ``omit`` default otherwise. ``dry_run=True``
    returns the plan without writing regardless of verdict. On success
    the only writes are the ``create``-selected launch files (via
    OS-exclusive creation) and the manifest — which records the
    adopted/created files **and** the durable ``omitted`` relpaths;
    ``adopt`` rows are never rewritten.

    If a later launch-file creation refuses, the files this invocation
    already created are rolled back before the error propagates
    (guarded — see :func:`_rollback_created`), so the refusal is
    all-or-nothing for every caught path.
    """

    if plan is not None:
        if plan.root != root:
            raise ManifestConflictError(
                f"refuse: plan was computed for root {plan.root}, not {root}"
            )
        if plan.upstream != upstream:
            raise ManifestConflictError(
                "refuse: plan was computed against a different upstream "
                "manifest; re-run adopt"
            )
        if opencode is not None and opencode != plan.opencode:
            raise ManifestConflictError(
                f"refuse: plan was computed with opencode={plan.opencode!r} "
                f"but apply requested opencode={opencode!r}"
            )
        choice = plan.opencode
    else:
        choice = opencode if opencode is not None else OPENCODE_CHOICE_OMIT

    if dry_run:
        return plan if plan is not None else plan_adopt(root, upstream, opencode=choice)

    # Transactional recheck: reclassify every target from disk NOW,
    # bound to the same root/upstream/choice. The fresh plan is the
    # only authority for the writes below.
    fresh = plan_adopt(root, upstream, opencode=choice)
    if plan is not None and (
        fresh.entries != plan.entries
        or fresh.already_installed != plan.already_installed
        or fresh.blocking_reason != plan.blocking_reason
    ):
        raise ManifestConflictError(
            "refuse: the on-disk state changed after planning; no writes "
            "performed — re-run adopt against the current state"
        )

    if fresh.already_installed:
        return fresh
    if fresh.blocking_reason:
        raise ManifestConflictError(f"refuse: {fresh.blocking_reason}")
    if fresh.has_mismatches:
        raise ManifestConflictError(
            "refuse: managed targets are not raw byte-identical to the "
            "packaged MBA content; no writes performed. Mismatched paths: "
            + ", ".join(fresh.mismatch_paths)
        )

    created: list[tuple[str, tuple[int, int, int, int, int] | None]] = []
    try:
        for entry in fresh.entries:
            if entry.action == ACTION_CREATE:
                # The identity token comes back from the exclusive-
                # create descriptor itself (see
                # :func:`_create_new_launch_file`) — recorded directly,
                # with no path lookup a concurrent replacement could
                # poison. A wrapper that swallows the return value
                # simply makes the file un-rollbackable: a ``None``
                # token is preserved and named, never guessed at.
                token = _create_new_launch_file(
                    root / entry.relpath, upstream, entry.relpath
                )
                created.append((entry.relpath, token))
    except ManifestConflictError as exc:
        # All-or-nothing for a caught refusal: undo the launch files
        # this invocation already created (guarded — see
        # :func:`_rollback_created`), so the tree is exactly as the
        # call found it, plus whatever the concurrent writer put there.
        preserved = _rollback_created(root, upstream, created)
        if preserved:
            raise ManifestConflictError(
                str(exc)
                + " Additionally, paths created by this invocation were "
                "concurrently modified or replaced and have been preserved "
                "instead of rolled back — resolve them manually: "
                + "; ".join(preserved)
            ) from exc
        raise

    recorded = {*fresh.adopted_paths, *fresh.created_paths}
    adopted_manifest = dataclasses.replace(
        upstream,
        files=tuple(
            file_entry
            for file_entry in upstream.files
            if file_entry.relpath in recorded
        ),
        omitted=fresh.omitted_paths,
    )
    write_manifest(root, adopted_manifest)
    return fresh


def adopt_plan_entries_to_rows(plan: AdoptPlan) -> list[dict[str, object]]:
    """Project an :class:`AdoptPlan` into JSON-friendly rows."""

    return [
        {
            "relpath": entry.relpath,
            "kind": entry.kind,
            "action": entry.action,
            "current_sha": entry.current_sha,
            "packaged_sha": entry.packaged_sha,
            "reason": entry.reason,
        }
        for entry in plan.entries
    ]
