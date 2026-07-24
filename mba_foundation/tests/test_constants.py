"""Constants — verbatim marker pair and validated version set."""

from __future__ import annotations

from mba_foundation import constants


def test_validated_versions_pin_bd_104() -> None:
    assert "1.0.4" in constants.VALIDATED_BD_VERSIONS
    assert len(constants.VALIDATED_BD_VERSIONS) == 1


def test_marker_pair_is_verbatim_charter_text() -> None:
    assert constants.MBA_RULES_BEGIN_MARKER == "<!-- BEGIN MBA RULES -->"
    assert constants.MBA_RULES_END_MARKER == "<!-- END MBA RULES -->"
    assert constants.MBA_RULES_BEGIN_MARKER.endswith("-->")
    assert constants.MBA_RULES_END_MARKER.endswith("-->")


def test_modes_are_exactly_two() -> None:
    assert constants.VALID_MBA_MODES == frozenset({"local", "shared"})


def test_windows_threshold_is_default_max_path() -> None:
    # The MBA spec defers the precise validated threshold to the platform
    # default. CONSTANTS pins the Windows default; the conditional row
    # in docs/beads/capabilities.md is the place to widen the value.
    assert constants.WINDOWS_MAX_PATH == 260
