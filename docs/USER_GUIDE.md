# MBA user guide — install, target-project use, status, upgrade, remove

> Single-page operating manual. The normative requirements live in
> [`mba/charter.md`](mba/charter.md); the Beads capability record lives in
> [`beads/capabilities.md`](beads/capabilities.md). This page covers
> what the **user of MBA** needs in order to install the tool, initialise
> a target repository, observe its state, refresh its installed content,
> pick up upstream updates, and remove MBA-managed content.

## 1. Install MBA

MBA's Python package name is **`multiple-beaded-agents`**. The current
`v0.1.0` release is installable from the public GitHub tag; PyPI publishing
is a separate future decision. Runtime code is stdlib-only and supports
Python 3.10+.

| Install type | Command |
|---|---|
| Public `v0.1.0` release | `python -m pip install -U git+https://github.com/Khubaeb/MultipleBeadedAgents.git@v0.1.0` |
| Editable local clone | `python -m pip install -e .` |

After install, four console scripts are available:

| Command | Equivalent to |
|---|---|
| `mba` | `python -m mba_foundation` |
| `mba-foundation` | `python -m mba_foundation` |
| `mba-runtime` | `python -m mba_runtime` |
| `mba-primitives` | `python -m mba_primitives` |

Confirm the install with any of them:

```bash
mba --version          # prints the MBA shared version
mba-foundation --help  # subcommand list
```

### Prerequisite: Beads

MBA runs on top of [Beads](https://github.com/gastownhall/beads); a
working `bd` CLI is required.

```bash
bd version   # should print `bd version 1.0.4 (...)`
```

If `bd` is missing or the version differs from the validated set
(`bd 1.0.4`), `mba init` will refuse with `rc=5` and the reason in JSON.
Install or upgrade Beads, then retry.

## 2. Initialise a target project

Pick the repo you want MBA to manage, then run the install from inside it
(or pass `--root <path>`):

```bash
cd /path/to/your-repo
mba init
```

`mba init` writes the managed MBA install surface:

| File | What lands there | Existing content rule |
|---|---|
| `AGENTS.md` | MBA RULES marker block. | Preserve surrounding project text; conflict on user-edited MBA block. |
| `CLAUDE.md` | Same MBA RULES marker block. | Same. |
| `.agents/skills/mba/SKILL.md` | Copy-and-use workflow skill. | Verbatim managed file. |
| `opencode.json` | OpenCode default agent config. | Verbatim managed file. |
| `.opencode/agents/mba.md` | OpenCode Orchestrator agent. | Verbatim managed file. |
| `.opencode/agents/mba-worker.md` | OpenCode Doer/Auditor worker agent. | Verbatim managed file. |
| `docs/mba/*`, `docs/beads/*`, `docs/USER_GUIDE.md` | Local docs referenced by installed instructions. | Verbatim managed docs. |

It also writes `<root>/.mba/manifest.json` (atomic):

| Manifest records | Why |
|---|---|
| MBA version | Detects tool/content drift. |
| Beads preflight evidence | Proves setup used the validated Beads version. |
| Per-file SHA256 | Separates safe refresh from user-edited conflict. |

`.mba/` is local install state; do not publish it.

### Dry-run first

```bash
mba init --dry-run
```

This prints the install plan (rc=4) without touching the filesystem. Use
it to confirm what will land before committing to the write.

### What `mba init` will refuse

| Condition | Exit code |
|---|---|
| `bd version` not in the validated set | `5` |
| A user-edited managed block already exists at a target path that the install would overwrite | rc=5 + reason |

The preflight gate runs **first** — `mba init` never reaches the write
phase unless Beads is the validated version.

## 3. Observe installed state — `mba status`

```bash
mba status
```

`mba status` reads `<root>/.mba/manifest.json` (if any) and compares the
on-disk state of each managed file to what the manifest recorded.

| Exit code | Meaning |
|---|---|
| `0` | Installed, no drift, no conflicts. |
| `1` | Not installed (no `.mba/manifest.json`). |
| `2` | A user-edited managed block was found — a **conflict** the upgrade path will refuse without an explicit decision. |
| `3` | Drift other than user edits (e.g., an MBA upstream change is available, or a target file was deleted). |

The JSON output names every conflict path; use that list when you
decide whether to revert your edit (`mba upgrade` will not overwrite it
without your sign-off) or to back it up and let `mba upgrade` install
fresh.

## 3.5 First contact in a target project

MBA is not a resident background service.

| Mode | What happens |
|---|---|
| User-visible harness | The active AI session in Codex, Claude, OpenCode, ZCode, etc. becomes the Orchestrator. |
| Hidden harness | A harness may launch the Orchestrator invisibly; human questions must still go to the Bead. |
| OpenCode bootstrap | `opencode.json` selects the `mba` agent, which runs first-contact before project work. |

The human remains the authority/decision-maker. The active AI or harness
session is the thin Orchestrator.

```bash
bd version
python -m mba_runtime first-contact --cwd . --apply-setup
```

> Prefer the `python -m mba_runtime …` module form. The `mba-runtime`
> console script that `pip install` registers is an optional shortcut
> that works only when its install location is on `PATH` (Windows
> `pip install --user` writes it to
> `%APPDATA%\Python\Python3XX\Scripts`, which is not on `PATH` by
> default); the module form needs only a working `python` and the
> installed `mba_runtime` package.

| Result | What the Orchestrator does |
|---|---|
| AI-resource record ready | Reads the Bead, writes worker `prompt.md` files, launches separate Doer/Auditor sessions. |
| Missing / incomplete / inconsistent | Uses the JSON `recommended_setup_bead` to create or update the blocked `MBA setup` task, assign `Human`, add `mba`, `setup` and `human` labels, post its deterministic question comment, and stop before executable work or worker launches. |

Thin Orchestrator rule:

| Do | Do not |
|---|---|
| Classify request shape. | Pre-solve the task. |
| Prepare/reuse Beads. | Paste bulky context workers can read. |
| Write pointer-based worker prompts. | Use host todo/task trackers. |
| Launch separate workers. | Treat OpenCode `task` / `General Agent` as MBA workers. |
| Check artefacts and close/block. | Manufacture Doer/Auditor convergence in one transcript. |

Beads is the only workflow task tracker. Do not use TodoWrite or host
todo/task trackers. The Orchestrator writes pointer-based prompts and does
not pre-research, paste researched diffs, or pre-solve worker work.

Worker-launch contract:

| Item | Rule |
|---|---|
| Assignment | Full assignment in `prompt.md`. Inline launch message is only a short pointer. |
| Worker folder | Exactly `.mba-work/<bead-id>/<session-name>/`. |
| Bead ID | Must be the real `bd show <bead-id>` ID. |
| Friendly-name path | A friendly install name, test harness, ref hash, or `.mba-work/<friendly-name>/...` path is not Bead-scoped and is a refusal-grade error. |
| OpenCode worker | External hidden/background `opencode run --agent mba-worker`. |
| Windows executable | Resolve direct `opencode.exe`; avoid PowerShell shim / `.cmd` wrapper PIDs. |
| PowerShell safety | No Bash-only operators, heredocs, or rich Markdown inline. |

**Pre-launch prompt self-check.**

| Check | Requirement |
|---|---|
| Size | `≤ 4 KiB` and `≤ 60` non-blank lines. |
| No pasted diff | No `diff --git`, `+++ b/`, `@@`. |
| No precomputed result | No `ahead_by`, `behind_by`, `total_commits`, or similar. |
| Source pointer | At least one retrievable `bd show`, path, or URL. |
| Failure | Rewrite before launch; if still invalid, block the Bead for `Human`. |

The launch step is **one logical block**: write `prompt.md`, launch the worker hidden/background, capture the PID or session id, **immediately** write the launch receipt, and only then wait, tail logs, read `result.md`, read the Bead comment, or decide convergence. Writing the receipt **after** any worker output exists — `result.md`, `report.md`, the Bead comment, or a transcript tail — is a refusal-grade error: the receipt no longer proves the session existed before the output.

Before the Orchestrator accepts the first output from a launched Doer or Auditor session, it must write a small **launch receipt** at `.mba-work/<bead-id>/<session-name>/launch.md`. The receipt is the only portable proof that a separate worker session really ran. When the host exposes transcript or session identity, the receipt must also bind the launched worker identity and distinguish it from the Orchestrator identity; PID-only evidence is then insufficient. Without valid proof, the Orchestrator makes the Bead visible to the human even when a Bead comment already looks like worker output. **Write the receipt in the same launch step that captures the worker's PID or session id, before any wait, tail, read, or acceptance of the worker's output.**

| Receipt field | What goes in it |
|---|---|
| `Bead` and `Session name` | The Bead ID and the session-name directory the launch was directed at. |
| `Launch command shape` | The exact short plain pointer text the Orchestrator sent; never the rich assignment. |
| `PID or session id` | Process id, host session id, or `not_available` with a one-line reason when the host does not expose one. |
| `Worker / Orchestrator identity` | When the host exposes transcript or session identity, record both sides so output can be tied to the launched worker and rejected if it matches the Orchestrator. |
| `Start time (UTC)` | ISO-8601 timestamp at which the launch was issued. |
| `Model / AI resource` | The AI label/id plus executable launch fields from `.mba-work/.ai-resources.json`, especially `launch.model` and `launch.variant`; id is not the CLI model. |
| `Prompt path` | The session's `prompt.md` path the launch pointer targeted. |
| `Log / result paths` | Paths to the worker transcript, log, `result.md`, and the Bead comment file (paths known at launch time; later paths updated when the worker runs). |
| `Windows UX` (Windows / OpenCode) | One-line note that the launch was hidden/background (for example `Start-Process -WindowStyle Hidden`) so the user's desktop never shows empty CMD windows. |

On Windows / OpenCode, launch every Doer and Auditor worker **hidden and background** by default with `Start-Process -WindowStyle Hidden -RedirectStandardOutput ... -RedirectStandardError ... -PassThru`, or the equivalent non-interactive background launcher on the host. Visible empty CMD / conhost windows are bad UX and carry no useful progress; worker progress lives in the worker's log files, the launch receipt, the Bead comment, and any transcript the worker writes — **never in a desktop window**. A visible empty window means the launch is wrong; fix the launch step, do not accept it. Use PowerShell-native checks; do not use Bash-only shortcuts such as `||`, heredocs, `head`, `wc`, or `ls -la`. The empty CMD window is not the progress surface; logs, Bead comments, and launch receipts are.

Process stdout/stderr from the worker launch is captured as `run.log` / `run.err` in the same session folder via `-RedirectStandardOutput` / `-RedirectStandardError` (or the host equivalent). OpenCode may flush stdout late, so these are **captured log files**, not a guaranteed live stream — do not assume `run.log` is empty just because the worker has not yet finished. Tailing can provide hints, but the final proof is the captured logs plus `report.md` / `comment.md` / verdict files, not a live tail alone.

If an Auditor assignment asks for `_verdict.txt`, that file is mandatory:
missing, unreadable, or evidence-empty verdict files are not `ACCEPT`. The
Orchestrator must not infer acceptance from `report.md`, a Bead comment, or
friendly wording. A known wrong fact in user-facing deliverable content or
metadata requires `FIND` until fixed, unless the Auditor gives explicit
evidence that it is outside the accepted deliverable or truly
non-load-bearing. Calling it descriptive, minor, or a caveat is not proof.

AI Bead comments are a hard contract:

| Comment rule | Required result |
|---|---|
| Budget | Normally 4-16 non-blank structured Markdown lines. |
| Forbidden | Overlong, report-like, transcript dump, static Bead-field repeat, or pasting the prompt. |
| Auditor duty | Auditor must return `FIND` until the worker rewrites the comment. |
| Weak excuses | “Descriptive”, “minor”, or “useful context” is not enough. |

A hidden, headless, or non-interactive Orchestrator must never leave a human
question only in its hidden transcript.

| Case | Required handoff |
|---|---|
| Human decision needed | Bead comment + `blocked` + assignee `Human` + label `human`. |
| Worker cannot launch/resume | Same handoff, or one clean relaunch with a fresh receipt. |
| Worker artefacts missing after wait/resume/relaunch | Same handoff. |
| Worker comment invalid | Auditor returns `FIND`; worker rewrites before acceptance. |

**Launch directory, resume, and context reads.**

| Concern | Rule |
|---|---|
| Directory creation | `New-Item -ItemType Directory -Force -Path <session> | Out-Null`; do not pre-check/split the parent with `Split-Path -Parent` / `Test-Path`, because that can create a false `Missing parent` failure. |
| Resume after restart/disconnect | Read launch receipt + worker artefacts first; then wait/resume/relaunch/block. |
| Context recovery | Prefer targeted `bd show <bead-id>`, `bd ready`, `bd list --status=...`, `bd list --label=...`, `bd list --assignee=...`, and `bd search`; avoid broad `bd list --all` unless truly needed. |

The same rule applies after successful Doer/Auditor convergence. This is a
user-authority gate: if accepted work is waiting only on user approval such as Git commit/push,
`bd dolt push/pull`, deployment, or reusable workflow adoption, post the
decision comment, set the Bead `blocked`, assign `Human`, and add `human`.
Do not leave it silently `in_progress`; a `ready-for-user-*` label alone is
not enough.

The setup branch is a handoff, not background scheduling: see the
[startup/setup diagram](mba/startup-setup.md). After the user supplies the
record, rerun first-contact and continue only when it reports ready.

The private AI-resource record is `.mba-work/.ai-resources.json`. MBA never
invents providers, plans, models or teams.

### Live stream / log capture for MBA-launched workers

MBA captures every worker it launches by **redirecting stdout and
stderr directly to per-session files** at
`.mba-work/<bead-id>/<session-name>/run.log` and
`.mba-work/<bead-id>/<session-name>/run.err`. The capture is
**durable across viewer restart and disconnect**: an Orchestrator,
a follow-on Auditor, or the `mba stream` subcommand can read the
same files the worker wrote without sitting between the worker
and its durable sink. The capture is a **process log**, not a
guaranteed token-live stream — see the caveats below.

The default capture mode is `direct_files`; tests and
opt-out callers can pin `CAPTURE_MODE_PIPE` to keep the legacy
`Popen(PIPE)+communicate()` behaviour. The capture protocol is
declared per-dispatch (`ndjson`, `line_text`, or `opaque_bytes`)
and surfaced on `SessionOutcome.capture`.

#### Reading the capture — `mba stream`

The `mba-runtime stream` subcommand is the documented read-only
follower. It validates the path is Bead-scoped to
`.mba-work/<bead-id>/` (refusing friendly names, ref hashes, and
symlink escapes), replays complete records, and with `--follow`
polls the file for new bytes:

```powershell
# Replay a captured session's complete records (bounded summary).
python -m mba_runtime stream --bead-id <bead-id> --session-name doer-round2

# Follow a still-running worker.
python -m mba_runtime stream --bead-id <bead-id> --session-name doer-round2 --follow

# Emit the verbatim NDJSON line (diagnostic only; never a Bead comment).
python -m mba_runtime stream --bead-id <bead-id> --session-name doer-round2 --raw

# Cap how much of the file to read.
python -m mba_runtime stream --bead-id <bead-id> --session-name doer-round2 --max-bytes 8192
```

The follower buffers a partial final line so a crash that
truncates a row can never produce a fake completed event; tool
inputs / outputs and reasoning text are truncated by default
(`--max-tool-payload-chars` / `--max-field-chars`).

#### Harness-neutral scope

MBA captures **MBA-launched processes** — the work it spawns
through the dispatch adapter. The active Orchestrator may itself
run inside any user-selected harness (Codex, Claude, ZCode,
MiniMax Code, a human-driven session, etc.). MBA does **not**
claim to portably capture transcripts from those user-launched
harnesses. The orchestrator-side chat UX belongs to the host; MBA
records the milestones it produces (launch receipts, exact worker
identities, recovery decisions, comments, and required artefacts)
and the worker evidence that flows through its own dispatch.

#### Optional hidden OpenCode Orchestrator

If MBA itself launches an OpenCode Orchestrator session, the same
capture surface, launch-receipt discipline, and MBA-owned
cleanup apply. The hidden OpenCode Orchestrator is **optional**:
most users start the Orchestrator themselves in a user-visible
harness. Hidden mode is supported without forcing every
Orchestrator to be OpenCode. When the Orchestrator is hidden,
human questions still flow through the structured blocked/Human
Bead handoff; a hidden transcript is not a human interaction
surface.

#### OpenCode `--format json` caveats

When a worker is OpenCode, set `capture_protocol=ndjson` (or
let the Orchestrator declare it) and pass `--format json` to
`opencode run`. The stream is **NDJSON of step / tool /
completed-part events** — not token deltas. `--thinking` is
display-only and completion-granular, with provider-dependent
coverage; the runtime never claims token-level reasoning
visibility. Always pass `--format json` to a hidden worker
when a follow-on viewer needs the typed events; the bounded
summary line includes `step_start`, `step_finish`, `tool_use`,
`text`, `reasoning`, and `error` rows at the cadence the
provider emits them.

Minimum OpenCode resource entry:

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

| Field | Meaning |
|---|---|
| `id` | Local nickname used by teams. Not a model string. |
| `launch.tool` | Launch surface, such as `opencode`. |
| `launch.model` | Exact provider/model string the tool expects. |
| `launch.variant` | Effort/variant value, when the tool supports one. |

First-contact is not ready until every default Doer/Auditor AI has a
callable launch entry. For OpenCode, `launch.model` must look like a real
provider/model string, for example `minimax-coding-plan/MiniMax-M3`.

### MBA-owned worker cleanup

MBA cleans up **only** the workers it launched. Cleanup is
ownership-gated, fail-closed, and recorded in the launch receipt.

| Rule | What it means in plain language |
|---|---|
| Receipt before any output | The launch receipt at `.mba-work/<bead-id>/<session-name>/launch.md` is written in the same step that captures the worker PID / session id — **before** any output is read. The receipt is the only portable proof that a separate MBA-owned worker actually ran. |
| Cleanup at every terminal state | After a worker ends for any reason — successful completion, convergence, blocked handoff, timeout, failure, retry, or relaunch — the runtime cleans up any matching MBA-owned worker that is still alive. |
| Ownership gate | Non-MBA / user sessions are **never** killed. The user's own Claude / OpenCode / Codex sessions in the same repo are recorded as `owner=user` (or omitted from the MBA-owned registry) and the cleanup helper refuses to touch them. |
| Identity binding | When the host exposes transcript or session identity, the kill must tie back to the launched worker identity and be rejected if it matches the Orchestrator's own session or transcript. PID-only cleanup is not acceptable when stronger identity was available at launch. |
| Ambiguous ownership → blocked/Human | If ownership cannot be proven — no launch receipt, identity match to the Orchestrator, or identity cannot be tied back to a launch — the runtime **does not kill**. It posts a structured Bead comment, sets status `blocked`, assigns `Human`, and adds the `human` label, then stops until a user decision is recorded. A clean relaunch is allowed only with a fresh receipt written before any new output is read or accepted. |
| Permission refusal → protected | A kill that returns a permission refusal is recorded as `PROTECTED` and routed to a blocked/Human handoff. The runtime **never** escalates a permission refusal into a force-kill. |
| Progress surface | Worker progress is the worker's log files, the launch receipt, the Bead comment, and any transcript the worker writes. It is **never** an empty desktop CMD / conhost window. A visible empty window means the launch is wrong; fix the launch step, do not accept it. |

## 4. Refresh installed content — `mba upgrade`

Two upgrades are possible — they are not the same thing. Use the
correct one.

| You want to upgrade | Run |
|---|---|
| The **tool** (new MBA release from GitHub / future PyPI / a clone) | `python -m pip install -U git+https://github.com/Khubaeb/MultipleBeadedAgents.git@v0.1.0` (or `python -m pip install -e . --upgrade` from a clone). |
| The **installed content** in a target repo (MBA RULES block / skill changed upstream) | `mba upgrade --dry-run` then `mba upgrade`. |

### Preview

```bash
mba upgrade --dry-run
```

The dry-run prints one row per managed file with one of five actions:

| Action | Meaning |
|---|---|
| `up_to_date` | On-disk already matches the new upstream — nothing to do. |
| `install` | Target file is missing or lacks the managed block — installing fresh is safe. |
| `replace` | On-disk matches the recorded install, but upstream content has changed — safe to overwrite. |
| `retire` | The old manifest records a target that the new upstream no longer installs. An unchanged target is removed; an already-absent target is simply dropped from the new manifest. |
| `conflict` | On-disk differs from the recorded install, or the target path is unsafe to touch — MBA preserves it and refuses the whole upgrade without a user decision. |

### Apply

```bash
mba upgrade
```

`mba upgrade` runs the same preflight gate as `mba init`. If any
`conflict` rows exist in the plan, the command refuses with rc=7 and
no filesystem writes happen. This includes retired paths that were edited,
resolve outside the target root, use symlinks, or are otherwise unsafe to
classify. MBA never deletes those paths or removes their old manifest record;
resolve the conflict explicitly and retry. Resolving a conflict is a
user-authority decision (Charter §11): either revert your local edit, back it
up and let `mba upgrade` overwrite/remove it, or fork the workflow change for
review.

Upgrade preserves `.mba-work/.ai-resources.json` and does not reinitialize
setup. After upgrading, run
`python -m mba_runtime first-contact --cwd . --apply-setup`. If the
record is already ready, this writes nothing; answer only missing or newly
required choices.

## 5. Pick up upstream MBA updates

The canonical upstream is the **`Khubaeb/MultipleBeadedAgents`** GitHub
repository (the same URL listed in `pyproject.toml` `[project.urls]`).
MBA itself does **not** auto-fetch updates; the user runs the two-step
upgrade above. The flow is intentionally explicit because of Charter
§11 (user authority over reusable workflow changes).

| You want… | Run |
|---|---|
| Current public MBA release | `python -m pip install -U git+https://github.com/Khubaeb/MultipleBeadedAgents.git@v0.1.0` |
| A specific commit / branch / tag | `pip install -U git+https://github.com/Khubaeb/MultipleBeadedAgents.git@<ref>` |
| Then refresh a target repo's installed content | `mba upgrade --dry-run` (preview) → `mba upgrade` (apply) |

### Why two steps

The package and the **installed content** are different layers:

| Layer | Lives in | Changes when… |
|---|---|---|
| Tool | your Python environment (`pip` install) | a new MBA release ships |
| Installed content | the target repo (`AGENTS.md`, `CLAUDE.md`, `.agents/skills/mba/SKILL.md`, `opencode.json`, `.opencode/agents/mba.md`, `.opencode/agents/mba-worker.md`) | an upstream MBA release changes the MBA RULES block, the skill, or the OpenCode bootstrap |

`mba upgrade` only updates the installed content. It uses the version
recorded in `.mba/manifest.json` and the new `mba_version.__version__`
to decide whether an upgrade is available — see `mba status`
(`upgrade_available: true`) and the per-file actions in
`mba upgrade --dry-run`.

## 5.5 Remove MBA from a target project

```bash
mba remove --dry-run
mba remove
```

| Command | Result |
|---|---|
| `mba remove --dry-run` | Shows what would be removed; writes nothing. |
| `mba remove` | Removes MBA-managed blocks/files and `.mba/manifest.json`. |
| `mba remove --force` | Also removes user-edited MBA-managed content; review dry-run first. |

Preserved by design:

- `.beads/` — project task history and Dolt data;
- `.mba-work/` — local AI-resource record, prompts, transcripts, evidence and choices.

Delete or archive those folders manually only if you intentionally want to
discard that project history/configuration.

## 6. Verifying the install surface

The package ships every public doc, the LICENSE, and the MBA skill so a
fresh install has the documentation locally. The shipped set is:

```
README.md                       License                       docs/mba/README.md
docs/mba/charter.md             docs/mba/non-technical-flow.md
docs/mba/technical-flow.md      docs/mba/roadmap.md           docs/mba/implementation-status.md
docs/beads/capabilities.md      docs/beads/evaluation.md      docs/USER_GUIDE.md
mba_foundation/resources/skills/mba/SKILL.md
mba_foundation/resources/opencode/opencode.json
mba_foundation/resources/opencode/agents/mba.md
mba_foundation/resources/opencode/agents/mba-worker.md
```

The same paths appear in both `pyproject.toml` (`[tool.setuptools.data-files]`)
and `MANIFEST.in`. A repository with `LICENSE` missing, stale URLs, or
docs not listed in both metadata files fail the public-readiness check.

## 7. Workspace folder hygiene — what MBA writes where

MBA is a normal repo tool: it writes **inside the target repo it was
installed into**, in three well-known paths:

| What MBA writes | Where in the target repo |
|---|---|
| `AGENTS.md` / `CLAUDE.md` MBA RULES block, `.agents/skills/mba/SKILL.md`, `.opencode/agents/mba*.md`, `opencode.json` | the target repo root (installed content) |
| `.beads/` (Beads issue store) | the target repo root (Beads-managed) |
| `.mba-work/<bead-id>/<session>/` (prompts, launch receipts, worker artefacts, evidence, AI-resource record) | the target repo root (Git-ignored local, or Git-tracked shared) |

**Sibling folders at the install path are not MBA output.** When the
install path is `C:\CODE\<repo>`, MBA never creates `C:\CODE\<repo>-Public`,
`C:\CODE\<repo>-PublicSync`, `C:\CODE\<repo>-temp`,
`C:\CODE\.mba-test-workspaces`, or one-off experiment / sync-scratch
folders. Those are **operator or test-harness choices** — public-sync
clones, local package mirrors, downstream-test sandboxes, or
one-off experiment workspaces the user or test harness made on purpose.

**Operator / test-harness rules:**

- Sibling clones (a `-Public` / `-PublicSync` repo used to verify the
  public surface or host a Dolt sync mirror) and
  `.mba-test-workspaces/*` must be **named clearly** (the suffix tells
  a reader what they are) and **cleaned up or explicitly retained**
  when the test or sync round they belong to is over.
- A temporary sibling folder created during one MBA round
  (e.g. `_outer-test-YYYYMMDD-N`, an experiment clone, a Dolt sync
  scratch) must be named with its purpose and either deleted before
  the round closes or recorded in `report.md` so the next operator
  knows it is intentional.
- Stray sibling folders an earlier round left behind because no one
  cleaned up are **test artefacts, not installed MBA output**. A
  downstream-test Orchestrator should treat them as noise unless the
  user explicitly names one as the install target.
- MBA never writes outside the repo it was installed into. A folder
  appearing next to a target repo is either (a) the user / test
  harness put it there on purpose, or (b) an earlier round forgot to
  clean up — it is **not** a sign MBA is misbehaving.
