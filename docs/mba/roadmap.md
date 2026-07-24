# MBA roadmap

Planned work after `0.1.0`. Priority remains user-owned.

## Near-term

| Priority candidate | Why it matters |
|---|---|
| Wire fallback/recovery into `drive-bead` | Lets the runtime automatically resume, relaunch, or switch to a suitable configured AI when a worker hits limits. |
| Persist MBA-owned session registry | Makes restart/disconnect recovery more reliable across hosts. |
| Render session/audit trail | Gives humans a compact view of launched sessions, model choices, logs, and convergence without reading raw files. |
| Prompt templates for retry/recheck | Keeps Orchestrator prompts small and consistent. |
| Additional downstream self-runs | Uses real repos to find usability gaps before bigger releases. |

## Product decisions still owned by the user

| Decision | Default until decided |
|---|---|
| Pre-authorized fallback vs per-swap approval | No silent downgrade; record every substitution. |
| Auto-recheck on suspiciously fast Auditor `ACCEPT` | Do not auto-expand unless configured. |
| Retry cap behavior | Stay open/blocked rather than auto-close. |
| Shared vs local `.mba-work` mode | Local/Git-ignored default. |
| Advanced Beads features | Conditional, not default. |
| PyPI publishing | Not enabled. |

## Conditional Beads adoption

| Feature family | Current stance |
|---|---|
| Formulas / molecules | Use only if they simplify a repeatable workflow better than direct Beads. |
| Wisps | Consider for ephemeral orchestrator scratch only when version semantics are validated. |
| Bonds / gates | Consider when native readiness/gating is simpler than MBA-owned logic. |
| Distillation / squash / promote | Consider only with explicit audit and user authority. |
| Official plugin / MCP | Optional convenience, not portable-core dependency. |

## Cross-project rule

| Project | Owns |
|---|---|
| MBA dev repo | MBA source, private dev Beads, public release mirror. |
| Consumer repo | Its own Beads, MBA setup, AI-resource config, local work. |

MBA development must not close or rewrite a consumer repo's Beads unless the
user explicitly scopes that repo and action.
