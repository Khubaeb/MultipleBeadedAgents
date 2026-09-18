"""Failure-path coverage independent of optional installed Beads binaries."""
import json
import subprocess

import pytest

from mba_foundation import constants, preflight
from mba_primitives import bead_write
from mba_runtime import bd_client, lifecycle


@pytest.mark.parametrize("version", ["1.0.4", "1.3.0", "1.3.0-rc.1", "1.3.0+local", "1.4.0", "9.9.9"])
def test_exact_version_policy_is_shared(version):
    from mba_runtime.constants import VALIDATED_BD_VERSIONS
    assert VALIDATED_BD_VERSIONS is constants.VALIDATED_BD_VERSIONS
    parsed = preflight.extract_bd_version(f"bd version {version} (fixture)")
    assert parsed == version
    assert preflight.capability_conformance_check(parsed)[0] == (version in {"1.0.4", "1.3.0"})


@pytest.mark.parametrize("args", [
    ["--actor", "Orchestrator", "create", "--title", "setup"],
    ["update", "fixture-x", "--status", "blocked", "--actor", "Orchestrator"],
    ["close", "fixture-x", "--actor", "Orchestrator"],
    ["comments", "add", "fixture-x", "-f", "comment.md", "--actor", "Doer"],
    ["dep", "add", "fixture-x", "fixture-y", "--actor", "Orchestrator"],
])
def test_runtime_direct_writes_refuse_unsupported_before_mutation(monkeypatch, tmp_path, args):
    calls = []
    def invoke(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "bd version 9.9.9", "")
    monkeypatch.setattr(bd_client.subprocess, "run", invoke)
    result = bd_client.call("bd", args=args, cwd=tmp_path)
    assert result.returncode != 0
    assert calls == [["bd", "version"]]


def test_primitive_refuses_unsupported_before_mutation(monkeypatch, tmp_path):
    calls = []
    def invoke(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "bd version 1.3.0-rc.1", "")
    monkeypatch.setattr(bead_write.subprocess, "run", invoke)
    result = bead_write.safe_write_field("fixture-x", "notes", "text", cwd=tmp_path)
    assert result.returncode != 0
    assert calls == [["bd", "version"]]


def test_selected_binary_capture_and_close_actor(monkeypatch, tmp_path):
    calls = []
    def invoke(binary, *, args, **kwargs):
        calls.append((binary, args))
        return subprocess.CompletedProcess([binary, *args], 0, "bd version 1.3.0", "")
    monkeypatch.setattr(bd_client, "call", invoke)
    assert lifecycle._bd_version_gate(tmp_path, bead_id="fixture-x", bd_binary="/chosen/bd") == "1.3.0"
    capture = json.loads((tmp_path / ".mba-work/fixture-x/orchestrator/bd-version.log").read_text())
    assert capture["stdout"] == "bd version 1.3.0"
    assert calls == [("/chosen/bd", ["version"])]
    lifecycle._close_bead(bead_id="fixture-x", cwd=tmp_path, bd_binary="/chosen/bd", reason="verified")
    assert calls[-1][1][-2:] == ["--actor", "Orchestrator"]


def test_primitive_honors_actor_environment_and_unicode(monkeypatch, tmp_path):
    calls = []
    def invoke(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "bd version 1.3.0", "")
    monkeypatch.setattr(bead_write.subprocess, "run", invoke)
    env = {"HOME": str(tmp_path)}
    result = bead_write.safe_write_field("fixture-x", "notes", "日本語\n✓", actor="Codex Doer", env=env, cwd=tmp_path)
    assert result.returncode == 0
    assert calls[-1][0][-2:] == ["--actor", "Codex Doer"]
    assert calls[-1][1]["env"] == env
    assert calls[-1][1]["encoding"] == "utf-8"
