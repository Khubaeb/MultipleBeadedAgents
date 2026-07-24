"""Strict, file-aware marker verifier for the MBA RULES markers.

Audit finding F5 (turn-2 correction): the tool used to maintain its
own copies of the marker-line regexes and the substring patterns;
that made the tool drift-prone. The tool now imports its counting
primitives from :mod:`mba_foundation.markers` (single source of
truth) and keeps only the human-readable report formatting here.
"""

import json
import sys
from pathlib import Path


def _ensure_repo_on_path() -> None:
    """Make ``mba_foundation`` importable when the tool is invoked as
    a standalone script (``python mba_foundation/tools/verify_markers.py``).
    """

    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


_ensure_repo_on_path()

# Single source of truth — module-level import. The runtime primitives
# here are the same ``count_markers`` / ``assert_beads_markers_untouched``
# the test suite and the runtime CLI consume. F5 fix: no duplicated
# regex constants.
from mba_foundation import markers  # noqa: E402  (after sys.path tweak)


def main() -> int:
    rc = 0
    files = ("AGENTS.md", "CLAUDE.md")
    summary: dict[str, dict[str, object]] = {}

    for path_str in files:
        path = Path(path_str).resolve()
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        counts = markers.count_markers(text)
        beads_ok, beads_reason = markers.assert_beads_markers_untouched(path)

        print(f"=== {path} ===")
        print(f"  MBA RULES         : {counts.begin_count} BEGIN, {counts.end_count} END")
        print(f"  BEADS markers     : untouched={beads_ok} ({beads_reason or 'ok'})")

        ac8_ok = counts.exactly_one_pair
        if not ac8_ok or not beads_ok:
            rc = 1
            print(f"  AC #8 fails: "
                  f"begin={counts.begin_count} (need 1), "
                  f"end={counts.end_count} (need 1), "
                  f"beads_untouched={beads_ok} ({beads_reason})")
        else:
            print(f"  AC #8 ok: exactly one BEGIN MBA RULES + one END MBA RULES.")

        summary[str(path)] = {
            "mba_begin_count": counts.begin_count,
            "mba_end_count": counts.end_count,
            "beads_untouched": beads_ok,
            "beads_reason": beads_reason,
        }

    print()
    print("=== summary ===")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    sys.exit(main())
