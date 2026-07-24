"""`<!-- BEGIN MBA RULES -->` / `<!-- END MBA RULES -->` marker install + verifier.

Acceptance row coverage (Foundation AC #7, AC #8):

* ``AGENTS.md`` and ``CLAUDE.md`` each contain the MBA-owned block
  installed by ``install_block`` from the ``MBA_RULES_BLOCK`` literal
  below. The Beads-generated ``BEGIN BEADS INTEGRATION`` /
  ``BEGIN BEADS CODEX SETUP`` markers are untouched.
* ``grep`` confirms exactly one ``BEGIN MBA RULES`` and exactly one
  ``END MBA RULES`` in each of ``AGENTS.md`` and ``CLAUDE.md``, with
  the canonical ``-->`` tail.

The MBA-owned block content ships in ``MBA_RULES_BLOCK`` below. To
edit it, change the literal here and re-run ``install_block`` — the
charter and capability record references stay exact (one source of
truth in this codebase).

Marker-pattern consolidation (Audit finding F5, turn-2 correction):

* The single source of truth for **MBA** marker pair detection is
  ``_BEGIN_LINE_PATTERN`` / ``_END_LINE_PATTERN`` plus ``count_markers``.
* The single source of truth for **Beads-generated** marker pair
  detection is ``_BEADS_LINE_MARKERS`` plus
  ``assert_beads_markers_untouched``. Substring matching is no longer
  used; ``BEADS_MARKER_PATTERNS`` is removed entirely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .constants import (
    MBA_RULES_BEGIN_MARKER,
    MBA_RULES_END_MARKER,
)

# Canonical MBA RULES block. Kept as a triple-quoted literal;
# ``install_block`` writes it verbatim into ``AGENTS.md`` and
# ``CLAUDE.md``.
MBA_RULES_BLOCK: str = """{begin}
## MBA Foundation Rules

> The Multiple Beaded Agents (MBA) workflow charter is the source of
> truth: ``docs/mba/charter.md``. The capability record is
> ``docs/beads/capabilities.md``. This block carries the subset every
> agent must honour on every MBA-aware project.

### Responsibility taxonomy (Charter §3)

- Exactly three workflow responsibilities: **Orchestrator**, **Doer**,
  **Auditor**. "Developer", "Engineer", "Researcher", "Tester" and the
  like are **organizational roles (hats)** worn by a Doer or Auditor.
- The user's session is the Orchestrator. A session may also serve as
  Doer or Auditor with user agreement, never both.
- Doer and Auditor use separate sessions. A single-AI project uses
  fresh sessions for each responsibility, with opposing hats.
- One transcript changing hats is not a Doer/Auditor pair. If the
  Orchestrator cannot launch or resume the required worker session(s),
  it records the block or asks the user; it does not manufacture
  convergence inside the Orchestrator transcript.
- The Orchestrator stays thin: classify, choose/reuse/create Beads,
  choose stages/roles, write concise pointer-based worker prompts,
  launch workers, verify required artefacts, handle blocks, and close.
  It must not pre-solve, research diffs, paste large findings into
  prompts/notes, or perform Doer/Auditor analysis workers can read
  from Beads/files/URLs.
- Beads is the only workflow task tracker. Do not use OpenCode
  ``todowrite`` or any host-internal todo, task-list, or planning
  tracker; record shared work, follow-up, blockers, and status in
  Beads.
- Write the launch receipt at ``.mba-work/<bead-id>/<session-name>/launch.md``
  in the **same launch step** that captures the worker's PID or session
  id, and **before** any wait, tail, read, or acceptance of the
  worker's output (Bead comment, transcript, ``result.md``,
  ``report.md``). The launch step is one logical block:
  write ``prompt.md``, launch the worker hidden/background,
  capture the PID or session id, immediately write ``launch.md``
  with the launch command shape, PID or session id (or
  ``not_available`` plus a one-line reason), the start time (UTC,
  ISO-8601), the model / AI resource, the prompt path, the log /
  result paths, and a Windows-UX note, and only then wait, tail
  logs, read result/comment, or decide convergence. Writing the
  receipt **after** any worker output exists is a refusal-grade
  error: the receipt no longer proves the session existed before
  the output. The receipt is the only portable proof a separate
  worker session really ran; if it is missing or stale, the
  Orchestrator marks the Bead blocked or asks the user — it must not
  treat a Bead comment as proof the session existed. When the host
  exposes transcript or session identity, the receipt records the
  launched worker identity and the Orchestrator identity. Before
  accepting output, verify that its identity is tied to the launched
  worker and differs from the Orchestrator; PID-only proof is not
  sufficient when stronger host identity exists.
- The worker folder is **exactly**
  ``.mba-work/<bead-id>/<session-name>/``. The ``<bead-id>`` component
  MUST be the actual Bead ID returned by ``bd show <bead-id>`` for the
  work this session is launched for — never a friendly install name,
  the test harness, a ref hash, or a wrapped sibling-clone folder. A
  non-bead-scoped path
  (``.mba-work/<friendly-name>/...`` where ``<friendly-name>`` is not
  the Bead ID) is a refusal-grade error: the Bead cannot be wired
  back to its Beads record, so the launch receipt is invalid and the
  round must be relaunched with the correct path. The
  ``<session-name>`` is a bounded sub-name such as ``doer``,
  ``auditor``, or ``doer-round2``.
- Process stdout/stderr from the worker launch is captured as
  ``run.log`` / ``run.err`` in the same session folder via the
  ``-RedirectStandardOutput`` / ``-RedirectStandardError`` arguments
  on ``Start-Process`` (or the host equivalent). OpenCode may flush
  stdout late, so these are **captured log files**, not a guaranteed
  live stream — do not assume ``run.log`` is empty just because the
  worker has not yet finished. Tailing can provide hints, but the
  final proof is the captured logs plus ``report.md`` / ``comment.md``
  / verdict files, not a live tail alone.
- Hidden, headless, and non-interactive Orchestrators never ask for a
  human decision only in their transcript. If a human decision is
  needed, a valid worker cannot be launched or resumed, or the worker's
  ``report.md`` / ``comment.md`` is still missing after the configured
  bounded wait/resume/relaunch policy, the Orchestrator posts a
  structured Bead comment, sets status ``blocked``, assigns ``Human``,
  and adds the ``human`` label. The wait/resume/relaunch contract is
  fixed: bounded wait for the worker's expected artefacts, attempt a
  resume of the same MBA-owned session, then a single clean relaunch
  with a fresh receipt written **before** any new output is read or
  accepted; if any step still leaves the Bead without a valid worker
  comment, stop and hand the Bead to the human. A Bead must never
  remain silently ``in_progress``.
- **Canonical launch directory creation** (downstream test repo ``path-guard-regression`` retry fix).
  The Orchestrator creates the worker session folder with
  ``New-Item -ItemType Directory -Force -Path <session> | Out-Null``
  directly, **without** any pre-check of the parent directory
  (``Split-Path -Parent`` / ``Test-Path -LiteralPath $parent`` /
  ``if (-not (Test-Path ...)) {{ throw "Missing parent ..." }}``). The
  pre-check shape is the bug-pattern that produced the downstream test repo
  ``Missing parent .mba-work\\<bead>`` failure: ``New-Item -Force``
  already creates missing parent directories, so the pre-check only
  ever short-circuits a clean launch. The session path is the
  single argument; do not split it into separate parent-and-session
  steps.
- **Orchestrator resume after restart / disconnection** (downstream test repo
  ``path-guard-regression`` retry fix). When the active Orchestrator session
  resumes after a restart, disconnection, or other loss of
  in-progress state, it must not assume an open Bead is still being
  worked. The first action for every in-progress Bead is to read the
  Bead's launch receipt at
  ``.mba-work/<bead-id>/<session-name>/launch.md`` and the worker
  artefacts (``report.md`` / ``comment.md`` / verdict files) to
  determine the round's actual state. If the launch receipt exists
  but the worker artefacts are missing, the Orchestrator continues
  the same bounded **wait → resume → relaunch → blocked/Human**
  contract — it does not silently reopen the Bead and does not
  leave it ``in_progress`` indefinitely. A Bead that cannot be
  recovered gets a structured Bead handoff (status ``blocked``,
  assignee ``Human``, label ``human``) in the same way as any
  other stalled worker.
- **Targeted Beads context reads** (downstream test repo ``path-guard-regression`` retry fix).
  The Orchestrator's routine context recovery uses targeted Beads
  reads: ``bd show <bead-id>`` for the active Bead, ``bd ready``
  for the next Bead, ``bd list --status=...`` / ``bd list
  --label=...`` / ``bd list --assignee=...`` for filtered lists,
  ``bd search <text>`` for free-text. Broad ``bd list --all`` is
  not a routine context-recovery move — it dumps every Bead in
  the workspace and does not pay for itself when the Orchestrator
  already knows the active Bead id. Reach for ``bd list --all``
  only when the Orchestrator genuinely has no other pointer to
  the work and the user has explicitly asked for a full roster.
- Worker-internal AI delegation (``none`` or each inner LLM /
  session / subagent / tool delegation) describes only what the
  launched worker did inside its own session after launch; it never
  substitutes for the launch receipt and must never be recorded when
  no separate worker session was actually launched for the Bead.
- **Prompt self-check before launch** (downstream test repo ``prompt-budget-regression`` retry fix). The
  Orchestrator must self-check the worker's ``prompt.md`` *before* the
  ``Start-Process`` step, in the same launch step that writes
  ``launch.md``. A valid assignment is a compact pointer-style contract:
  ``<= 4 KiB`` and ``<= 60`` non-blank lines; **no** unified-diff payload
  (``diff --git``, ``+++ b/``, ``@@``); **no** precomputed source-result
  values such as ``ahead_by: <n>``, ``behind_by: <n>`` or
  ``total_commits: <n>``; at least one retrievable source pointer
  (``bd show``, absolute / relative path, or URL). If the check fails,
  the Orchestrator must rewrite the prompt in the same step. If it
  cannot rewrite it, it must **not** launch — it posts a structured Bead
  comment, sets status ``blocked``, assigns ``Human``, and adds the
  ``human`` label. A launch receipt written against a prompt that
  violates this rule is itself invalid.
- If an Auditor assignment requested ``_verdict.txt``, that file is
  mandatory. Missing/unreadable verdict files, or verdict files
  lacking ``VERDICT``, ``RESOLUTION`` and non-empty ``EVIDENCE``, are
  not ``ACCEPT``. The Orchestrator must not infer acceptance from
  ``report.md``, a Bead comment, or friendly wording.
- A known wrong fact in user-facing deliverable content or metadata
  requires ``FIND`` until corrected. ``ACCEPT`` is allowed only when
  the Auditor gives explicit evidence that the fact is outside the
  accepted deliverable or truly non-load-bearing; merely calling it
  descriptive, minor, or a caveat is not proof.
- The host's built-in subagent surface (OpenCode's internal ``task``
  tool, the ``General Agent`` subagent, or any "delegate to another
  model" facility on the active host) is **not** an MBA worker. Those
  calls share this Orchestrator transcript, never write
  ``.mba-work/<bead-id>/<session-name>/launch.md``, and therefore
  cannot satisfy the Charter §3 requirement that Doer and Auditor use
  separate sessions. Treat any such call as if the work had been done
  in this transcript; if you would not accept the Orchestrator
  transcript as Doer/Auditor evidence, do not accept the subagent
  transcript either. The canonical MBA worker surface on Windows /
  OpenCode is an external ``opencode run --agent mba-worker`` process
  launched via a resolved direct ``opencode.exe`` path and
  ``Start-Process -WindowStyle Hidden -RedirectStandardOutput ...
  -RedirectStandardError ... -PassThru`` (see the launch-receipt rule
  above for the same-launch-step ordering). Do not launch workers with
  the ``mba`` Orchestrator agent or a bare ``opencode`` PowerShell shim.
- Put the full worker assignment in its ``prompt.md``. Launch or resume
  the worker with only a short plain file pointer. Never put Markdown
  backticks, command examples, or rich assignment text in a PowerShell
  inline launch message because PowerShell can interpret and corrupt it.
- On Windows / OpenCode, launch every Doer and Auditor worker hidden
  and background by default so the user's desktop never shows empty
  CMD / conhost windows. Resolve the real ``opencode.exe`` behind any
  npm shim before ``Start-Process``; ``Start-Process -FilePath
  "opencode"`` can pick ``opencode.ps1`` and fail, while
  ``opencode.cmd`` can leave the receipt tied to a wrapper ``cmd.exe``
  PID. Use ``opencode.exe run --agent mba-worker`` with
  ``Start-Process -WindowStyle Hidden -RedirectStandardOutput ... -RedirectStandardError ... -PassThru``
  (PowerShell) or the equivalent non-interactive background launcher
  on the host. Visible empty CMD windows are bad UX and carry no
  useful progress; the empty CMD window is not the progress surface.
  Worker progress lives in the worker's log files, the launch receipt,
  the Bead comment, and any transcript file the worker writes — logs,
  Bead comments, and launch receipts are the progress surface, never
  a desktop window. A visible empty window means the launch is wrong;
  fix the launch step, do not accept it.
- On Windows / PowerShell, never use Bash-only operators or helpers
  such as ``||``, heredocs, ``head``, ``wc``, or ``ls -la`` in Beads
  or worker-launch commands. Use separate commands, ``$LASTEXITCODE``,
  or PowerShell ``if (...) {{ ... }}`` checks.

### Beads-version preflight (Foundation AC #1, AC #2)

- Run ``bd version`` at the start of every MBA run; record the value
  in ``.mba-work/<bead>/orchestrator/working.md``; gate every
  subsequent Bead write on the recorded version.
- The capability record's validated set is ``bd 1.0.4``. Refuse to
  proceed on mismatch; never silently upgrade ``bd`` per
  ``docs/beads/capabilities.md`` Version policy.

### Activation and AI-resource preflight

- MBA is instruction-driven, not a resident daemon. When this repo's
  instructions or MBA skill are loaded and the user asks for work, the
  active AI session becomes the thin Orchestrator first.
- Run ``python -m mba_runtime first-contact --cwd . --apply-setup``. The
  ``mba-runtime`` console script that ``pip install`` registers is an
  optional shortcut that works only when its install location is on
  ``PATH`` (Windows ``pip install --user`` writes it to
  ``%APPDATA%\\Python\\Python3XX\\Scripts``, which is not on ``PATH`` by
  default); the module form needs only a working ``python`` and the
  installed ``mba_runtime`` package. If the private
  ``.mba-work/.ai-resources.json`` record is missing, incomplete or
  inconsistent, use its JSON ``recommended_setup_bead`` guidance to create
  or update the ``MBA setup`` task, assign it to ``Human``, mark it
  ``blocked``, add ``mba``, ``setup`` and ``human`` labels, and post its
  structured Markdown question comment.
- Prefer the deterministic, runtime-assisted path: add the
  ``--apply-setup`` flag so the first-contact invocation itself
  creates or updates the blocked ``MBA setup`` Bead and posts the
  setup question comment with explicit ``--actor Orchestrator``. The
  CLI still returns ``4`` so the Orchestrator stops before creating
  executable Beads or launching workers.
- In that setup branch, stop before creating/driving executable Beads or
  launching workers. If resources are ready, continue with the normal
  separate Doer/Auditor worker flow.
- Upgrade preserves configuration; it does not reinitialize AI-resource
  setup. Re-run ``python -m mba_runtime first-contact --cwd . --apply-setup``
  after upgrade. If the record is already ready, this writes nothing; ask
  only for missing or newly required choices.
- An AI-resource ``id`` is only a local nickname. It must not be used
  as the CLI/provider model argument. A ready resource used by the
  default Doer/Auditor team must include ``launch.tool`` and
  ``launch.model`` (plus ``launch.variant`` when the tool uses one).
  OpenCode resources use provider/model strings such as
  ``minimax-coding-plan/MiniMax-M3`` in ``launch.model``.

### Detect / install / initialise (Foundation AC #3)

- Detect Beads at the project root. On missing/invalid ``.beads/``,
  prompt the user for authority to install/initialise. Never install
  or initialise silently.
- Nested-init guard: refuse ``bd init`` inside any directory whose
  ancestor Beads workspace owns ``sync.remote``.
- Worker safety: never run ``bd init --reinit-local`` or other
  destructive Beads init flags unless the Orchestrator supplied an
  approved disposable root and the worker first checked it has no
  ancestor ``.beads``.

### MBA-owned worker cleanup (example-cleanup)

- Record every launched MBA-owned worker PID and session / transcript
  identity in the launch receipt at
  ``.mba-work/<bead-id>/<session-name>/launch.md``. The cleanup gate
  uses this record as the only portable proof of MBA ownership.
- After every terminal state — successful worker completion,
  convergence, blocked-handoff, timeout, failure, retry, or relaunch
  — clean up any matching MBA-owned worker that is still alive. The
  runtime is the sole writer of the cleanup call; the launch receipt
  is the single source of truth.
- Never kill a session whose identity cannot be pinned to the launch
  receipt. When the host exposes transcript or session identity, the
  cleanup must tie the kill back to the launched worker identity and
  reject any record that matches the Orchestrator's own session or
  transcript. PID-only cleanup is not acceptable when stronger
  identity was available at launch.
- Never kill a non-MBA / user session. The user's own Claude /
  OpenCode / Codex sessions are recorded as ``owner=user`` (or
  omitted from the MBA-owned registry) and the cleanup helper
  refuses to touch them. The runtime never treats a foreign PID as
  if it were MBA-owned.
- Ambiguous ownership — a worker the runtime cannot bind to the
  launch receipt, a worker whose identity matches the Orchestrator,
  or a worker the runtime cannot kill because of a permission
  refusal — becomes a blocked/Human handoff: post the structured
  Bead comment, set ``status``, ``assignee``, and ``label`` per
  Charter §3, and stop until a user decision is recorded. A clean
  relaunch is allowed only with a fresh launch receipt written
  before any new output is read or accepted. The runtime never
  escalates a permission refusal into a force-kill.

### Windows sync guard (Foundation AC #5)

- Refuse ``bd dolt push`` in embedded mode when the resolved
  ``.beads/dolt/`` path length exceeds the validated Windows
  ``MAX_PATH`` threshold. Offer the authorised server-mode
  (``bd init --server``) or non-git remote alternative instead.

### `.mba-work/` mode and AI-resource privacy (Foundation AC #9, AC #10)

- At project setup, surface the mode choice:
  - ``local`` (default, Git-ignored).
  - ``shared`` (Git-tracked).
- Persist the choice in ``.mba-work/.mba-mode``. Default honours the
  existing ``.gitignore`` ``.mba-work/`` rule.
- The local AI-resource record (``.mba-work/.ai-resources.json``) is
  always private and always Git-ignored, in **both** modes. A
  ``.gitignore`` rule checks this at runtime.

### §7 lifecycle (Foundation AC #11)

- Classify every incoming request as **atomic** or **staged** per
  Charter §6 / §7.
- Atomic: produce and audit in the same Task.
- Staged: drive the Selection Bead lifecycle (Understanding →
  classify → select applicable stages → assign roles / sessions /
  dependencies → create only the required Beads → run ready work in
  parallel → complete).

### Comments and detailed working (Charter §10)

- Bead comments: useful structured Markdown summaries, **normally 4-16
  non-blank lines**; prefer lists, checklists or compact tables; state
  result, material changes, verification status, next state, and
  details. The comment is the normal complete human-facing record.
  Never repeat static / implicit Bead fields. A comment that is
  overlong, report-like, pastes the prompt, a transcript, or static
  Bead fields is **not** acceptable — the worker must rewrite and
  repost before the Auditor can ``ACCEPT``. An Auditor must return
  ``FIND`` while a worker comment exceeds the 4-16 non-blank budget,
  pastes the prompt or a transcript, or repeats static Bead fields —
  calling it "descriptive", "minor", or "useful context" is not
  enough; the worker must rewrite the comment and repost before the
  Auditor can accept the round.
- The Orchestrator does **not** post a routine kickoff comment to
  announce that work started. The launch receipt and the worker's own
  role-attributed comment are the normal progress surface; a kickoff
  Bead comment adds no information and burns an audit row.
- Do not paste worker transcripts, prompts, or other bulky evidence
  into the Bead comment. Bulky evidence goes in
  ``.mba-work/<bead-id>/<session-name>/`` only when it is actually
  useful, and the comment says so instead of linking a placeholder
  file.
- ``.mba-work`` is for prompts, machine transcripts, bulky evidence and
  generated artefacts only when useful. If no separate file is needed,
  the comment says so instead of linking a placeholder file.
- Every AI Beads write uses an explicit role actor, e.g.
  ``bd --actor "Orchestrator" ...`` or ``bd --actor "<Role>" ...``.
  Never rely on the OS/Git/default actor; user attribution is only for
  real user input, decisions or actions. Do not use ``bd update
  --claim`` for AI-role work because it claims as the current user.
- File-backed comments use the supported form:
  ``bd --actor "<Role>" comments add <id> -f <file>``. Do not use
  unsupported stdin/flag forms such as ``bd comments add -t -`` or
  ``-f -``. On Windows/PowerShell, avoid Bash-only shortcuts such as
  ``||``, ``head``, ``wc``, ``ls -la`` and heredocs.
- Doer/Auditor may perform the narrow human-handoff write when blocked:
  post the decision comment, add ``human``, set status ``blocked``,
  and assign ``Human``. Hierarchy, dependencies, closure and Dolt stay
  Orchestrator-owned unless explicitly delegated.
- Dependency writes are AI Beads writes too. Use
  ``bd --actor "Orchestrator" dep add ...`` rather than bare
  ``bd dep add ...``.

### User authority (Charter §11)

- Pause for explicit user decision before: source Git commit / push,
  ``bd dolt push/pull``, deployment, external messages, credentials,
  destructive actions, and adoption of reusable workflow changes.
- If Doer/Auditor convergence is reached but a required next action is
  held by this user-authority gate, the Orchestrator must not leave the
  Bead silently ``in_progress`` and must not close it as if the gated
  action happened. It posts a structured decision comment, sets status
  ``blocked``, assigns ``Human``, and adds the ``human`` label. A
  ``ready-for-user-*`` label alone is not enough.

### Generated Beads blocks

- The ``<!-- BEGIN BEADS INTEGRATION -->`` /
  ``<!-- END BEADS INTEGRATION -->`` and ``<!-- BEGIN BEADS CODEX SETUP -->`
  / ``<!-- END BEADS CODEX SETUP -->`` blocks are generated by
  ``bd setup``; this MBA block does not edit them.
{end}
""".format(begin=MBA_RULES_BEGIN_MARKER, end=MBA_RULES_END_MARKER)


_BEGIN_LINE_PATTERN = re.compile(
    r"^" + re.escape(MBA_RULES_BEGIN_MARKER) + r"\s*$", re.MULTILINE
)
_END_LINE_PATTERN = re.compile(
    r"^" + re.escape(MBA_RULES_END_MARKER) + r"\s*$", re.MULTILINE
)

# Line-anchored, full-marker patterns for the Beads-generated blocks.
# Matching is **only** against the start-of-line, `-->` tail form so
# substrings inside the MBA RULES block content (which mentions those
# marker names in prose) cannot create a false-positive hit.
# ``re.fullmatch`` semantics is achieved by anchoring ``^…\s*$`` with
# ``re.MULTILINE``.
_BEADS_INTEGRATION_BEGIN_RE = re.compile(
    r"^<!-- BEGIN BEADS INTEGRATION[^>]*-->\s*$", re.MULTILINE
)
_BEADS_INTEGRATION_END_RE = re.compile(
    r"^<!-- END BEADS INTEGRATION[^>]*-->\s*$", re.MULTILINE
)
_BEADS_CODEX_BEGIN_RE = re.compile(
    r"^<!-- BEGIN BEADS CODEX SETUP[^>]*-->\s*$", re.MULTILINE
)
_BEADS_CODEX_END_RE = re.compile(
    r"^<!-- END BEADS CODEX SETUP[^>]*-->\s*$", re.MULTILINE
)

# Each rule is ``(begin_re, end_re, label)`` so a single iteration
# detects a marker pair on its own line.
_BEADS_LINE_MARKERS: tuple[tuple[re.Pattern[str], re.Pattern[str], str], ...] = (
    (_BEADS_INTEGRATION_BEGIN_RE, _BEADS_INTEGRATION_END_RE, "BEADS INTEGRATION"),
    (_BEADS_CODEX_BEGIN_RE, _BEADS_CODEX_END_RE, "BEADS CODEX SETUP"),
)


def _beads_marker_line_counts(text: str) -> dict[str, tuple[int, int]]:
    """Return line-anchored counts for each Beads marker pair.

    Each entry maps ``label -> (begin_count, end_count)``. A future
    file that has the prose text but loses the marker lines reports
    ``(0, 0)`` for the lost pair; the ``assert_beads_markers_untouched``
    helper surfaces this as an explicit failure.
    """

    out: dict[str, tuple[int, int]] = {}
    for begin_re, end_re, label in _BEADS_LINE_MARKERS:
        out[label] = (
            len(begin_re.findall(text)),
            len(end_re.findall(text)),
        )
    return out


@dataclass(frozen=True)
class MarkerCounts:
    """Counts of MBA markers in a file."""

    begin_count: int
    end_count: int

    @property
    def exactly_one_pair(self) -> bool:
        return self.begin_count == 1 and self.end_count == 1


def count_markers(text: str) -> MarkerCounts:
    """Return the number of BEGIN / END marker lines in ``text``."""

    return MarkerCounts(
        begin_count=len(_BEGIN_LINE_PATTERN.findall(text)),
        end_count=len(_END_LINE_PATTERN.findall(text)),
    )


def install_block(path: Path, *, block: str | None = None) -> bool:
    """Install / refresh the MBA RULES block in ``path``.

    Behaviour:

    * If the file does not exist, write a minimal header followed by
      the block.
    * If a block already exists (BEGIN ... END), replace its contents
      in place. Otherwise append the block on its own.
    * Beads-generated blocks (BEGIN BEADS INTEGRATION / CODEX SETUP) are
      untouched.
    """

    block = block if block is not None else MBA_RULES_BLOCK
    if not path.exists():
        header = (
            f"# Project Instructions\n\n"
            f"This file is populated and maintained by the MBA Foundation.\n\n"
        )
        path.write_text(header + block + "\n", encoding="utf-8")
        return True

    original = path.read_text(encoding="utf-8")

    m_begin = _BEGIN_LINE_PATTERN.search(original)
    m_end = _END_LINE_PATTERN.search(original)
    if m_begin and m_end and m_end.start() > m_begin.end():
        replaced = original[: m_begin.start()] + block.rstrip() + "\n" + original[m_end.end() :]
        path.write_text(replaced, encoding="utf-8")
        return True

    sep = "" if original.endswith("\n") else "\n"
    path.write_text(original + sep + "\n" + block, encoding="utf-8")
    return True


def assert_beads_markers_untouched(path: Path) -> tuple[bool, str]:
    """Confirm that the generated Beads blocks are still present as marker lines.

    Finding F1 (turn-2 correction): the substring form (``if "BEGIN BEADS
    INTEGRATION" in text``) returned false positives when the MBA RULES
    block content mentioned those markers in prose. The runtime check now
    uses line-anchored, full-marker regexes that only fire on the
    ``<!-- BEGIN|END … … -->\n`` boundary lines, never on prose
    references.

    Per-file baseline: the first MBA install captures which Beads marker
    families were present on that file and persists the baseline in
    ``<cwd>/.mba-work/.marker-baseline.json``. Files that never had a
    Beads-CODEX-SETUP block (e.g., ``CLAUDE.md`` when ``bd setup`` ran
    only ``codex`` on ``AGENTS.md``) are *not* flagged when the marker
    is still absent later; files that had it and lost it are flagged.
    """

    cwd = _find_repo_cwd(path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""

    baseline = _read_baseline(cwd)
    file_key = _file_key(path, cwd)
    recorded = baseline.get(file_key)
    if recorded is None:
        # First time we see this file — capture the baseline now.
        recorded = _capture_marker_labels(text)
        baseline[file_key] = recorded
        _write_baseline(cwd, baseline)

    counts = _beads_marker_line_counts(text)
    missing: list[str] = []
    for label in recorded:
        begin_count, end_count = counts.get(label, (0, 0))
        if begin_count == 0 or end_count == 0:
            missing.append(
                f"{label} (begin={begin_count}, end={end_count})"
            )
    if missing:
        return False, (
            f"Beads-generated marker line(s) absent from {path}: "
            + ", ".join(missing)
            + ". The MBA RULES block content is prose, not a real marker "
            "line; this helper only fires on line-anchored full-marker lines."
        )
    return True, ""


def _find_repo_cwd(path: Path) -> Path:
    """Find the active repository cwd that owns this file.

    The upward walk stops at the strongest project boundary it can
    find so that an ancestor ``.mba-work/`` *outside* the active
    repository cannot capture the per-file Beads-marker baseline
    (see ``example-008.1``). The pass order is deliberate:

    1. ``.git/`` — the VCS root defines the active repository. The
       first ancestor that owns ``.git/`` is the project boundary;
       the walk stops there. This closes the original confinement
       hole: an ancestor pollution directory above the VCS root is
       never visited.
    2. ``.beads/`` — a Beads workspace without VCS. Same stop-on-first-
       hit rule.
    3. Fallback — return the file's parent (or ``Path.cwd()`` if the
       file does not exist). The walk deliberately does **not**
       continue looking for a stray ``.mba-work/`` at any ancestor;
       that is the very pattern that produced ``C:\\.mba-work`` in
       the ``example-007.1`` boundary incident.

    The marker baseline therefore always lives inside the active
    repository, never at an ancestor outside it.
    """

    here = path.resolve().parent if path.exists() else Path.cwd()
    for candidate in [here, *here.parents]:
        if (candidate / ".git").exists():
            return candidate
    for candidate in [here, *here.parents]:
        if (candidate / ".beads").exists():
            return candidate
    return here


# ---------------------------------------------------------------------------
# Per-file Beads-marker baseline (Audit finding F1 follow-up)
# ---------------------------------------------------------------------------


BASELINE_FILE: str = ".marker-baseline.json"
_BEADS_LABELS: tuple[str, ...] = ("BEADS INTEGRATION", "BEADS CODEX SETUP")


def _beads_marker_re_by_label() -> dict[str, tuple[re.Pattern[str], re.Pattern[str]]]:
    return {label: (begin_re, end_re) for begin_re, end_re, label in _BEADS_LINE_MARKERS}


def _file_key(path: Path, cwd: Path) -> str:
    """Stable key for the baseline JSON — POSIX-friendly relative path."""

    abs_path = path.resolve()
    try:
        relative = abs_path.relative_to(cwd)
        return relative.as_posix()
    except ValueError:
        return abs_path.as_posix()


def _capture_marker_labels(text: str) -> list[str]:
    """Return the list of Beads-marker families present in ``text``."""

    counts = _beads_marker_line_counts(text)
    return [label for label, (b, e) in counts.items() if b > 0 and e > 0]


def _baseline_path(cwd: Path) -> Path:
    return cwd / ".mba-work" / BASELINE_FILE


def _read_baseline(cwd: Path) -> dict[str, list[str]]:
    path = _baseline_path(cwd)
    if not path.exists():
        return {}
    import json as _json

    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except _json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, value in data.items():
        if isinstance(value, list):
            out[key] = [v for v in value if isinstance(v, str)]
    return out


def _write_baseline(cwd: Path, baseline: dict[str, list[str]]) -> None:
    path = _baseline_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    import json as _json

    path.write_text(
        _json.dumps(baseline, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def reset_baseline(cwd: Path | None = None) -> None:
    """Drop the baseline JSON so the next ``assert_beads_markers_untouched``
    call re-captures from disk. Used by the Auditor to re-baseline after
    a known ``bd setup`` change.
    """

    cwd = cwd or Path.cwd()
    path = _baseline_path(cwd)
    if path.exists():
        path.unlink()
