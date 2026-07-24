# MBA implementation status

Snapshot for release `0.1.0`. Requirements live in [`charter.md`](charter.md).

## Built

| Area | Status | Main code |
|---|---|---|
| Beads detection and version gate | Built | `mba_foundation.detect`, `preflight` |
| Target-repo install/status/upgrade/remove | Built | `mba_foundation.cli`, `manifest` |
| `AGENTS.md` / `CLAUDE.md` marker blocks | Built | `mba_foundation.markers` |
| OpenCode Orchestrator + worker install files | Built | `mba_foundation/resources/opencode/` |
| Workspace mode and AI-resource privacy | Built | `mba_foundation.workspace` |
| Public/private product boundary | Built | `mba_foundation.product_boundary` |
| Atomic vs staged classification | Built | `mba_foundation.orchestrator` |
| Bead read/write primitives | Built | `mba_primitives.*` |
| Assignment contract and records layout | Built | `mba_primitives.assignment_contract`, `records_layout` |
| AI-resource record and route planning | Built | `mba_runtime.ai_resources`, `pattern_router` |
| First-contact setup handoff | Built | `mba_runtime.cli first-contact` |
| Doer/Auditor convergence loop | Built | `mba_runtime.convergence`, `lifecycle` |
| Evidence-required Auditor `ACCEPT` | Built | `mba_runtime.convergence`, `lifecycle` |
| Multi-Auditor verdict combination | Built | `mba_runtime.lifecycle` |
| User-authority gate | Built | `mba_runtime.user_authority` |
| Dependency-graph verification | Built | `mba_runtime.graph` |
| External worker process adapter | Built | `mba_runtime.external_dispatch` |
| Worker `run.log` / `run.err` capture | Built | `mba_runtime.external_dispatch`, `stream_capture` |
| Read-only stream follower | Built | `mba_runtime.cli stream` |
| MBA-owned worker cleanup guard | Built | `mba_runtime.external_dispatch` |
| Ordered suitable-resource fallback library | Built | `mba_runtime.session_recovery`, `cli resources` |
| Resume-first recovery planner | Built | `mba_runtime.session_recovery` |
| User-session protection | Built | `mba_runtime.session_recovery`, cleanup guard |

## Partly built / not automatic yet

| Area | Current state | Next needed |
|---|---|---|
| Automatic in-loop resource fallback | Library and CLI exist. | Wire runner unavailable/paused signals into `drive-bead`. |
| Session continuity across provider stalls | Deterministic planner exists. | Persist/resume live host sessions in the dispatch loop. |
| Audit-trail rendering | Runtime-only audit-trail model exists. | Generate human-readable `sessions.md` / audit view. |
| Hidden Orchestrator transcript capture | Supported when the harness owns it; MBA records MBA milestones and worker artefacts. | Optional host-specific capture helpers if useful. |
| Advanced Beads features | Evaluated as Conditional. | Adopt only with a simpler validated use case and user approval. |
| PyPI publishing | Not part of `0.1.0`. | Separate release decision and credentials. |

## Test slices used for release confidence

| Slice | Purpose |
|---|---|
| `mba_foundation/tests` | Install/status/upgrade/remove, markers, public readiness, product boundary. |
| `mba_primitives/tests` | Safe Bead IO, assignment contract, records layout. |
| `mba_runtime/tests` | Convergence, dispatch, streams, recovery, authority, graph, workspace safety. |
| Public mirror checks | No private files; no Beads Dolt ref; public-safe source subset only. |
| Downstream install check | Install from public GitHub tag; no editable dev link. |

## Release boundary

| Item | Status |
|---|---|
| Current version | `0.1.0` |
| Public install source | GitHub tag `v0.1.0` |
| PyPI | Not published. |
| Dev Beads data | Private dev remote only. |
| Public repo history | Rebuilt as a single public-safe commit for `v0.1.0`. |
