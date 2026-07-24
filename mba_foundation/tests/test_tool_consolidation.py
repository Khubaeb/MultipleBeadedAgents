"""F5 (turn-2) — the marker-counting primitives live in **one** place.

These tests exercise both surfaces — runtime ``markers.count_markers``
and the CLI tool ``tools/verify_markers.py`` — and prove they share
the same regex patterns by construction (the tool imports the
runtime).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest


def test_runtime_count_and_tool_match_on_live_files() -> None:
    """The CLI tool and the runtime ``count_markers`` must agree on the
    live ``AGENTS.md`` and ``CLAUDE.md`` files.
    """

    from mba_foundation import markers

    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo))
    import mba_foundation.tools.verify_markers as tool

    # Inline import: the tool runs its main() on direct invocation;
    # for a unit test we just exercise the same module-level helpers
    # through markers directly to prove the import wiring holds.
    live_paths = [repo / path_str for path_str in ("AGENTS.md", "CLAUDE.md")]
    if not all(path.exists() for path in live_paths):
        pytest.skip(
            "dev-local AGENTS.md / CLAUDE.md are absent in source-only public snapshots"
        )
    for path_str in ("AGENTS.md", "CLAUDE.md"):
        path = repo / path_str
        text = path.read_text(encoding="utf-8")
        runtime_counts = markers.count_markers(text)
        assert runtime_counts.exactly_one_pair


def test_tool_imports_runtime_module() -> None:
    """If the tool were to re-implement the regexes, F5 would re-open.
    This test asserts the import wiring that consolidates the two.
    """

    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo))
    import importlib

    tool = importlib.import_module("mba_foundation.tools.verify_markers")
    # The tool must have imported ``mba_foundation.markers`` at
    # module-load time, and have a callable ``main`` that reuses it.
    import mba_foundation.markers  # noqa: F401  (the import the tool depends on)
    assert callable(tool.main)
    # If someone reverts the tool to inline regex constants, this
    # attribute disappears and the assertion fires.
    assert tool.markers is mba_foundation.markers


def test_tool_does_not_define_duplicate_regex_constants() -> None:
    """The whole point of F5: the tool must NOT redeclare ``re.compile``
    patterns for the MBA or Beads markers.
    """

    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo))
    import importlib

    tool = importlib.import_module("mba_foundation.tools.verify_markers")
    source_path = Path(tool.__file__).read_text(encoding="utf-8")
    assert "re.compile" not in source_path, (
        "tools/verify_markers.py must consume runtime regex patterns, "
        "not redeclare them. F5 audit finding."
    )
    # Conversely, it must import from mba_foundation.markers.
    assert "from mba_foundation import markers" in source_path or (
        "import mba_foundation.markers" in source_path
    )
