# How MBA works — technical flow

> Module-level view of the current implementation. Requirements are in
> [`charter.md`](charter.md); build status is in
> [`implementation-status.md`](implementation-status.md).

## Package map

| Package | Responsibility | Key modules |
|---|---|---|
| `mba_foundation` | Detect / validate / initialise Beads; guards | `detect`, `preflight`, `sync_guard`, `markers`, `workspace`, `orchestrator` (atomic-vs-staged classification), `constants` |
| `mba_primitives` | Bead read/write + records layout + assignment contract | `bead_read`, `bead_write`, `records_layout`, `assignment_contract`, `constants` |
| `mba_runtime` | The `drive_bead` orchestration shell + supporting services | `lifecycle`, `pattern_router`, `convergence`, `comments`, `external_dispatch`, `user_authority`, `graph`, `ai_resources`, `session_recovery`, `cli` |

Every layer records `bd version` and gates writes on the validated set
(`1.0.4`) before touching Beads (Foundation AC #1/#2).

## Target-repo upgrade and managed-file retirement

`mba_foundation.manifest` compares the installed manifest with both the
filesystem and the newly built upstream manifest. The planner walks the union
of old and new paths, so a path present only in the old manifest produces an
explicit `retire` row instead of disappearing from the dry-run.

| Retired target state | Upgrade result |
|---|---|
| Matches its recorded checksum | Remove the verbatim file, or remove only the MBA marker block while preserving surrounding project text; omit the row from the new manifest. |
| Already absent | Perform no deletion; omit the stale row from the new manifest. |
| Edited, non-regular, symlinked, non-canonical, or outside the target root | `conflict`; refuse before any write and retain both user content and the installed manifest. |

Retirement rows are checksum-revalidated immediately before the first write.
The upstream manifest replaces the old manifest only after every planned
operation succeeds. This makes a successful retry a no-op and leaves
`.mba-work/.ai-resources.json`, `.mba-work/.mba-mode`, and setup choices
outside the upgrade surface.

## First-contact setup branch

`first-contact` is read-only by default. If the private AI-resource
record is missing, incomplete or inconsistent, the JSON output includes
`recommended_setup_bead` with the `MBA setup` task title, `task` type,
`blocked` status, `Human` assignee, `mba`/`setup`/`human` labels, and a
structured Markdown comment containing the deterministic questions. It
distinguishes `allowed_now` actions (`create_or_update_setup_bead`,
`post_setup_questions`) from `blocked_until_ready` actions
(`create_or_drive_executable_beads`, `launch_workers`) so the handoff
wording is unambiguous.

The deterministic path is `first-contact --apply-setup`: the runtime
itself creates or updates the `MBA setup` Bead and posts the
deterministic setup question comment with explicit `--actor
Orchestrator`. The CLI still returns exit code `4` so the Orchestrator
stops before executable Beads or worker launches; the manual
hand-written path remains available for the few cases that need a
custom comment body.
The Orchestrator performs that setup handoff and stops; it does not enter
`drive_bead` until a later first-contact check reports ready.

AI-resource readiness includes executable launch identity:

| Field | Required meaning |
|---|---|
| `id` | Project nickname used by team configs. Never pass this as a CLI model. |
| `launch.tool` | Tool/surface used to launch the session, e.g. `opencode`. |
| `launch.model` | Exact provider/model argument, e.g. `minimax-coding-plan/MiniMax-M3`. |
| `launch.variant` | Effort/variant argument, when supported, e.g. `max`. |

For OpenCode, `launch.model` must be a provider/model string. A record
that only says `id: minimax-m3-max` is incomplete and first-contact blocks
on `MBA setup`.


```text
bd ready ─► drive_bead(config) ─► bd close
```

| # | Step | Module |
|---|---|---|
| 0 | First-contact activation/resource preflight when an AI first starts work in the repo; missing setup becomes a blocked `MBA setup` handoff to `Human` and stops executable work | `ai_resources`, `cli first-contact` |
| 1 | `bd version` gate + read the Bead (`bd show --json`) | `lifecycle`, `bd_client` |
| 2 | Load the AI-resource record; pick a team; route the pattern (a/b/c/d) | `ai_resources`, `pattern_router` |
| 3 | Create the §10 layout for every session | `mba_primitives.records_layout` |
| 4 | Fill each worker's `prompt.md` from the assignment contract | `mba_primitives.assignment_contract` |
| 5 | Run the §8 adversarial loop (Doer round → Auditor round) | `convergence` |
| 6 | Post one useful structured role-attributed comment per session | `comments` |
| 7 | Verify the dependency graph (`bd dep list` + `bd dep cycles`) | `graph` |
| 8 | Gate any external action on a recorded §11 decision | `user_authority` |
| 9 | Close the Bead (no routine Orchestrator comment) | `lifecycle` |

**No routine Orchestrator comment.** `DriveConfig.post_orchestrator_comment`
defaults to `False`; `drive_bead` never auto-emits an Orchestrator
comment. The CLI matches this — an Orchestrator comment is **opt-in**
via `--orchestrator-comment`.

## Session dispatch (`mba_runtime.external_dispatch`)

| Property | Behaviour |
|---|---|
| Surface | Spawns a real external worker subprocess per Doer/Auditor session, pointed at its `prompt.md`. |
| Confinement | **Pre-launch default-deny** gate; launches only with `trusted_confinement` or a recorded **human-origin** §11 `external_dispatch_unconfinement` decision. No racy pre/post file-diff. |
| Worker safety | Records path, prompt path and allowed write root stay under the project; destructive `bd init` flags require an approved disposable root checked for no ancestor `.beads`. |
| Comment discipline | The adapter is the **single writer** of the worker's role comment (`posts_own_comment=True`); the runtime skips its own posting for that session. |
| Verdict channel | The Auditor writes `_verdict.txt` (`ACCEPT`/`FIND`/`BLOCKED` + `RESOLUTION` + non-empty `EVIDENCE`) which feeds the §8 loop. If the assignment requested `_verdict.txt`, missing evidence is not `ACCEPT`. |

Manual copy-and-use sessions follow the same rule: a Doer/Auditor pair
means distinct worker sessions or processes. OpenCode's built-in `task` /
`General Agent` subagents share the Orchestrator transcript and are not MBA
workers; use separate external hidden/background `opencode run --agent
mba-worker` processes with launch receipts. The local CLI supports `--dir`,
`--agent`, `--model`, `--variant`, `--auto`, `--title`, and `--format`.
If a host cannot launch or resume the
required worker session, the Orchestrator records `BLOCKED` or asks the user;
it does not produce both worker outputs from one Orchestrator transcript.

The Orchestrator stays thin: it classifies, prepares/reuses Beads, writes
pointer-based prompts, launches workers, checks required artefacts, and closes
or blocks. It does not pre-research, pre-solve, write long task notes, or paste
bulky context that workers can read from Beads, files, or URLs.

### Worker launch — one logical block, receipt first

Downstream smoke tests exposed a real gap: the launch receipt
(`.mba-work/<bead-id>/<session-name>/launch.md`) can be accidentally
written **after** the worker's `result.md`, Bead comment, or transcript
tail. Then the receipt proves nothing — the output may have come from the
Orchestrator's own transcript. Empty CMD / conhost windows on Windows add
bad UX without useful progress. The fix is a **safe copy-paste launch
pattern** the Orchestrator uses every time, on every host:

The worker folder is **exactly** `.mba-work/<bead-id>/<session-name>/`.
The `<bead-id>` component MUST be the actual Bead ID returned by
`bd show <bead-id>` for the work this session is launched for — never a
friendly install name, the test harness, a ref hash, or a wrapped
sibling-clone folder. A non-bead-scoped path
(`.mba-work/<friendly-name>/...` where `<friendly-name>` is not the Bead
ID) is a refusal-grade error: the Bead cannot be wired back to its
Beads record, so the launch receipt is invalid and the round must be
relaunched with the correct path. The `<session-name>` is a bounded
sub-name such as `doer`, `auditor`, or `doer-round2`.

```text
write prompt.md
   │
   ▼
self-check prompt.md against the compact-pointer budget
   │
   ▼
launch worker hidden/background, capture PID/session id (same step)
   │
   ▼
write launch.md IMMEDIATELY (same launch step)
   │
   ▼
ONLY NOW: wait, tail logs, read result.md, read Bead comment,
decide convergence
```

The **prompt self-check** between writing `prompt.md` and launching keeps
worker prompts compact and pointer-based. A valid assignment is `≤ 4 KiB`
and `≤ 60` non-blank lines, has no unified-diff payload, has no precomputed
source-result values such as `ahead_by: <n>` / `behind_by: <n>` /
`total_commits: <n>`, and lists at least one retrievable source pointer.
A failed check forces a rewrite in the same step; an unfixable prompt blocks
the Bead to `Human`.

Canonical PowerShell shape on Windows / OpenCode:

```powershell
$session = ".mba-work\<bead-id>\<session-name>"
New-Item -ItemType Directory -Force -Path $session | Out-Null

# 1. Write the full assignment to the worker's prompt.md (file path,
#    not inline). Never the rich assignment inline in the launch cmd.
```

The session folder is created with `New-Item -ItemType Directory -Force`
**directly on the session path**, with no pre-check of the parent directory.
The pre-check shape (`Split-Path -Parent $session` followed by
`if (-not (Test-Path -LiteralPath $parent)) { throw "Missing parent ..." }`)
is a known bug pattern: `New-Item -Force` already creates missing parent
directories, so the pre-check can short-circuit a clean launch. The session
path is the single argument; do not split it into separate parent-and-session
steps.

# 2. Resolve the real executable, not the PowerShell shim or .cmd wrapper.
$opencodeExe = $null
$cmd = Get-Command opencode.cmd -ErrorAction SilentlyContinue | Select-Object -First 1
if ($cmd) {
  $candidate = Join-Path (Split-Path $cmd.Source -Parent) "node_modules\opencode-ai\bin\opencode.exe"
  if (Test-Path $candidate) { $opencodeExe = $candidate }
}
if (-not $opencodeExe) { throw "Could not resolve opencode.exe" }

# 3. Launch hidden/background with the worker agent, capture PID, redirect logs.
$args = @(
  "run", "--dir", $PWD.Path,
  "--agent", "mba-worker",
  "--model", "<launch.model>", "--variant", "<launch.variant>", "--auto",
  "--title", "<bead-id> <Role>", "--format", "json",
  "Read $session\prompt.md and follow it."
)
$proc = Start-Process -FilePath $opencodeExe -ArgumentList $args `
  -WindowStyle Hidden `
  -RedirectStandardOutput "$session\run.log" `
  -RedirectStandardError  "$session\run.err" `
  -PassThru

# 4. IMMEDIATELY write the receipt — before any wait/tail/read.
$receipt = @"
# Worker launch receipt

| Field | Value |
|---|---|
| Bead | <bead-id> |
| Session name | <session-name> |
| Launch command shape | opencode.exe run --dir "$PWD" --agent mba-worker ... "Read $session\prompt.md and follow it." |
| PID or session id | $($proc.Id) |
| Start time (UTC) | $([DateTime]::UtcNow.ToString("o")) |
| Model / AI resource | <resource-id> / <launch.model> / <launch.variant> |
| Prompt path | $session\prompt.md |
| Log / result paths | $session\run.log; $session\run.err; expected $session\report.md; expected $session\comment.md |
| Windows UX | hidden/background launch via Start-Process -WindowStyle Hidden; worker progress is in logs/Bead comments, not desktop CMD windows |
"@
Set-Content -Path "$session\launch.md" -Value $receipt

# 5. Only now may the Orchestrator wait/tail/read/decide.
```

Failure modes the pattern fixes:

| Failure | Symptom | Fix in this pattern |
|---|---|---|
| Receipt written **after** `result.md` / Bead comment | Round looks converged with no proof the worker actually ran. | Step 3 writes the receipt before step 4; one logical block. |
| Empty CMD / conhost window stays on desktop | Bad UX, no progress visible | Step 2 uses `-WindowStyle Hidden` and `-RedirectStandardOutput` / `-RedirectStandardError`; worker progress is in the files, never in the window. |
| Receipt missing PID or session id | Auditor cannot prove the session existed | Step 3 captures `$proc.Id` immediately; if the host does not expose one, write `not_available` plus a one-line reason. |
| Host exposes transcript/session identity | PID does not prove which transcript produced output | Record the launched worker and Orchestrator identities in the receipt; reject output that matches the Orchestrator or cannot be tied to the launched worker. |
| Worker report or comment never appears | Bead remains silently `in_progress` | Use bounded wait → resume the same MBA-owned session → one clean relaunch with a fresh receipt. If output is still missing, post a structured Bead handoff, set `blocked`, assign `Human`, and add `human`. |
| Rich assignment inline in the launch message | PowerShell interprets backticks / variables; rich assignment can be parsed wrong | Step 2 sends only the short plain pointer `"Read $session\prompt.md and follow it."`; the full assignment is in `prompt.md`. |

On non-Windows hosts the equivalent rule applies: hidden / background
/ no-tty launcher (e.g. `nohup ... >log 2>&1 &` or `setsid ... </dev/null
>log 2>&1 &`); redirect logs; write the receipt in the same launch
step.

Process stdout/stderr from the worker launch is captured as `run.log` /
`run.err` in the same session folder via the `-RedirectStandardOutput` /
`-RedirectStandardError` arguments on `Start-Process` (or the host
equivalent). OpenCode may flush stdout late, so these are **captured
log files**, not a guaranteed live stream — do not assume `run.log` is
empty just because the worker has not yet finished. Tailing can provide
hints, but the final proof is the captured logs plus `report.md` /
`comment.md` / verdict files, not a live tail alone.

Hidden, headless and non-interactive Orchestrators use the Bead as the
human question surface. They never ask only inside the hidden transcript.
Any human decision or invalid worker provenance becomes a structured Bead
comment plus `blocked`, assignee `Human`, and label `human`. For launch/resume
failure or missing `report.md` / `comment.md`, first use the bounded recovery
sequence: wait, resume the same MBA-owned session, then one clean relaunch
with a fresh receipt. If output is still missing, perform that handoff rather
than leaving the Bead silently `in_progress`.

**Orchestrator resume after restart / disconnection.** When the active
Orchestrator session resumes after a restart, disconnection, or other loss
of in-progress state, it must not assume an open Bead is still being worked.
The first action for every in-progress Bead is to read the Bead's launch receipt at
`.mba-work/<bead-id>/<session-name>/launch.md` and the worker artefacts
(`report.md` / `comment.md` / verdict files) to determine the round's actual
state. If the launch receipt exists but the worker artefacts are missing,
the Orchestrator continues the same bounded **wait → resume → relaunch →
blocked/Human** contract — it does not silently reopen the Bead and does
not leave it `in_progress` indefinitely. A Bead that cannot be recovered
gets a structured Bead handoff (status `blocked`, assignee `Human`, label
`human`) just like any other stalled worker.

**Targeted Beads context reads.** Routine Orchestrator context recovery uses targeted Beads reads: `bd show <bead-id>`
for the active Bead, `bd ready` for the next Bead, `bd list --status=...` /
`bd list --label=...` / `bd list --assignee=...` for filtered lists,
`bd search <text>` for free-text. Broad `bd list --all` is not a routine
context-recovery move — it dumps every Bead in the workspace and does not
pay for itself when the Orchestrator already knows the active Bead id.
Reach for `bd list --all` only when the Orchestrator genuinely has no other
pointer to the work and the user has explicitly asked for a full roster.

### Worker stream / log capture (`mba_runtime.stream_capture`)

The dispatch adapter is the **single** capture surface for MBA-launched
workers. The `mba_runtime.stream_capture` module is the **single** read
surface:

| Layer | File | Role |
|---|---|---|
| Capture (write) | `mba_runtime/external_dispatch.py` (`ExternalProcessSessionRunner`, default `capture_mode=direct_files`) | Redirects the worker's stdout and stderr to per-session `run.log` / `run.err` files; pre-creates the files; closes the sinks on every terminal state. |
| Capture record | `mba_runtime/stream_capture.py` (`StreamCapture`) | Runtime-owned metadata that points at the captured files and the declared protocol (`ndjson`, `line_text`, `opaque_bytes`). Surfaced on `SessionOutcome.capture`. |
| Follower (read) | `mba_runtime/stream_capture.py` (`follow_stream`, `follow_capture`, `summarize_log`, `summarize_event`, `read_events`) | Read-only consumer. Buffers a partial final line; never sits between the worker and its durable sink. |
| CLI | `mba_runtime/cli.py` (`stream` subcommand) | `python -m mba_runtime stream <bead-id> <session-name> [--follow] [--raw] [--max-bytes N] [--max-events N] [--max-field-chars N] [--max-tool-payload-chars N] [--poll-interval-ms N]`. Validates the Bead-scoped path against `.mba-work/<bead-id>/`; refuses friendly names and ref hashes. |

Key contract:

1. **Direct file redirection is canonical.** `Popen(stdout=run.log, stderr=run.err, text=False)` writes the worker's raw output to the file. The worker can flush at any pace; the file is durable.
2. **The follower is read-only and process-decoupled.** A viewer disconnect, a follow-on audit, or the bounded UI never interrupts capture. A restarted viewer replays complete records and, with `--follow`, polls for new bytes.
3. **Capture is harness-neutral.** The default protocol is `line_text`. Operators that know the worker is OpenCode set `capture_protocol=ndjson` (or pass `--format json` to `opencode run`) so the follower can render typed `step_start` / `step_finish` / `tool_use` / `text` / `reasoning` / `error` summaries instead of raw lines.
4. **OpenCode output is captured at completion granularity, not token granularity.** `--format json` is documented as raw JSON events, but the v1.18.3 implementation emits at step / tool / completed-part granularity. `--thinking` is display-only and provider-dependent; it is opt-in for the operator and never a default. The follower renders bounded summaries (`--max-field-chars` and `--max-tool-payload-chars`); raw JSON is available only with `--raw` (diagnostic, never a Bead-comment transport).
5. **Bead-scoped path guard.** `validate_bead_scoped_path` refuses `.mba-work/<friendly-name>/...` mistakes and symlink escapes; the CLI surfaces a `BeadScopedPathError` (exit 99).
6. **Cleanup is unchanged.** The MBA-owned worker cleanup gate (next section) is untouched. `stream_capture` never spawns, signals, or kills; it only reads.

The `mba stream` subcommand is the documented surface; an
auditor or follow-on tool that needs bounded worker progress should
use it instead of opening the capture file directly. The launch
receipt at `.mba-work/<bead-id>/<session-name>/launch.md` continues
to record the durable capture paths so a viewer can locate the
files without re-launching the worker.

### MBA-owned worker cleanup (`mba_runtime.external_dispatch`)

The dispatch adapter is the **single** capture-and-cleanup surface for
MBA-owned worker PIDs and session ids. Cleanup is ownership-gated,
fail-closed, and bound to the launch receipt.

| Step | What happens |
|---|---|
| Launch | The dispatch spawn produces an `MBAOwnedWorker` record carrying `pid`, `session_id`, `transcript_id`, `worker_identity`, and `ownership_proof`. The runner rebuilds this record immediately after `Popen` so the PID is captured at the same step. |
| Receipt | The launch receipt at `.mba-work/<bead-id>/<session-name>/launch.md` is the only portable proof of MBA ownership. It is written in the same launch step that captures the worker PID / session id — before any `result.md`, Bead comment, or transcript tail is read. |
| Terminal cleanup | `cleanup_mba_owned_worker` is invoked after every terminal state: successful completion, convergence, blocked handoff, timeout, failure, retry, or relaunch. Any matching MBA-owned worker that is still alive is killed; the helper is the only writer of this call. |
| Ownership gate | The helper refuses to act on a record whose ownership cannot be tied back to the launch. Non-MBA / user sessions — the user's own Claude / OpenCode / Codex sessions — are returned as `CLEANUP_NOT_MBA_OWNED` and never killed. |
| Identity binding | When the host exposes transcript or session identity, the kill must tie back to the launched worker identity. A match to the Orchestrator's own session or transcript returns `CLEANUP_REFUSED`. PID-only cleanup is not acceptable when stronger identity was available at launch. |
| Ambiguous ownership | Identity-binding refusal calls `blocked_fn(...)` and returns `CLEANUP_REFUSED` with `blocked_for_human=True`. The default `record_blocked` is `raise_human_needed` — a structured Bead handoff to `Human`. A clean relaunch is allowed only with a fresh receipt written before any new output is read or accepted. |
| Permission refusal | A kill that raises `PermissionError` is recorded as `CLEANUP_PROTECTED` plus a blocked handoff. The runtime never escalates a permission refusal into a force-kill. |

| `CleanupOutcome.action` | Meaning |
|---|---|
| `CLEANED` | Matching MBA-owned worker was alive and was killed. |
| `NOTHING_TO_CLEAN` | Process already gone; no-op. |
| `NOT_MBA_OWNED` | Ownership could not be proven; no kill, blocked handoff whose text names the user's own Claude / OpenCode / Codex sessions. |
| `REFUSED` | Identity binding failed (Orchestrator-match or unbound record); no kill, blocked handoff. |
| `PROTECTED` | Permission refusal from the kill primitive; no escalation, blocked handoff. |

Recovery decisions (`mba_runtime.session_recovery`) feed into the same
ownership model: only `owner == "mba"` sessions are ever eligible for
`RESUME` / `RELAUNCH` / `FALLBACK`; the User's own sessions are reported
as `protected_session_ids` and never touched.

## Convergence loop (`mba_runtime.convergence`)

```text
for round in range(max_rounds):
    Doer round  → produces or updates the claim/result
    Auditor round → returns ACCEPT / FIND / BLOCKED
    ACCEPT (with non-empty reasons AND non-empty evidence)  → converged
    BLOCKED → stop, record non-convergence (Bead stays open/blocked)
    FIND    → next round
```

Convergence is **evidence-required**, not reason-synthesised:

* The Auditor's `result.md` carries a structured
  `VERDICT:` / `RESOLUTION:` / `EVIDENCE:` block. The runtime parses
  it via `mba_runtime.convergence.parse_auditor_protocol` and
  refuses to backfill evidence; a side-channel `ACCEPT` with no
  `EVIDENCE` block is downgraded to `FIND` so the next round can
  supply the missing evidence.
* An `ACCEPT` with empty `reasons` or empty `evidence` is refused
  at the constructor (`Verdict.accept`) and at the loop boundary
  (`iterate_until_converged`).
* After a `FIND` (`round_index > 0`), an `ACCEPT` requires **either**
  a **changed** artefact (the runtime hashes the artefacts each
  round and compares) **or** an explicit `RESOLUTION:
  no-change-proof` from the Auditor. An `ACCEPT` with an unchanged
  artefact and no `no-change-proof` is downgraded to `FIND` and the
  loop continues.
* If an `ACCEPT` conflicts with supplied known facts, files, logs, Bead
  state or prior proof, the runtime downgrades it to `FIND` before
  convergence. A known wrong fact in user-facing deliverable content or
  metadata therefore requires `FIND` until corrected, unless the Auditor
  gives explicit evidence that it is outside the accepted deliverable or
  truly non-load-bearing. Describing it as minor, descriptive, or a caveat
  is not evidence.
* When multiple Auditor sessions ran for the round (pattern (c) or
  any multi-Auditor roster), the round verdict is combined with
  priority `BLOCKED > FIND > ACCEPT`: any `FIND` or `BLOCKED` from
  any Auditor prevents convergence that round, even when another
  Auditor returned `ACCEPT`. The combined `ACCEPT` carries every
  Auditor's evidence and the strongest `no-change-proof` resolution.
* An exhausted loop records non-convergence rather than declaring
  success. Unrecognised verdict strings raise loudly.

### User-authority handoff after convergence

If Doer/Auditor convergence succeeds but the next required step needs user
approval (Git commit/push, `bd dolt push/pull`, deployment, destructive work,
external messages, credentials/spend, or reusable workflow adoption), the
Orchestrator performs a visible handoff instead of leaving the Bead
`in_progress`.

| Field | Required state |
|---|---|
| Status | `blocked` |
| Assignee | `Human` |
| Label | `human` |
| Comment | Structured decision needed + exact blocked action |

A `ready-for-user-*` label may be added as extra signal, but it does not
replace `blocked` + `Human`.

**Honest limit.** The runtime can require the evidence *structure*
(non-empty `EVIDENCE` block, `RESOLUTION: no-change-proof` after a
`FIND`); it cannot prove the Auditor truly *reasoned*. MBA is
**evidence-required and reasoning-trusted**, not runtime-proven reasoning.

## Recovery pipeline (`mba_runtime.session_recovery`)

A pure, deterministic library (time is injected) that decides what to do
when a dispatch is needed. Order — **resume-first, then relaunch, then
ordered fallback, then bounded retry**:

```text
want (bead, responsibility, role, stage, ai_id, effort)
        │
        ▼
1. exact MBA-owned resumable session?  ── yes ─► RESUME
        │ no / ambiguous
        ▼
   ambiguous MBA near-match / multi-match? ── yes ─► ASK_USER
        │ no
        ▼
2. wanted AI available?  ── yes ─► RELAUNCH (same AI, same effort)
        │ no
        ▼
3. next suitable AI available? ── yes ─► FALLBACK (record substitution)
        │ no (all unavailable)
        ▼
4. bounded retry / probe ─► terminal state (Bead stays open)
```

| Decision / state | Meaning |
|---|---|
| `RESUME` | Exactly one MBA-owned session matches all six identity fields and is resumable. |
| `RELAUNCH` | No resumable match; start a fresh MBA session on the same AI + effort. |
| `FALLBACK` | Wanted AI unavailable; move to the next *suitable* AI, recorded as a `Substitution` (no silent downgrade). |
| `ASK_USER` | Multiple exact matches, or a same-Bead near-match — never resume/kill on ambiguity. |
| `RETRY_SCHEDULED` | All unavailable, reset times known → retry at `max(reset)+backoff`, ≤ `MAX_SCHEDULED_RETRIES`. |
| `PROBE_SCHEDULED` | All unavailable, reset unknown → probe ≤ `MAX_PROBES_WHEN_RESET_UNKNOWN`, each ≤ `PROBE_INTERVAL_MAX_SECONDS`. |
| `BLOCKED_PROVIDER_UNAVAILABLE` | Retry budget exhausted; Bead stays open for a User decision. |
| `PAUSED_NO_RESET_TIME` | Probe budget exhausted; Bead stays open for a User decision. |

**User-session protection.** Only `owner == "mba"` sessions are ever
eligible. Foreign sessions (the User's own Claude/OpenCode work) are
reported as `protected_session_ids` and never probed, resumed,
interrupted, or killed. Ordered fallback comes from
`suitable_resources(record, team, responsibility)`. The **runtime-only**
`AuditTrail` appends one JSONL row per decision.

## User-authority gate (`mba_runtime.user_authority`)

`gate(action, decision_fn)` refuses without a recorded human-origin
decision. Catalogue (`constants.USER_AUTHORITY_ACTIONS`): source Git
commit/push, `bd_dolt_push`/`bd_dolt_pull`, deployment, external message,
credentials/spending, destructive change, `external_dispatch_unconfinement`,
reusable workflow change. `actor: cli` and default actors never qualify.

**User authority overrides generated Git-push instructions.** The
Beads-generated `Session Completion` block instructs a workflow to
"push to remote" (see `AGENTS.md` / `CLAUDE.md`). That generated
instruction does **not** override the §11 gate; the MBA Rules block
in `AGENTS.md` / `CLAUDE.md` records the override so a runtime that
honours MBA cannot accidentally push without an explicit user
decision.

## Records layout (Beads first; `.mba-work` when useful, Charter §10)

```text
Bead comments            complete normal human status / decision record
.mba-work/<bead-id>/
├── orchestrator/        optional material coordination notes
├── <session-name>/      prompt.md + optional bulky evidence / machine result
└── final/               optional generated artefacts + convergence.md
```

## CLI (`python -m mba_runtime`)

`--version` prints MBA's shared `mba_version.__version__` value without
running a subcommand.

| Subcommand | Purpose |
|---|---|
| `first-contact` | Show activation status + AI-resource setup questions/readiness. |
| `drive-bead` | Drive one Bead end-to-end (`--dispatch-worker` for a real subprocess). |
| `route` | Show the SessionPlan for a team + pattern. |
| `resources` | Show the ordered suitable-resource fallback + bounds. |
| `gate` | Exercise the §11 user-authority gate against a decisions file. |
| `comment render` | Preview a role-attributed comment. |
| `graph verify` | Run `bd dep list` + `bd dep cycles`. |
| `convergence check` | Render a stub §8 transcript. |
