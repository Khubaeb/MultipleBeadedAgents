# Beads capability record

> Single source of truth for which native Beads features MBA adopts, conditions,
> or excludes. The MBA charter points here; this file carries the full
> classification.

## Core

Every MBA project uses these.

| Capability | MBA use |
|---|---|
| Direct `bd` CLI | Default Beads interface for reads/writes. |
| `task` | Atomic executable work. |
| `epic` | A larger goal containing related tasks. |
| Parent/child hierarchy | Task-with-stages; each stage can have its own Doer/Auditor loop. |
| Built-in statuses | `open`, `in_progress`, `blocked`, `closed` as the workflow state. |
| Priorities `P0`-`P4` | Native urgency signal. |
| Assignee | Human, Orchestrator, or organizational role name. |
| Labels | Lightweight filters such as `mba`, `setup`, `human`, stage, domain. |
| Comments | Structured human-facing progress, findings, decisions and handoffs. |
| `blocks` dependencies | Sequencing, parallel branches and joins. |
| `discovered-from` dependencies | Scout/follow-up Beads found during active work. |
| `bd ready` / `bd blocked` | Native ready-work and blocked-work views. |
| `bd dep list` / `bd dep cycles` | Graph verification before closure. |
| Embedded Dolt | Local durable Beads database. |
| `bd dolt push/pull` | Private/dev Beads sync, only with user authority. |
| `bd version` | Preflight gate before any live write. |

## Core rules

| Rule | Required behavior |
|---|---|
| Beads owns task state | MBA does not create a second task tracker. |
| AI actors are explicit | Use `bd --actor "<Role>" ...`; never rely on OS/Git/default attribution. |
| AI role assignment is explicit | Avoid `bd update --claim` for AI work; set the intended assignee/actor directly. |
| Worker comments are allowed | Doer/Auditor may post structured comments on their assigned Bead. |
| Worker handoff is narrow | A blocked worker may comment, add `human`, set `blocked`, and assign `Human`. |
| Orchestrator owns graph/closure | Hierarchy, dependencies, closure and Dolt sync stay Orchestrator-owned unless explicitly delegated. |
| `bd --readonly` is situational | Use it only when isolation is required, not as a blanket worker rule. |
| Parent closure is manual | MBA closes after verified acceptance; it does not auto-close parents by default. |

## Comment format

| Field | Rule |
|---|---|
| Shape | Structured Markdown; lists/tables/checklists preferred. |
| Size | Normally 4-16 non-blank lines. |
| Content | Result, material changes/findings, verification, next state. |
| Avoid | Static Bead fields, pasted prompts, transcripts, long reports. |
| Details | Put bulky evidence in `.mba-work/<bead-id>/<session>/` only when useful. |
| Attribution | User attribution only for real user input/decisions/actions. |

## Conditional

Use only with a documented use case, version validation and user approval where
the charter requires it.

| Capability | Possible use | Condition |
|---|---|---|
| Formulas | Repeatable workflow templates. | The workflow is stable enough to template. |
| Ephemeral protos | Temporary generated workflow shape. | The Beads version supports the exact behavior. |
| Molecules | Structured reusable multi-step work. | Simpler task/parent/dependency graph is insufficient. |
| Wisps | Orchestrator-runtime scratch. | Scratch does not need to sync cross-machine. |
| Bonds | Wire advanced workflow steps. | Step IDs and supported CLI semantics are verified. |
| Gates | Timers, human/CI/PR-style external waits. | The specific gate works in the running version/host. |
| `bd promote` | Turn temporary work into durable issues. | Promotion semantics are validated for the active version. |
| Distill/squash/burn | Curate long-running advanced workflow state. | Audited summary/retention policy exists. |
| Server mode / Windows sync workaround | Avoid path or embedded-Dolt sync limits. | User chooses it for the repo. |

## Excluded from the portable core

| Mechanism | Why excluded |
|---|---|
| Custom MBA issue types/statuses/priorities | Native Beads fields are enough. |
| `bd cook --persist` | Legacy path; not needed for the foundation. |
| Formula `bond_points` as CLI targets | Authoring metadata, not a default runtime contract. |
| Unavailable gate types | Cannot be portable if the running version/host lacks them. |
| Custom wisp cross-machine sync | Wisps are not the durable shared record. |
| Official Beads plugin as a required dependency | Host-specific; direct CLI is simpler and broader. |
| Beads MCP as a required dependency | Useful only when CLI access is unavailable or explicitly chosen. |
| JSONL import/export as sync | JSONL is a snapshot/export, not the live Beads sync protocol. |
| Forking Beads-generated skill/hooks | MBA uses generated Beads surfaces unchanged. |

## Setup integration

| Mechanism | Status |
|---|---|
| Detect Beads at target root | Core |
| Ask before installing/initialising Beads | Core |
| MBA RULES marker block | Core, inserted/replaced in target instruction files. |
| Beads-generated setup recipe | Conditional; use when the host has one. |
| Beads-generated skill | Conditional; use unchanged. |
| Beads-generated Git hooks | Conditional; only when repo policy authorizes them. |
| Official plugin / MCP | Optional escape hatch, not portable core. |

## Version policy

| Rule | Meaning |
|---|---|
| Record before write | Run `bd version` before live Beads writes. |
| Validate before use | Only use behavior proven for the running version. |
| No silent upgrade | User approval plus revalidation is required. |
| Current foundation | `bd 1.0.4`. |
| Advanced-feature reference | `v1.1.0`, conditional only. |
