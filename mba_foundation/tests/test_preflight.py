"""`bd version` preflight + capability-record conformance check (AC #1, AC #2)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mba_foundation import preflight


def test_extract_bd_version_pulls_first_semver_token() -> None:
    raw = "bd version 1.0.4 (ce242a879: HEAD@ce242a879678)"
    assert preflight.extract_bd_version(raw) == "1.0.4"


def test_extract_bd_version_returns_none_on_empty() -> None:
    assert preflight.extract_bd_version("") is None
    assert preflight.extract_bd_version("   ") is None


def test_capability_check_passes_for_validated_version() -> None:
    ok, reason = preflight.capability_conformance_check("1.0.4")
    assert ok is True
    assert reason == ""


def test_capability_check_refuses_unvalidated_version() -> None:
    ok, reason = preflight.capability_conformance_check("1.5.0")
    assert ok is False
    assert "not in the validated set" in reason
    assert "1.0.4" in reason


def test_capability_check_refuses_missing_version() -> None:
    ok, reason = preflight.capability_conformance_check(None)
    assert ok is False
    assert "preflight did not run" in reason


def test_preflight_writes_orchestrator_working_md(tmp_path: Path) -> None:
    """Synthetic happy path — `bd` isn't invoked; the helper runs unit-only."""

    class FakeBins:
        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

    class FakeBin:
        def __init__(self, fake: FakeBins, version_output: str) -> None:
            self.fake = fake
            self.version_output = version_output

        def run(self, *args: str) -> str:
            self.fake.calls.append(args[0] if args else ())
            return self.version_output

    fake = FakeBins()

    # Replace `subprocess.run` for this test: the `run_bd_version` helper
    # routes through `subprocess.run` directly, so we patch the import
    # path used by `preflight`.
    import mba_foundation.preflight as preflight_mod

    original_run = preflight_mod.subprocess.run
    calls: list[tuple] = []

    def spy(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((cmd, *args))
        class _R:
            returncode = 0
            stdout = "bd version 1.0.4 (ce242a879: HEAD@ce242a879678)\n"
            stderr = ""
        return _R()

    preflight_mod.subprocess.run = spy  # type: ignore[assignment]
    try:
        orch_dir = tmp_path / "orch"
        result = preflight.preflight(
            bead_id="example-003.1",
            orchestrator_dir=orch_dir,
            bd_binary="bd",
            cwd=tmp_path,
        )
    finally:
        preflight_mod.subprocess.run = original_run  # type: ignore[assignment]

    assert result.ok is True
    assert result.bd_version == "1.0.4"

    working = orch_dir / "working.md"
    assert working.exists()
    body = working.read_text(encoding="utf-8")
    assert "bd_version" in body
    assert "1.0.4" in body

    payload = json.loads(body.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert payload["bead_id"] == "example-003.1"
    assert payload["bd_version"] == "1.0.4"
    assert payload["matches_record"] is True
    assert "1.0.4" in payload["validated_versions"]


def test_preflight_refuses_when_binary_absent(tmp_path: Path) -> None:
    orch_dir = tmp_path / "orch"
    result = preflight.preflight(
        bead_id="example-003.1",
        orchestrator_dir=orch_dir,
        bd_binary="__no_such_bd_binary__",
        cwd=tmp_path,
    )
    assert result.ok is False
    assert result.bd_version is None
    assert "binary is unavailable" in result.reason


def test_preflight_gates_subsequent_writes(tmp_path: Path) -> None:
    """When `bd` is unvalidated, refusal must persist (the working.md
    evidence carries the gate)."""

    orch_dir = tmp_path / "orch"
    result = preflight.preflight(
        bead_id="example-003.1",
        orchestrator_dir=orch_dir,
        bd_binary="__no_such_binary__",
        cwd=tmp_path,
    )
    assert result.ok is False
    assert not (orch_dir / "working.md").exists() or "bd_version" in (
        orch_dir / "working.md"
    ).read_text(encoding="utf-8")
