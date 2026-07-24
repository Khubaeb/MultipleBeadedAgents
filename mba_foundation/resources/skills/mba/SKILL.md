---
name: mba
description: Use when an MBA-aware project needs a self-running copy-and-use workflow. Trigger on requests like "drive this Bead", "run an MBA loop", "spawn the auditor for this task", "what stage is this Bead in", or "show me the convergence for an in-progress Bead". Use this skill to obtain the canonical MBA Orchestrator / Doer / Auditor workflow, the assignment-contract shape, the §8 adversarial convergence contract, and the §11 user-authority gate that blocks Git-push / Dolt-push / destructive actions.
---

# MBA — Multiple Beaded Agents workflow

MBA turns one or more AIs into a configurable project team on top of [Beads](https://github.com/gastownhall/beads). It is **not** a task database and **not** a fixed pipeline. The Orchestrator, Doer, and Auditor are the only three workflow responsibilities; "Developer", "Researcher", "Writer" and similar are organisational roles (hats) a Doer or Auditor wears.

> **Source of truth:** [`docs/mba/charter.md`](../../../docs/mba/charter.md). This skill is the **copy-and-use entrypoint**; the charter is the contract.

## First step

If this skill is loaded in an MBA-installed repo and the user asks for
project work, the active session is the **Orchestrator** first. MBA is not a
resident daemon; activation happens because the AI reads the repo
instructions/skill.

```bash
bd version
python -m mba_runtime first-contact --cwd . --apply-setup
```

> Prefer the **`python -m mba_runtime …`** module form. The `mba-runtime`
> console script that `pip install` registers is an optional shortcut
> that works only when its install location is on `PATH` (for example,
> Windows `pip install --user` writes the script to
> `%APPDATA%\Python\Python3XX\Scripts`, which is not on `PATH` by
> default). The module form needs only a working `python` and the
> installed `mba_runtime` package.

Refuse to proceed when the recorded Beads version is not in the validated set
`bd 1.0.4`. Record the value before any `bd` write — see Foundation §3 of the
project instruction file. If `first-contact` reports missing or incomplete
AI-resource configuration, use the JSON `recommended_setup_bead` to create
or update the `MBA setup` task, assign it to `Human`, mark it `blocked`,
add `mba`, `setup` and `human` labels, and post the structured Markdown
question comment. In that setup branch, stop before creating/driving
executable Beads or launching workers; continue with the normal separate
Doer/Auditor worker flow only when first-contact reports ready.

> Prefer the deterministic, runtime-assisted path: pass `--apply-setup`
> so `first-contact` itself creates or updates the blocked `MBA setup`
> Bead and posts the setup question comment with explicit `--actor
> Orchestrator`. The CLI still returns exit code `4` so the
> Orchestrator stops before executable Beads or worker launches. A
> plain `first-contact` (without the flag) stays read-only when
> resources are missing; the runtime never auto-writes without the
> explicit flag.

AI-resource `id` values are local nicknames only. A ready default
Doer/Auditor resource must include `launch.tool` and `launch.model`
in `.mba-work/.ai-resources.json`; use `launch.model` as the CLI
model argument and never the resource `id`. For OpenCode, the model
must be a provider/model string such as
`minimax-coding-plan/MiniMax-M3`, with `launch.variant` carrying
values such as `max`.

## Beads write discipline

Never rely on Beads' default actor. AI work must not be recorded as the user unless the user actually made that input, decision, or action.

Use explicit role actors and assignees for every Beads write:

```powershell
# Create a bounded task as Orchestrator.
bd --actor "Orchestrator" create --title "Short clear title" --type task --priority 2 --assignee "Orchestrator" --labels "atlas,research"

# Update status or assignee as Orchestrator.
bd --actor "Orchestrator" update <id> --status in_progress --assignee "Orchestrator"

# Post a role-attributed comment from a file.
bd --actor "<Role>" comments add <id> -f .mba-work\<id>\<session>\comment.md
```

Do not use `bd update --claim` for an AI role because it claims as the current OS/Git user. Do not use unsupported comment forms such as `bd comments add -t -` or `-f -`. On Windows/PowerShell, do not use Bash-only shortcuts such as `||`, `head`, `wc`, `ls -la`, or heredocs; use PowerShell-native commands.

## Copy-and-use workflow

When a user asks to drive an executable Bead end-to-end:

1. **Read the Bead.** `bd show <bead> --json`. Confirm the Bead is open and not blocked; record any `bd comments add` history.
2. **Pick the responsibility.** Orchestrator coordinates; Doer makes the artefact/result; Auditor challenges it. Never wear both Doer and Auditor hats in the same session.
2a. **Stay thin and use Beads only.** The Orchestrator stays thin: read only enough to classify the request, choose/reuse/create Beads, choose applicable stages/roles, write pointer-based worker prompts, launch workers, verify required artefacts, handle blocks, and close. Do not pre-solve the task, research commit diffs, paste large findings into prompts/notes, or do Doer/Auditor analysis that workers can get from the Bead and referenced files. Beads is the only workflow task tracker: do not use OpenCode `todowrite` or any host-internal todo, task-list, or planning tracker.
3. **Launch real worker sessions.** Do not satisfy the Doer/Auditor pair by switching hats inside one transcript. Do **not** satisfy the pair by calling the host's built-in subagent tooling (OpenCode's internal `task` tool, the `General Agent` subagent, or any "delegate to another model" facility) as a stand-in for a Doer/Auditor worker; those share this Orchestrator transcript and never produce an MBA launch receipt, so the round would silently fail Charter §3 even when the output looks right. Write the full assignment to the session's `prompt.md`, then launch or resume a distinct worker session/process with only a short plain pointer such as "Read .mba-work/<bead-id>/<session-name>/prompt.md and follow it." On Windows / OpenCode the canonical worker surface is `opencode run --agent mba-worker …` launched through a resolved direct `opencode.exe` path and `Start-Process -WindowStyle Hidden` (see step 3b). Do not launch workers with the `mba` Orchestrator agent, a bare `opencode` PowerShell shim, or a `.cmd` wrapper when the real executable can be resolved. Do not put Markdown backticks, command examples, or rich assignment text in a PowerShell inline launch message because PowerShell can interpret and corrupt backticked text. If you cannot launch/resume the required separate session, make the Bead visible to the human: post a structured comment, set status `blocked`, assign `Human`, and add the `human` label. A hidden, headless, or non-interactive Orchestrator must never ask only inside its hidden transcript and must never manufacture convergence from the Orchestrator transcript. A user-approved Orchestrator+one-worker pattern still needs the opposing worker in a separate session.
3a. **Write and validate the launch receipt in the same launch step, before any wait, tail, read, or accept of worker output.** For every Doer and Auditor session the Orchestrator dispatches, the launch step is **one logical block**: write `prompt.md`, run a **prompt self-check** against the compact-pointer budget (`≤ 4 KiB` and `≤ 60` non-blank lines; no unified-diff payload; no precomputed source-result values such as `ahead_by: <n>` / `behind_by: <n>` / `total_commits: <n>`; at least one retrievable source pointer), launch the worker hidden/background, capture the PID or session id, **immediately** write `.mba-work/<bead-id>/<session-name>/launch.md`, and only then wait, tail logs, read `result.md`, read the Bead comment, or decide convergence. A failed self-check forces a rewrite in the same step; an unfixable prompt blocks the Bead to `Human` instead of launching. The receipt must record: `Bead` and `Session name`, the literal `Launch command shape` (the short plain pointer only, never the rich assignment), `PID or session id` (or `not_available` plus a one-line reason when the host does not expose one), `Start time (UTC)` as ISO-8601, `Model / AI resource` (the AI label/id the launch targeted, drawn from `.mba-work/.ai-resources.json` when used), `Prompt path` (the session's `prompt.md`), `Log / result paths`, and a `Windows UX` note on Windows / OpenCode hosts. When the host exposes transcript or session identity, also record the launched worker identity and the Orchestrator identity, then verify that output is tied to the launched worker and does not match the Orchestrator. PID-only proof is insufficient when stronger host identity exists. Writing the receipt **after** any worker output exists — `result.md`, `report.md`, the Bead comment, or a transcript tail — is a refusal-grade error: the receipt no longer proves the session existed before the output. If `.mba-work/<bead-id>/<session-name>/launch.md` is missing or stale when an output arrives, the Orchestrator makes the Bead visible to the human instead of treating the worker comment as proof a separate session was launched.

The worker folder is **exactly** `.mba-work/<bead-id>/<session-name>/`. The `<bead-id>` component MUST be the actual Bead ID returned by `bd show <bead-id>` for the work this session is launched for — never a friendly install name, the test harness, a ref hash, or a wrapped sibling-clone folder. A non-bead-scoped path (`.mba-work/<friendly-name>/...` where `<friendly-name>` is not the Bead ID) is a refusal-grade error: the Bead cannot be wired back to its Beads record. The `<session-name>` is a bounded sub-name such as `doer`, `auditor`, or `doer-round2`.

Process stdout/stderr from the worker launch is captured as `run.log` / `run.err` in the same session folder via the `-RedirectStandardOutput` / `-RedirectStandardError` arguments on `Start-Process` (or the host equivalent). OpenCode may flush stdout late, so these are **captured log files**, not a guaranteed live stream — do not assume `run.log` is empty just because the worker has not yet finished.
3c. **Canonical launch directory creation.** The worker session folder is created with `New-Item -ItemType Directory -Force -Path <session> | Out-Null` directly, **without** any pre-check of the parent directory (`Split-Path -Parent $session` followed by `if (-not (Test-Path -LiteralPath $parent)) { throw "Missing parent ..." }`). That pre-check is a known bug pattern: `New-Item -Force` already creates missing parent directories, so the pre-check can short-circuit a clean launch. The session path is the single argument; do not split it into separate parent-and-session steps.
3b. **Windows / OpenCode launch is hidden and background by default.** Resolve the real `opencode.exe` behind any npm shim before `Start-Process`; `Start-Process -FilePath "opencode"` can pick `opencode.ps1` and fail, while `opencode.cmd` can leave the receipt tied to a wrapper `cmd.exe` PID. Use `opencode.exe run --agent mba-worker …` with `Start-Process -WindowStyle Hidden -RedirectStandardOutput ... -RedirectStandardError ... -PassThru` (PowerShell) or the equivalent non-interactive background launcher on the host so the user's desktop never shows empty CMD / conhost windows. The `--model` value comes from the configured resource's `launch.model`; the `--variant` value comes from `launch.variant`. Never pass the resource `id` as the model. On Windows/PowerShell, never use Bash-only `||`, heredocs, `head`, `wc`, or `ls -la`; use separate commands, `$LASTEXITCODE`, or PowerShell `if (...) { ... }`. Visible empty CMD windows are bad UX and carry no useful progress; worker progress lives in the worker's log files, the launch receipt, the Bead comment, and any transcript the worker writes — never in a desktop window. A visible empty window means the launch is wrong; fix the launch step, do not accept it. The empty CMD window is not the progress surface; logs, Bead comments, and launch receipts are.
4. **Fill the §10 assignment contract.** Required Markdown fields: `Bead`, `Stage`, `Responsibility` (`Doer`/`Auditor`), `Organizational role`, `Session purpose`, `Task`, `Read`, `Produce`, `Acceptance`, `Authority and limits`. Keep the prompt pointer-based: Bead id, stage, role/hat, authority, acceptance shape, and paths/URLs to read. Do not paste long diffs, pre-researched conclusions, or bulky context workers can fetch from Beads/files/URLs. Every worker records worker-internal AI delegation as `none` or each inner LLM/session/subagent/tool delegation with role, tool/session type, and purpose in the Bead comment or linked details file. The `none` value means the launched worker performed no further inner delegation inside its own session after launch — it never stands in for the launch receipt and must never be recorded when no separate worker session was launched for that Bead. Do not include private transcripts or inspect the user's separate personal sessions. Static/implicit Bead fields are **not** repeated merely to fill lines.
5. **Stage labels are general MBA vocabulary.** Use Charter §7 stage names: `Understanding`, `Selection`, `Plan`, `Build`, `Verify`, `Deliver`. Never use this project's internal build-order names (`foundation`, `primitives`, `runtime`, `acceptance`) in user-facing prompts or comments.
6. **Run the §8 adversarial loop.**

    ```text
    Doer produces claim/result
            │
    Auditor returns ACCEPT | FIND | BLOCKED
            │
    ACCEPT, with reasons AND non-empty evidence ⇒ converged
    BLOCKED             ⇒ stop, record non-convergence
    FIND                ⇒ next round (max_rounds bounds the loop)
    ```

    Every executable Bead uses Doer-vs-Auditor opposition. Convergence requires a **verified fix or an accepted proof** — not bare agreement, not elapsed time, not a turn count. A round whose Auditor verdict is `ACCEPT` with **empty reasons** *or* **empty evidence** is a refusal-grade error. When the Auditor assignment requested `_verdict.txt`, that file is mandatory: if it is missing, unreadable, or lacks `VERDICT`, `RESOLUTION`, and non-empty `EVIDENCE`, do not infer `ACCEPT` from `report.md` or a Bead comment. After a `FIND`, an `ACCEPT` also requires **either** a changed artefact **or** an explicit `RESOLUTION: no-change-proof` from the Auditor. If ACCEPT conflicts with supplied known facts, files, logs, Bead state, or prior proof, treat it as unresolved (`FIND`/`BLOCKED`). A known wrong fact in user-facing deliverable content or metadata requires `FIND` until corrected, unless the Auditor gives explicit evidence that it is outside the accepted deliverable or truly non-load-bearing; merely calling it descriptive, minor, or a caveat is not proof. The runtime requires the evidence *structure*; it cannot prove the Auditor truly reasoned.
6a. **User-authority gate is a blocked handoff.** If convergence succeeds but the next required action needs user approval (source Git commit/push, `bd dolt push/pull`, deployment, external message, destructive action, credentials/spend, or reusable workflow adoption), post a structured decision comment, set status `blocked`, assign `Human`, and add `human`. Do not leave the Bead silently `in_progress`; a `ready-for-user-*` label alone is not enough. Do not close the Bead as if the gated action happened.
7. **Combine Auditor verdicts when more than one.** If multiple Auditor sessions ran for the round (pattern `(c)`): any `BLOCKED` ⇒ round `BLOCKED`; else any `FIND` ⇒ round `FIND`; only `ACCEPT` when every Auditor accepted.
8. **Post the useful role-attributed comment.** **Normally 4-16 non-blank structured Markdown lines**; use lists, checklists or compact tables; **no repetition of static/implicit Bead fields**; the comment is the normal complete human record and links a separate details path only when bulky evidence or generated artefacts exist. A comment that is overlong, report-like, or pastes the prompt, a transcript, or static Bead fields is **not** acceptable — the worker must rewrite and repost before the Auditor can `ACCEPT`. The Orchestrator does **not** post a routine kickoff comment to announce "work started" — the launch receipt and the worker's own comment are the normal progress surface. Workers do **not** paste their transcript, prompt, or other bulky evidence into the comment; that belongs in `.mba-work/<bead-id>/<session-name>/` only when it is actually useful. An Auditor must return `FIND` while a worker comment exceeds the 4-16 non-blank budget, pastes the prompt or a transcript, or repeats static Bead fields — calling it "descriptive", "minor", or "useful context" is not enough; the worker must rewrite the comment and repost before the Auditor can accept the round.
9. **Human-needed handoff.** If a Doer or Auditor is blocked for a human decision, cannot produce a valid worker report/comment after the configured **wait → resume → relaunch** policy, or has an unverified launch identity, the runtime makes the Bead visible: post the decision or failure comment, add `human`, set status `blocked`, and assign `Human`; this makes it visible through `bd human list`. The bounded wait/resume/relaunch policy is the contract: bounded wait for the worker's expected artefacts, attempt a resume of the same MBA-owned session, then a single clean relaunch with a fresh receipt; if any of those still leaves the Bead without a valid worker comment, stop and hand the Bead to the human. A clean relaunch requires a fresh receipt before any output is read or accepted.
9b. **Orchestrator resume after restart / disconnection.** When the active Orchestrator session resumes after a restart, disconnection, or other loss of in-progress state, it must not assume an open Bead is still being worked. The first action for every in-progress Bead is to read the Bead's launch receipt at `.mba-work/<bead-id>/<session-name>/launch.md` and the worker artefacts (`report.md` / `comment.md` / verdict files) to determine the round's actual state. If the launch receipt exists but the worker artefacts are missing, continue the same bounded **wait → resume → relaunch → blocked/Human** contract — do not silently reopen the Bead and do not leave it `in_progress` indefinitely. A Bead that cannot be recovered gets a structured Bead handoff (status `blocked`, assignee `Human`, label `human`) just like any other stalled worker.
9c. **Targeted Beads context reads.** Routine context recovery uses targeted Beads reads: `bd show <bead-id>` for the active Bead, `bd ready` for the next Bead, `bd list --status=...` / `bd list --label=...` / `bd list --assignee=...` for filtered lists, `bd search <text>` for free-text. Broad `bd list --all` is not a routine context-recovery move — it dumps every Bead in the workspace and does not pay for itself when the Orchestrator already knows the active Bead id. Reach for `bd list --all` only when the Orchestrator genuinely has no other pointer to the work and the user has explicitly asked for a full roster.
10. **Wire and verify the dependency graph.** Dependency writes also use an explicit actor, e.g. `bd --actor "Orchestrator" dep add <issue> <depends-on> --type related`. Then verify before any closure: `bd dep list <bead>` and `bd dep cycles`. Fail loudly on cycles.
11. **Close only on convergence:** `bd close <bead> --reason=...`.

## §11 user-authority gate

Pause for an explicit user decision before any of:

- source Git commit or push,
- `bd dolt push` / `bd dolt pull`,
- deployment, external message, credentials or spending,
- destructive change,
- adoption of a reusable workflow change.

The runtime refuses these without a recorded human-origin decision. Default actors (`cli`, etc.) never qualify.

## What MBA will not do

- Not a second task database or scheduler — Beads is the durable record.
- Not a provider-discovery service — the user configures AIs in `.mba-work/.ai-resources.json`; the Orchestrator may only offer read-only local discovery.
- Not a fixed pipeline — stages, hats, and session counts are dynamic.
- Not a silent self-modifier — reusable workflow changes need user approval.
- Not a resident background service — it runs when an AI/tool reads the installed instructions and follows them.

## Where the evidence lives

| You want… | Open… |
|---|---|
| Normal human status / decision record | The Bead's comments. |
| Bulky evidence, prompts, machine transcripts | `.mba-work/<bead-id>/<session>/`, when useful. |
| Combined generated artefacts | `.mba-work/<bead-id>/final/`, when useful. |

## Quick diagnostic commands

```bash
bd ready                                 # find available work
bd show <id>                             # inspect a Bead
bd --actor "Orchestrator" update <id> --status in_progress --assignee "Orchestrator"
bd --actor "Orchestrator" dep add <id> <depends-on> --type related
bd --actor "Orchestrator" close <id> --reason="..."   # close after converged
bd --actor "<hat>" comments add <id> -f <file>         # post a role-attributed comment
bd dep list <id>                         # verify the dependency graph
bd dep cycles                            # refuse on any cycle
```

For the full normative contract see [`docs/mba/charter.md`](../../../docs/mba/charter.md). For the Beads layer (validated versions, capability record) see [`docs/beads/capabilities.md`](../../../docs/beads/capabilities.md).
