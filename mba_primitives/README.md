# `mba_primitives`

Low-level helpers used by MBA's Orchestrator/runtime.

## What this package owns

| Area | Module | Purpose |
|---|---|---|
| Safe Bead writes | `bead_write.py` | Multiline-safe `bd update` calls without shell quoting. |
| Bead read-back verification | `bead_read.py` | Read a Bead and compare fields byte-for-byte. |
| Worker assignment contract | `assignment_contract.py` | Render the required `prompt.md` shape for Doer/Auditor sessions. |
| Records layout | `records_layout.py` | Create `.mba-work/<bead-id>/{orchestrator,<session>,final}/`. |
| CLI | `cli.py`, `__main__.py` | Reproducible command-line access. |

## Commands

```bash
python -m mba_primitives --version
python -m mba_primitives safe-write --bead-id <id> --field description --content-file prompt.md
python -m mba_primitives read-back --bead-id <id>
python -m mba_primitives assert-field-matches --bead-id <id> --field description --content-file prompt.md
python -m mba_primitives assignment-contract --role Engineer --bead <id> --stage Build --session-purpose main --task "..." --read "..." --produce "..." --acceptance "..." --authority-and-limits "..."
python -m mba_primitives ensure-layout --bead-id <id> --session doer --session auditor
```

## Boundaries

| Primitive | Does | Does not |
|---|---|---|
| Bead writer | Writes one requested field safely. | Change hierarchy, status, assignment, closure, Git, or Dolt. |
| Bead reader | Verifies Beads state. | Assume the write worked without read-back. |
| Assignment contract | Produces compact worker prompts. | Inline bulky context workers can read themselves. |
| Records layout | Creates scoped `.mba-work/<bead-id>/...` folders. | Accept non-Bead-scoped paths, traversal, or absolute session names. |

## Tests

```bash
python -m pytest mba_primitives/tests
```
