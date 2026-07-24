# `mba_foundation`

Foundation utilities for installing and maintaining MBA in a target repository.

## What this package owns

| Area | Module |
|---|---|
| Beads version gate | `preflight.py` |
| Beads workspace detection + nested-init guard | `detect.py` |
| Windows Dolt sync path guard | `sync_guard.py` |
| `.mba-work` mode + AI-resource privacy | `workspace.py` |
| Atomic vs staged request classification | `orchestrator.py` |
| MBA marker blocks in `AGENTS.md` / `CLAUDE.md` | `markers.py` |
| Install/upgrade/remove manifest | `manifest.py` |
| Product/public/private boundary | `product_boundary.py` |
| CLI entrypoint | `cli.py`, `__main__.py` |

## Main commands

```bash
python -m mba_foundation --version
python -m mba_foundation init --root .
python -m mba_foundation status --root .
python -m mba_foundation upgrade --root . --dry-run
python -m mba_foundation remove --root . --dry-run
```

`mba` is the public shortcut for this package:

```bash
mba init
mba status
mba upgrade --dry-run
mba remove --dry-run
```

## Install surface

| Target path | Managed how |
|---|---|
| `AGENTS.md` | MBA marker block; preserves surrounding project text. |
| `CLAUDE.md` | Same marker block. |
| `.agents/skills/mba/SKILL.md` | Verbatim copy. |
| `opencode.json` | Verbatim copy. |
| `.opencode/agents/mba.md` | OpenCode Orchestrator agent. |
| `.opencode/agents/mba-worker.md` | OpenCode worker agent. |
| `docs/...` | Local docs referenced by installed instructions. |
| `.mba/manifest.json` | Version + per-file checksums. |

## Safety rules

| Rule | Behavior |
|---|---|
| Beads version mismatch | Refuse before writing. |
| Existing user-edited managed block | Refuse; require user decision. |
| Upgrade | Replace only unchanged managed content; conflict on edits. |
| Remove | Remove MBA-managed content; preserve `.beads` and `.mba-work`. |
| Product boundary | Exclude `.beads`, `.mba-work`, `.agents`, `.claude`, build artefacts, caches, and VCS data from public package/release output. |

## Tests

```bash
python -m pytest mba_foundation/tests
```
