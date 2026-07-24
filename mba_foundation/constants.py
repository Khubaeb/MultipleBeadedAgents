"""Foundation-wide constants.

Derived from the converged capability record (``docs/beads/capabilities.md``
Version policy and Setup integration) and the MBA charter (``docs/mba/charter.md``
§5 and §10).
"""

from __future__ import annotations

import re
import sys

# Validated by `bd 1.0.4` foundation research; widening support to a later
# version requires the revalidation workflow in
# ``docs/beads/capabilities.md`` Version policy.
VALIDATED_BD_VERSIONS: frozenset[str] = frozenset({"1.0.4"})

# Verbatim MBA-owned marker pair installed into ``AGENTS.md`` and
# ``CLAUDE.md`` from ``mba_foundation.markers.MBA_RULES_BLOCK``.
MBA_RULES_BEGIN_MARKER: str = "<!-- BEGIN MBA RULES -->"
MBA_RULES_END_MARKER: str = "<!-- END MBA RULES -->"

# Standard Windows MAX_PATH. The MBA spec defers the precise validated
# threshold to the platform default until evidence on a non-default
# configuration demands otherwise; the Charter (§5 / docs/beads/capabilities.md
# Conditional) treats Windows sync workarounds (short-path, long-path,
# non-git remote) as Conditional exactly so this value can be tuned
# without code change.
WINDOWS_MAX_PATH: int = 260

# Boolean: are we running on the Windows host where the MAX_PATH check
# is meaningful?
IS_WINDOWS: bool = sys.platform.startswith("win")

# `.mba-work/` mode toggle. The default honours the existing
# `.gitignore:8` ``.mba-work/`` line. The chosen mode persists across
# runs in ``.mba-work/.mba-mode``.
MBA_MODE_LOCAL: str = "local"      # `.mba-work/` is Git-ignored (default).
MBA_MODE_SHARED: str = "shared"    # `.mba-work/` is Git-tracked.

VALID_MBA_MODES: frozenset[str] = frozenset({MBA_MODE_LOCAL, MBA_MODE_SHARED})

# Path of the AI-resource record. Always private; the runtime privacy
# guarantee (Constraint 20 / Foundation F4) holds in BOTH modes.
AI_RESOURCE_RECORD: str = ".mba-work/.ai-resources.json"

# Marker used in `bd version` output. The Beads 1.0.4 binary prints
# ``bd version 1.0.4 (ce242a879: HEAD@ce242a879678)``; we extract the
# leading semver-like token.
BD_VERSION_PATTERN: re.Pattern[str] = re.compile(r"\b(\d+\.\d+\.\d+)\b")
