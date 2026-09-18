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
| Current foundation | Exact releases `bd 1.0.4` and `bd 1.3.0`; other versions, including prereleases, are refused. |
| Advanced-feature reference | `v1.1.0`, conditional only. |

### Version-gate hardening in 0.1.3

The 0.1.3 candidate retains exact Beads **1.0.4** and **1.3.0** support from
the completed 0.1.2 release. The runtime wrapper now validates the selected
executable's version for every command except a small set of known queries:
`version`, `show`, `list`, `ready`, `blocked`, `search`, `dep list`, and
`dep cycles`. Bare invocation and standalone `--help`, `-h`, and `--version`
also remain available without a supported-version check.

Leading `--actor VALUE` and `--actor=VALUE` retain query recognition; original
arguments are forwarded unchanged. Other leading global options and unlisted
queries (including comments listing) conservatively require a supported
version. Thus, on an unsupported version, use the listed query forms for
recovery. Unknown and future commands can no longer bypass validation merely
because they are absent from a write-verb list. This is a bounded classifier,
not a general CLI parser or a guarantee against future mutating options added
to an existing query verb.

The label-write follow-up adds regression coverage only. On supported versions,
the existing writer already performs one version probe and one update per
logical write, including multiple labels. No production optimization or speedup is claimed. Release
preparation does not itself publish 0.1.3 or authorize downstream adoption.

### Compatibility in MBA 0.1.2

The portable core is behavior-tested against exact Beads releases **1.0.4**
and **1.3.0** on macOS with embedded stores. The shared allowlist feeds both
Foundation and Runtime; prerelease/build-suffixed and other versions are
refused. Compatibility evidence does not authorize downstream adoption.
The release notes for the target are [Beads v1.3.0](https://github.com/gastownhall/beads/releases/tag/v1.3.0).

`tests/test_beads_compatibility.py` exercises real CLI JSON, all primitive
field transports, explicit worker attribution/assignment, Unicode Markdown
comments, labels, typed dependencies, ready/block/unblock, cycle rejection,
and lifecycle status changes. It also migrates a copy of a synthetic native
1.0.4 store in UTC and restores a separate complete old-native copy. It never
uses an issue JSONL export as the migration or rollback input.

Native comparisons preserve comment multiplicity without assuming same-second
ordering. Migration may rekey comment/event IDs, add dependency IDs, rename
`depends_on_id` to `depends_on_issue_id`, add issue `is_blocked`/`row_lock`
columns, and refresh issue update timestamps. Full rollback comparisons retain
original IDs and timestamps. JSON exports alone do not cover auxiliary tables;
the native-table checks use an explicitly supplied Dolt binary.

The package upgrade fixture starts from public MBA 0.1.1, preserves an outer
Codex/Claude app override and a synthetic private resource record, and checks
managed manifest drift. Production migration, remote synchronization, actual
consumer adoption, Windows execution, and independent review remain separate
acceptance gates. Earlier Windows CI coverage is not new 1.3.0 evidence.

To run the opt-in contracts, set `MBA_TEST_BD_104` and `MBA_TEST_BD_130` to
absolute exact-version binaries, `MBA_TEST_DOLT` to the inspection binary,
`MBA_TEST_PUBLIC_BASELINE` to a read-only public 0.1.1 source checkout, and
`MBA_TEST_WORKSPACE_ROOT` to a disposable directory outside every Beads
ancestor. Run `python -m pytest tests/test_beads_compatibility.py`. Missing
fixture configuration is a skip, never evidence of compatibility. Use an
isolated test environment; the root fixture clears inherited Beads/Dolt/Git
configuration and uses a temporary home, UTC, no telemetry, and disabled
backup/export settings. No production board or private resource record is a
test input. All source/package publication remains subject to review.
