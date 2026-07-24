# Multiple Beaded Agents Requirements

> Source of truth for what Multiple Beaded Agents (MBA) must do. Implementation choices belong in research or later design work.

## 1. Goal

Use one or more AIs as a configurable project team that delivers high-quality work through:

- native Beads coordination;
- stages relevant to the work;
- applicable expertise for each opposing responsibility;
- one or more sessions for each responsibility when the work's criticality benefits from them;
- evidence-backed opposition between a maker and a reviewer;
- simple, portable setup for engineering and non-engineering work.

Priority: **quality**, then **speed**, then **resource efficiency**.

## 2. Product boundary

| Included | Not required |
|---|---|
| Work inside one folder/repository and its descendants | Work outside that root |
| One or many callable AIs | A non-AI workflow |
| User-configurable AIs, teams, roles and sessions | Fixed providers, models or plans |
| Beads as the durable work record | A second task database or scheduler |
| Dynamic stages, expertise and session counts | The same stages, expertise or session counts on every task |
| A simple local resource record | An AI-discovery service or provider adapters |
| Optional read restrictions | Complex access controls by default |
| User-approved improvement | Silent self-modification |

At least one callable AI is required.

## 3. Three responsibilities

| Responsibility | Purpose |
|---|---|
| Orchestrator | Coordinates the work, Beads, assignments, questions and user authority |
| Doer | Makes the result and supports it with reasons and evidence |
| Auditor | Challenges the result, verifies evidence and records findings |

These are the only workflow responsibility types.

Developer, Researcher, Writer, Designer, Tester and similar names are **organizational roles (hats)** used by a Doer or Auditor. They are not additional workflow responsibilities.

| Choice | Meaning |
|---|---|
| Stage | What work must be done |
| Responsibility | Who produces and who challenges: Doer and Auditor |
| Organizational role | What expertise each responsibility needs |
| Session count | How many independent executions improve that responsibility's work |

These choices are made separately. Every executable stage has both Doer and Auditor responsibilities; each side uses the applicable organizational role and one or more sessions.

- The AI session started by the user is the Orchestrator.
- Activation is instruction-driven, not daemon-driven: when MBA is installed
  and the active AI reads the repo instructions or MBA skill, that AI must
  enter Orchestrator mode before doing the work itself.
- With user agreement, that session may also be the Doer or Auditor for a Bead, but never both.
- Doer and Auditor use separate sessions. If only one AI is available, it uses separate fresh sessions for the two responsibilities.
- One transcript changing hats is not a Doer/Auditor pair. OpenCode's internal `task` / `General Agent` subagents share the Orchestrator transcript and therefore are not MBA workers. OpenCode launches use separate external hidden/background `opencode run --agent mba-worker` processes with launch receipts. If the Orchestrator cannot launch or resume the required separate worker session(s), it records the block instead of manufacturing convergence. A hidden, headless or non-interactive Orchestrator never asks only inside its hidden transcript: it posts the structured human-handoff comment, marks the Bead `blocked`, assigns `Human`, and adds the `human` label.
- The Orchestrator stays thin. It reads only enough to classify the request, choose/reuse/create Beads, choose applicable stages/roles, write concise pointer-based worker prompts, launch workers, verify required artefacts, handle blocks, and close. It must not pre-solve the task, research commit diffs, paste large findings into prompts/notes, or perform Doer/Auditor analysis that workers can get from the Bead and referenced files.
- Beads is the only workflow task tracker. The Orchestrator and workers must not use host-internal todo, task-list or planning trackers (including OpenCode `todowrite`); record shared work and follow-up in Beads instead.
- A real worker session is identified by **observable launch evidence** written by the Orchestrator **in the same launch step that captures the worker's PID or session id, and before any wait, tail, read, or acceptance of the worker's output (Bead comment, transcript, `result.md`, `report.md`)**. The minimum is `.mba-work/<bead-id>/<session-name>/launch.md` recording the launch command shape, the PID or session id when the host exposes one, the start time, the model / AI resource the launch was pointed at, the prompt path, the log / result paths, and a Windows-UX note. When the host exposes transcript or session identity, the receipt also records the launched worker identity and the Orchestrator identity; output is accepted only when it is tied to the launched worker and differs from the Orchestrator. PID-only proof is insufficient when stronger host identity exists. Without valid proof, the outputs did not come from a real Doer/Auditor worker and the Orchestrator performs the visible blocked/Human handoff or refuses the round. Writing the receipt **after** any worker output exists is a refusal-grade error: the receipt no longer proves the session existed before the output.
- The worker folder is **exactly** `.mba-work/<bead-id>/<session-name>/`. The `<bead-id>` component MUST be the actual Bead ID returned by `bd show <bead-id>` for the work this session is launched for — never a friendly install name, the test harness, a ref hash, or a wrapped sibling-clone folder. A non-bead-scoped path (`.mba-work/<friendly-name>/...` where `<friendly-name>` is not the Bead ID) is a refusal-grade error: the Bead cannot be wired back to its Beads record, so the launch receipt is invalid and the round must be relaunched with the correct path. The `<session-name>` is a bounded sub-name such as `doer`, `auditor`, or `doer-round2`.
- If the worker's `report.md` or `comment.md` is still missing, use the bounded recovery sequence: wait, resume the same MBA-owned session, then perform one clean relaunch with a fresh receipt written before reading or accepting new output. If output is still missing, the Bead must not remain silently `in_progress`; the Orchestrator performs the visible blocked/Human handoff.
- **Canonical launch directory creation.** The Orchestrator creates the worker session folder with `New-Item -ItemType Directory -Force -Path <session> | Out-Null` directly, **without** any pre-check of the parent directory (`Split-Path -Parent` / `Test-Path -LiteralPath $parent` / `if (-not (Test-Path ...)) { throw "Missing parent ..." }`). The pre-check shape is a known bug-pattern: `New-Item -Force` already creates missing parent directories, so the pre-check only ever short-circuits a clean launch. The session path is the single argument; do not split it into separate parent-and-session steps.
- **Orchestrator resume after restart / disconnection.** When the active Orchestrator session resumes after a restart, disconnection, or other loss of in-progress state, the first action for every in-progress Bead is to read the Bead's launch receipt at `.mba-work/<bead-id>/<session-name>/launch.md` and the worker artefacts (`report.md` / `comment.md` / verdict files) to determine the round's actual state. If the launch receipt exists but the worker artefacts are missing, the Orchestrator continues the same bounded **wait → resume → relaunch → blocked/Human** contract — it does not silently reopen the Bead and does not leave it `in_progress` indefinitely. A Bead that cannot be recovered gets a structured Bead handoff (status `blocked`, assignee `Human`, label `human`) in the same way as any other stalled worker.
- **Targeted Beads context reads.** The Orchestrator's routine context recovery uses targeted Beads reads: `bd show <bead-id>` for the active Bead, `bd ready` for the next Bead, `bd list --status=...` / `bd list --label=...` / `bd list --assignee=...` for filtered lists, `bd search <text>` for free-text. Broad `bd list --all` is not a routine context-recovery move — it dumps every Bead in the workspace and does not pay for itself when the Orchestrator already knows the active Bead id. Reach for `bd list --all` only when the Orchestrator genuinely has no other pointer to the work and the user has explicitly asked for a full roster.
- On Windows / OpenCode, every Doer and Auditor worker launch is hidden and background by default. Use `Start-Process -WindowStyle Hidden -RedirectStandardOutput ... -RedirectStandardError ... -PassThru` (PowerShell) or the equivalent non-interactive background launcher on the host. Visible empty CMD / conhost windows on the user's desktop are bad UX and carry no useful progress; worker progress lives in the worker's log files, the launch receipt, the Bead comment, and any transcript the worker writes — never in a desktop window. A visible empty window means the launch is wrong; fix the launch step, do not accept it.
- **Harness-neutral worker capture.** The Runtime captures every MBA-launched worker by **redirecting its stdout and stderr directly to per-session `run.log` / `run.err` files** under `.mba-work/<bead-id>/<session-name>/`. The capture is **durable across viewer restart and disconnect**: a viewer (the Orchestrator, a follow-on Auditor, or the `mba stream` subcommand) reads the same files the worker wrote without sitting between the worker and its durable sink. Captured files are **process logs**, not a token-live stream: OpenCode `run --format json` emits at step / tool / completed-part granularity, and `--thinking` is a display-only completion-granular switch with provider-dependent coverage; the runtime never claims token-level fidelity. Direct file capture is the canonical first-release design; an async tee wrapper, OpenCode `serve`/SSE/SDK ownership, TUI/ConPTY capture, and token-by-token output are explicitly out of scope.
- **Optional hidden OpenCode Orchestrator.** If MBA itself launches an OpenCode Orchestrator session, the same capture surface, launch-receipt discipline, and MBA-owned cleanup apply. The hidden OpenCode Orchestrator is **optional** because most users start the Orchestrator themselves in a user-visible harness (Codex, Claude, ZCode, MiniMax Code, etc.). When the active Orchestrator runs inside one of those harnesses, MBA does not claim transcript capture: it records the milestones MBA itself produces (launch receipts, exact worker identities, recovery decisions, comments, and required artefacts) and lets the host own its own chat UX.
- Worker-internal AI delegation (`none` or each inner LLM/session/subagent/tool delegation) describes what the launched worker did *inside its own session after launch*; it does not prove the worker session itself was launched, and a recorded `none` for worker-internal delegation never justifies worker outputs when the launch receipt is missing.
- Organizational-role display names are configurable; role names are the default.

## 4. Sessions and AI resources

The user can configure:

- available AIs and how to call them;
- which AI performs each responsibility or organizational role;
- session lifetime;
- the number of sessions used for one responsibility.

| Pattern | Guidance |
|---|---|
| One Doer session + one Auditor session | Default |
| Several sessions for one responsibility | Use when criticality or independent coverage justifies them, such as research or critical review |
| Several sessions editing one artifact | Avoid; use only when work is safely separable |

Extra sessions keep the same responsibility and organizational role. Their findings are combined before the opposing responsibility reviews the result.

On first contact in an MBA-installed repo, the Orchestrator checks the
private AI-resource record. If it is missing, incomplete or inconsistent,
the Orchestrator asks the user for the available AIs/plans and may offer
read-only local discovery checks. MBA does not need its own discovery engine
and never invents providers, plans, models or teams.

Minimum resource entry:

```json
{
  "id": "minimax-m3-max",
  "label": "MiniMax-M3 Max via OpenCode",
  "capabilities": ["doer", "auditor"],
  "session_lifetime": "fresh_per_session",
  "launch": {
    "tool": "opencode",
    "model": "minimax-coding-plan/MiniMax-M3",
    "variant": "max"
  }
}
```

The resource `id` is a stable project nickname. It is not a CLI model
argument. Worker launches use `launch.model` and `launch.variant`.
First-contact is not ready until every default Doer/Auditor AI has a
callable `launch` object. For OpenCode, `launch.model` must be the real
provider/model string, such as `minimax-coding-plan/MiniMax-M3`.

The deterministic first-contact check is:

```bash
python -m mba_runtime first-contact --cwd . --apply-setup
```

> Prefer the **`python -m mba_runtime …`** module form. The
> `mba-runtime` console script that `pip install` registers is an
> optional shortcut that works only when its install location is on
> `PATH` (Windows `pip install --user` writes it to
> `%APPDATA%\Python\Python3XX\Scripts`, which is not on `PATH` by
> default); the module form needs only a working `python` and the
> installed `mba_runtime` package.

If the check is not ready, its JSON recommends creating or updating the
`MBA setup` task as a `task` assigned to `Human`, marked `blocked`, and
labelled `mba`, `setup` and `human`. The Orchestrator posts the supplied
structured Markdown question comment and stops before creating or driving
executable Beads or launching workers. If it returns ready, the Orchestrator
reads the Bead and launches separate Doer/Auditor worker sessions.

## 5. Workspace and configuration

- The selected folder/repository and all descendants are in scope.
- The user may mark paths as:
  - **no-read**; or
  - **read-only**.
- Project configuration is portable and shareable.
- The local AI-resource record is simple, private and ignored by Git.
- The user chooses whether `.mba-work/` is Git-ignored local working or Git-tracked shared working.
- Credentials remain in their existing credential stores.
- A webpage may edit the same configuration later; files remain sufficient.

## 6. Work shapes

```text
Atomic Task
`- one independently closable result

Task with stages
`- Parent Task
   |- Understanding
   |- Selection
   `- applicable stage Sub-tasks

Epic
|- shared Understanding
|- shared Selection
`- related Tasks
   `- applicable Sub-tasks
```

| User request | Required handling |
|---|---|
| Atomic Task | Keep one Task; do not create an Epic or unnecessary Sub-tasks |
| Task with stages | Use one Parent Task with independently useful Sub-tasks |
| Epic-level goal | Use one Epic containing related Tasks; Tasks may have Sub-tasks |

Exact promotion or reparenting must follow verified native Beads behavior while retaining the original Understanding history.

## 7. Dynamic lifecycle

```text
Understand + ask questions
            |
       classify work
            |
   atomic? ---- yes ---> produce + audit in the same Task
      |
      no
      v
Select applicable stages
            |
Assign each stage's Doer and Auditor expertise,
AI resources, session counts and dependencies
            |
Create only the required remaining Beads
            |
Run ready work, in parallel when safe
            |
Complete and capture improvements
```

- Work starts with an Understanding Bead reviewed by a Doer and an Auditor.
- Ask every question needed to make the work clear and doable.
- An atomic request remains that Task.
- Non-atomic work receives the correct parent and a Selection Bead.
- Selection decides separately:
  - relevant stages;
  - applicable organizational roles for each Doer and Auditor;
  - AI assignments and session counts based on criticality;
  - evidence and dependencies.
- Each resulting executable Bead uses Doer-Auditor opposition.
- An Epic provides shared context; each Task asks only its remaining task-specific questions.

## 8. Adversarial work

```text
Doer produces claim or result
          |
Auditor challenges with reasons and evidence
          |
Doer fixes the result or disproves the finding
          |
Auditor rechecks
          |
Convergence or recorded non-convergence
```

Convergence requires a verified fix or accepted proof. Agreement, confidence, reputation, elapsed time or a fixed turn count is not convergence.

An Auditor `ACCEPT` with evidence that contradicts supplied known facts, logs, files, Bead state or prior proof is unresolved and becomes `FIND` or `BLOCKED`. In particular, a known wrong fact in user-facing deliverable content or metadata requires `FIND` until corrected, unless the Auditor gives explicit evidence that the fact is outside the accepted deliverable or is truly non-load-bearing. Calling it descriptive, minor or a caveat is not sufficient proof. A compact audit packet is preferred over huge Bead dumps when contradictions matter.

A configured limit may stop the loop, but the Bead then remains open or blocked with the unresolved positions recorded.

## 9. Beads requirements

- Detect and validate Beads; install or initialize it only with user authority when needed.
- Use native Beads meanings for Epics, Tasks, child Tasks/Sub-tasks, dependencies and readiness.
- Use the shortest clear Bead title. Put scope, method and acceptance details in their own fields; do not make the title cryptic or repeat its parent, type, status, role or model.
- Use correct types, labels, priorities, assignees, statuses and acceptance criteria.
- Never rely on Beads' default actor for an AI write. Use explicit role actors such as `bd --actor "Orchestrator" ...`, `bd --actor "Doer" ...`, or `bd --actor "Auditor" ...`; user attribution is only for real user input, decisions or actions.
- Do not use `bd update --claim` for AI-role work, because it claims as the current OS/Git user. Set status and assignee explicitly.
- Dependency writes are AI Beads writes too; use `bd --actor "Orchestrator" dep add ...` rather than bare `bd dep add ...`.
- Create discovered work as a related Bead when it is independently actionable.
- Keep real dependency direction and safe parallel work.
- Workers receive a Bead ID and a pointer to their Orchestrator-written `prompt.md`; they read context from the Bead and referenced files.
- Use a native Beads capability when it satisfies the requirement correctly and more simply than MBA-owned behavior.
- Keep Beads/Dolt synchronization separate from source Git.
- For disposable Beads tests, use an approved temporary root with no ancestor `.beads`; never run `bd init --reinit-local` or other destructive init flags from an unchecked path.

Which native Beads features MBA uses, conditions adoption of, or excludes is the full record in the tracked file `docs/beads/capabilities.md`. MBA adopts direct `bd` CLI and the native simple-ordinary path as Core, the v1.1.0 advanced-feature layer as Conditional when a documented use case justifies it, and the official plugin, MCP, custom types, persistent proto beads, bond-point CLI addressability, and destructive durable-molecule operations as Excluded. The capability record also carries the version policy, the setup rule, and the worker Beads access and comment-format rules cited in §10.

## 10. Records and evidence

| Record | Location |
|---|---|
| Normal human status, decision, material risk and next action | Bead comment |
| Worker prompt / machine transcript / bulky evidence, when useful | `.mba-work/<bead-id>/<session-name>/` |
| Orchestrator coordination details, only when material | `.mba-work/<bead-id>/orchestrator/` |
| Combined generated artefacts, when useful | `.mba-work/<bead-id>/final/` |

```text
.mba-work/<bead-id>/
|- orchestrator/
|  |- working.md   # optional material coordination notes
|  `- result.md    # optional concise coordination result or handoff
|- <session-name>/
|  |- prompt.md    # assignment and references
|  |- working.md   # optional bulky analysis/evidence
|  `- result.md    # machine verdict/result when needed
`- final/          # optional combined approved artefacts
```

### Assignment contract

The Orchestrator uses the same contract for every worker session:

```markdown
# <Organizational Role> Assignment

- **Bead:** <Bead ID>
- **Stage:** <current stage>
- **Responsibility:** Doer | Auditor
- **Organizational role:** <required expertise>
- **Session purpose:** <main, independent view, specialist check, etc.>
- **Task:** <result required from this session>
- **Read:** <Bead and exact file paths>
- **Produce:** Bead comment; files only when needed for machine verdicts, bulky evidence, or deliverables
- **Acceptance:** <conditions for this session>
- **Authority and limits:** <allowed and prohibited actions>
- **Worker-internal AI delegation:** Record `none` or every worker-internal AI delegation in the Bead comment or linked details file, including role, tool/session type, and purpose. Do not include private transcript contents or inspect personal user sessions. This line describes what the worker did *inside its own session after launch*; it never substitutes for the launch receipt and never justifies worker outputs when the launch receipt is missing.
```

Worker prompts are pointer-based. The Orchestrator supplies the Bead id,
stage, responsibility, role/hat, authority limits, acceptance shape and
paths/URLs the worker should read. It does not paste long diffs,
pre-researched conclusions or bulky context that the worker can read from
Beads/files/URLs itself.

**Pre-launch prompt self-check.** Before
``Start-Process`` and the launch-receipt write, the Orchestrator
self-checks the worker's ``prompt.md`` against a compact-pointer
budget: ``<= 4 KiB`` and ``<= 60`` non-blank lines; **no** unified-diff
payload (``diff --git``, ``+++ b/``, ``@@``); **no** precomputed
source-result values such as ``ahead_by: <n>`` / ``behind_by: <n>`` /
``total_commits: <n>``; at least one retrievable source pointer
(``bd show``, absolute / relative path, or URL). A failed check
forces a rewrite in the same step; an unfixable prompt blocks the
Bead to ``Human`` instead of launching. A launch receipt written
against a prompt that violates this rule is itself invalid.

Responsibility rules:

- **Doer:** produce the assigned result with reasons and evidence.
- **Auditor:** challenge the result and evidence, record findings, and recheck corrections.

Stages and organizational roles do not require separate templates. They fill the contract's `Stage`, `Organizational role` and `Task` fields. Multiple sessions reuse the contract with different `Session purpose` values.

The worker-internal AI delegation line is a fixed output requirement. Each worker records `none` or the role, tool/session type, and purpose of every delegation in the Bead comment or linked details file; this does not authorize or require the Orchestrator to inspect the user's separate personal sessions. The `none` value describes the absence of inner delegation inside a launched worker session — it is not a substitute for the launch receipt and must never be recorded when no separate worker session was launched for that Bead.

```text
Choose responsibility, role and session count
                    |
Create the session directory
                    |
Write prompt.md from the assignment contract
                    |
Launch/resume a distinct worker session hidden/background,
capturing the PID or session id in the same step:
"Read .mba-work/<bead-id>/<session-name>/prompt.md and follow it."
                    |
Write .mba-work/<bead-id>/<session-name>/launch.md IMMEDIATELY after
PID/session capture, in the SAME launch step, and BEFORE any
wait / tail / read / acceptance of worker output
(this is the receipt that the session was really launched)
                    |
Worker posts a complete Bead comment
                    |
Opposing role reads the Bead and any linked details, then reviews
```

#### Canonical PowerShell worker launch pattern

The launch step is **one logical block** — never a sequence in which
the Orchestrator starts the worker, waits, reads output, and only then
writes the receipt. OpenCode's local `opencode run` command supports
`--dir`, `--agent`, `--model`, `--variant`, `--auto`, `--title`, and `--format`.
A safe copy-paste shape on Windows / OpenCode:

```powershell
$session = ".mba-work\<bead-id>\<session-name>"
New-Item -ItemType Directory -Force -Path $session | Out-Null
# 1. Write the full assignment to the worker's prompt.md first.
#    (Write-Content with the assignment contract; never the
#    rich assignment inline in the launch command.)

# 2. Resolve the real executable, not the PowerShell shim or .cmd wrapper.
$opencodeExe = $null
$cmd = Get-Command opencode.cmd -ErrorAction SilentlyContinue | Select-Object -First 1
if ($cmd) {
  $candidate = Join-Path (Split-Path $cmd.Source -Parent) "node_modules\opencode-ai\bin\opencode.exe"
  if (Test-Path $candidate) { $opencodeExe = $candidate }
}
if (-not $opencodeExe) { throw "Could not resolve opencode.exe" }

# 3. Launch hidden/background with the worker agent, capture PID,
#    and redirect logs so progress lives in files.
#    Resolve <launch.model> and <launch.variant> from the configured
#    resource in .mba-work/.ai-resources.json. The resource id is only
#    a nickname and must never be passed to --model.
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
| Launch command shape | opencode.exe run --dir "$PWD" --agent mba-worker --model <launch.model> ... "Read $session\prompt.md and follow it." |
| PID or session id | $($proc.Id) |
| Start time (UTC) | $([DateTime]::UtcNow.ToString("o")) |
| Model / AI resource | <resource-id> / <launch.model> / <launch.variant> |
| Prompt path | $session\prompt.md |
| Log / result paths | $session\run.log; $session\run.err; expected $session\report.md; expected $session\comment.md |
| Windows UX | hidden/background launch via Start-Process -WindowStyle Hidden; worker progress is in logs/Bead comments, not desktop CMD windows |
"@
Set-Content -Path "$session\launch.md" -Value $receipt

# 5. Only now may the Orchestrator wait, tail logs, read result.md,
#    read the Bead comment, or decide convergence.
```

The visible-empty-CMD-window failure mode is fixed at the launch step,
not by closing the window afterwards. On non-Windows hosts the
equivalent hidden / background / no-tty launcher applies (e.g.
`nohup ... >log 2>&1 &` or `setsid`); the rule is the same: no
interactive worker window on the user's desktop, redirect logs, write
the receipt immediately, and only then wait.

Process stdout/stderr from the worker launch is captured as `run.log` /
`run.err` in the same session folder via the `-RedirectStandardOutput` /
`-RedirectStandardError` arguments on `Start-Process` (or the host
equivalent). OpenCode may flush stdout late, so these are **captured
log files**, not a guaranteed live stream — do not assume `run.log`
is empty just because the worker has not yet finished. Tailing can provide
hints, but the final proof is the captured logs plus `report.md` /
`comment.md` / verdict files, not a live tail alone.

The `mba-runtime stream <bead-id> <session-name>` subcommand
(`mba_runtime/stream_capture.py`) provides a read-only follower that
replays complete NDJSON records from the capture and, with
`--follow`, polls the file for new bytes. The follower **never**
sits between the worker and its durable sink, buffers partial final
records so a crash cannot produce a fake completed event, redacts
tool payloads and reasoning text by default, and refuses any path
that is not Bead-scoped to `.mba-work/<bead-id>/`. `mba stream` is
the safe surface for any tool that wants to render bounded, redacted
worker progress; raw NDJSON is available with `--raw` for
diagnostic use only.

On Windows / PowerShell, never use Bash-only operators or helpers such
as `||`, heredocs, `head`, `wc`, or `ls -la` in worker-launch or Beads
commands. Use separate commands, `$LASTEXITCODE`, or `if (...) { ... }`
PowerShell-native checks.

#### Observable worker launch receipt (`launch.md`)

For every executable Bead, the Orchestrator creates
`.mba-work/<bead-id>/<session-name>/launch.md` for each Doer and Auditor
session it dispatches, **in the same launch step that captures the
worker's PID or session id, and before any wait, tail, read, or
acceptance of the worker's output (Bead comment, transcript,
`result.md`, `report.md`)**. The receipt carries enough information
that an external reader can confirm a separate worker session existed
and was pointed at the right `prompt.md`. At minimum it records:

| Field | What it captures |
|---|---|
| `Bead` | The Bead ID the session was launched for. |
| `Session name` | The session-name directory under `.mba-work/<bead-id>/`. |
| `Launch command shape` | The literal text of the launch message that pointed at `prompt.md` (the short plain pointer, never the rich assignment). |
| `PID or session id` | Process id, host session id, or other host-provided identifier when one is available; otherwise write `not_available` with a one-line reason. |
| `Worker / Orchestrator identity` | When the host exposes transcript or session identity, record both identities so output can be bound to the launched worker and rejected when it matches the Orchestrator. |
| `Start time (UTC)` | ISO-8601 timestamp at which the launch was issued. |
| `Model / AI resource` | The AI label/id plus executable launch fields from `.mba-work/.ai-resources.json`, especially `launch.model` and `launch.variant`. The id is never the CLI model argument. |
| `Prompt path` | The session's `prompt.md` path that the launch pointed at. |
| `Log / result paths` | Paths to the worker's transcript, log, `result.md`, and final Bead comment file (when the paths are known at launch time; otherwise the Orchestrator updates the receipt at first output). |
| `Windows UX` (Windows hosts) | One-line note that the launch was hidden/background (e.g. `Start-Process -WindowStyle Hidden`) so the empty desktop window is not the progress surface; logs, Bead comments, and launch receipts are. |

The Orchestrator treats this receipt as the only portable proof that a
real Doer/Auditor worker session was launched. When host transcript or
session identity is available, the Orchestrator checks the output identity
against both the launched worker and its own identity before reading the
output as worker evidence. A match with the Orchestrator, a mismatch with
the launched worker, or PID-only evidence when stronger identity exists is
a refusal. When the receipt is missing, stale or invalid, the Orchestrator
does **not** manufacture convergence:

- if a launch is in flight, write the receipt **in the same launch
  step** that captures the PID or session id; do not wait, tail, read,
  or accept any worker output first;
- if a launch cannot be issued, a valid identity cannot be established,
  or `report.md` / `comment.md` remains missing after the configured
  wait/resume/relaunch policy, the Orchestrator posts the structured
  handoff comment, marks the Bead `blocked`, assigns `Human`, adds the
  `human` label, and stops until the user unblocks;
- a clean relaunch is allowed only with a fresh receipt written before
  any output is read or accepted.

For Auditor acceptance, the machine verdict file is mandatory when the
assignment requested one. If `_verdict.txt` is missing, unreadable or
lacks `VERDICT`, `RESOLUTION` and non-empty `EVIDENCE`, the Orchestrator
must not infer `ACCEPT` from `report.md` or a Bead comment. It applies
the bounded wait/resume/relaunch policy or blocks for Human; convergence
requires the verdict protocol, not a prose summary.

A hidden, headless or non-interactive Orchestrator never asks only in its
hidden transcript. The Bead handoff above is the human-visible question
surface.

Writing `launch.md` **after** the worker's `result.md`, Bead comment,
or any transcript tail is a refusal-grade error: the receipt no longer
proves the session existed before the output, so the round has to be
re-launched or the Bead blocked. A worker result produced before
the launch receipt exists cannot prove it came from a separate worker
session. On Windows / OpenCode, the empty
desktop CMD window is not an acceptable substitute for the receipt —
fix the launch step, do not accept it.

Worker sessions may add pointers to their own transcript or result
inside the receipt; the Orchestrator must not read private transcript
contents for the inspection — the receipt is metadata, not a
replacement for the worker's own comment.

The full assignment lives in the worker's `prompt.md`; the launch message is only a short plain pointer to that file. Do not put Markdown backticks, command examples, or rich assignment text in a PowerShell inline launch message because PowerShell can interpret and corrupt backticked text.

The prompt points to the Bead and relevant project files instead of duplicating their context.

If a required worker session cannot be launched or resumed, the Orchestrator blocks or asks the user. It must not complete both Doer and Auditor work inside the Orchestrator transcript merely by changing labels, and it must not accept a worker's Bead comment as evidence of a separate session when `.mba-work/<bead-id>/<session-name>/launch.md` is missing or stale.

Referenced files remain in their original locations; do not copy them into session directories.

Tracked work always uses its Bead ID. An explicit run name may replace it only for a temporary test performed before any Bead exists.

At project setup, the user chooses whether `.mba-work/` is Git-ignored or Git-tracked. Bead comments remain brief and complete in either mode; they link to a details file only when a separate file is useful.

Worker sessions may read Beads and may post useful structured comments directly on their assigned Bead with their organizational role as `--actor`. The Orchestrator normally owns hierarchy, dependencies, closure and Dolt synchronization. A worker may make the narrow human-handoff write when blocked: post the decision comment, add `human`, set status `blocked`, and assign `Human`. Other status, assignment, hierarchy, dependencies, closure and Dolt writes require explicit delegation. `bd --readonly` is used only when isolation is actually required.

Portable Beads writes use explicit actors and file-backed comments:

```powershell
bd --actor "Orchestrator" create --title "Short clear title" --type task --priority 2 --assignee "Orchestrator" --labels "atlas,research"
bd --actor "Orchestrator" update <id> --status in_progress --assignee "Orchestrator"
bd --actor "Orchestrator" dep add <id> <depends-on> --type related
bd --actor "<Role>" comments add <id> -f .mba-work\<id>\<session>\comment.md
```

Avoid unsupported or shell-specific forms: `bd comments add -t -`, comment stdin via `-f -`, Bash-only `||`, `head`, `wc`, `ls -la`, and heredocs in PowerShell.

Normal Bead comments are useful structured Markdown summaries, not fixed-size
receipts. They are **normally 4-16 non-blank lines** and may use lists,
checklists or compact tables. They state the meaningful result, material
changes or findings, verification or current status, next state where useful,
and details. The details line says either that the Bead comment is complete or
gives the exact path to a separate bulky file. They do not repeat static or
implicit Bead fields merely to fill lines; an exception or material risk is
included only when it actually occurred. A comment that is overlong,
report-like, pastes the prompt, a transcript, or static Bead fields is **not**
acceptable — the worker must rewrite and repost before the Auditor can
`ACCEPT`. An Auditor must return `FIND` while a worker comment exceeds the
4-16 non-blank budget, pastes the prompt or a transcript, or repeats static
Bead fields — calling it "descriptive", "minor", or "useful context" is not
enough; the worker must rewrite the comment and repost before the Auditor can
accept the round. The Orchestrator also records its own material coordination
decision, result, exception, recovery or handoff, with applicable detail under
`.mba-work/<bead-id>/orchestrator/` only when useful; it does not comment
merely to announce that work started. AI actions are never attributed to the
user; user attribution is reserved for input, a decision or an action actually
made by the user.

After wiring any dependency edge, verify the resulting graph with `bd dep list` and `bd dep cycles` before considering the wire complete. This rule was confirmed necessary in the installed Beads environment of this project, where the original Task creation used a creation shorthand that produced a reverse displayed edge and was corrected by an explicit `bd dep add`. The portable rule is graph verification, not a claim about a single Beads version.

MBA rules live in the project instruction file under `<!-- BEGIN MBA RULES -->` / `<!-- END MBA RULES -->` markers, separated from generated Beads markers. MBA uses the Beads-generated setup recipe, skill and hooks unchanged when they are present on the active host. The official plugin and the plugin-hosted MCP or standalone `beads-mcp` are not portable-core dependencies; they may be installed independently without changing MBA semantics.

Detailed working contains inspectable claims, reasoning, sources, tests and results—not hidden chain-of-thought, credentials or raw token streams.

For tool behavior, use official information that matches the version being evaluated and test important behavior directly when safe and practical. Check current official information when relevant to identify changes. Where official sources leave a material disagreement or gap, compare relevant recognized expert evidence.

## 11. User authority

User approval is required for material external or persistent actions, including:

- source Git commit or push;
- deployment, publication or external messages;
- credentials, spending or metered use;
- destructive changes;
- remote Beads/Dolt synchronization unless already authorized for that destination;
- adoption of reusable workflow changes.

When Doer/Auditor convergence is reached but a required next action is held
by this user-authority gate, the Orchestrator must not leave the Bead silently
`in_progress` and must not close it as if the gated action happened. It posts
a structured decision comment, sets status `blocked`, assigns `Human`, and
adds the `human` label. A `ready-for-user-*` label alone is not sufficient.

## 12. Improvement

- Record reusable failures and corrections.
- Change the workflow only when evidence shows a reusable benefit and the user approves it.
- Keep local project differences separate from reusable MBA improvements.
- MBA installations can preview, adopt or reject compatible updates from the
  canonical MBA repository without losing project data or configuration.
- Upgrade preserves the private AI-resource record and does not reinitialize
  setup. After upgrade, the Orchestrator re-runs
  `python -m mba_runtime first-contact --cwd . --apply-setup` (the module
  form — see §4) and asks only for missing or newly required choices.
- Removal deletes MBA-managed install content and `.mba/manifest.json`; it
  preserves `.beads` and `.mba-work` unless the user separately chooses to
  archive or delete that project history.

## 13. Acceptance

- Works with one AI or many.
- Uses exactly three workflow responsibilities.
- Supports configurable multiple sessions for either responsibility based on criticality.
- Selects relevant stages, applicable expertise and session counts as separate decisions.
- Preserves native Beads hierarchy and good practices.
- Uses Doer-Auditor opposition on every executable Bead.
- Keeps Bead comments concise, useful and structured, with bulky details only when useful.
- Rebuilds no suitable native Beads capability.
- Remains simple, portable and understandable without chat history.
