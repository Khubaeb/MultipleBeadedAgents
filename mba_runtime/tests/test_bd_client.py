"""example-017.1 hardening tests for ``mba_runtime.bd_client``.

These tests pin the env-var executable-swap guarantee: an ambient
environment variable must **not** be sufficient to redirect the
runtime's ``bd`` invocation. The pre-packaging threat model assumed
that anyone who could set ``MBA_RUNTIME_BD_STUB`` could execute an
attacker-chosen Python script in place of the configured ``bd``.

The hardening moves the seam from ambient-env to an explicit
``set_subprocess_invoker_override()`` opt-in; an attacker can no
longer activate the seam by writing the env var alone.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from mba_runtime import bd_client


@pytest.fixture(autouse=True)
def _isolate_invoker_override():
    """Each test starts and ends with no in-process override.

    Cleared both before and after so a leaked override from a
    sibling test cannot bleed into this one. Without this guard a
    test that installs the override and forgets to clear it would
    silently activate the seam in every subsequent test.
    """

    bd_client.set_subprocess_invoker_override(None)
    yield
    bd_client.set_subprocess_invoker_override(None)


def _write_stub(path: Path) -> Path:
    """Write a tiny Python "fake bd" that exits 0 and prints a sentinel."""

    path.write_text(
        "import sys\n"
        "sys.stdout.write('STUB_INVOKED\\n')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    return path


def _write_marker(path: Path) -> Path:
    """Write a stub script that records its invocation to ``path``.

    The marker is the test's load-bearing evidence: when the
    runtime is tricked into executing the stub, the marker file
    comes into existence; the absence of the marker proves the
    runtime stayed on the production ``bd_binary`` path.
    """

    path.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(path)!r}).with_name("
        f"pathlib.Path({str(path)!r}).stem + '.INVOKED').write_text("
        "'1', encoding='utf-8')\n"
        "sys.stdout.write('STUB_INVOKED\\n')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    return path


def test_ambient_env_var_alone_does_not_activate_stub(
    tmp_path: Path, monkeypatch
) -> None:
    """Setting only the env var must NOT redirect ``bd_client.call``.

    Threat: an attacker (or a careless wrapper script) writes
    ``MBA_RUNTIME_BD_STUB=<evil-script>`` to the environment
    before the runtime starts. Round 1 closed only the narrower
    case (path env var but no ARMED marker); round 2 closes the
    full ambient-env attack — neither env var nor both env vars
    activate the seam. The function does not consult any
    environment variable in the production code path.
    """

    stub = _write_marker(tmp_path / "evil_stub.py")
    # Threat model: env var is set, the ARMED marker is NOT.
    monkeypatch.setenv("MBA_RUNTIME_BD_STUB", str(stub))
    monkeypatch.delenv("MBA_RUNTIME_BD_STUB_ARMED", raising=False)

    # The seam is not active. There is no env-var bootstrap.
    assert bd_client.get_subprocess_invoker_override() is None, (
        "an ambient env var activated the stub; round-2 "
        "hardening is broken"
    )

    bd_binary_path = tmp_path / "fake_bd_binary.py"
    bd_binary_path.write_text(
        "import sys\n"
        "sys.stdout.write('BINARY_INVOKED\\n')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    proc = bd_client.call(sys.executable, args=[str(bd_binary_path)])
    assert "STUB_INVOKED" not in proc.stdout, (
        "the ambient env var activated the stub; round-2 "
        "hardening is broken"
    )


def test_both_env_vars_set_does_not_activate_stub(
    tmp_path: Path, monkeypatch
) -> None:
    """Round-2 boundary: BOTH env vars set, no explicit override.

    Threat (the round-1 failure the Auditor demonstrated):
    a fresh process has ``MBA_RUNTIME_BD_STUB=<evil.py>`` **and**
    ``MBA_RUNTIME_BD_STUB_ARMED=1`` set. Round 1 consulted both
    and used the path; the ARMED marker added no security
    boundary because an attacker who can write the path can
    also write the marker. Round 2 disables the env-var bootstrap
    entirely — the production code path consults neither var.
    A fresh process with both vars set and no in-process override
    must fall through to ``bd_binary`` and never execute
    ``<evil.py>``.
    """

    stub = _write_marker(tmp_path / "evil_stub.py")
    monkeypatch.setenv("MBA_RUNTIME_BD_STUB", str(stub))
    monkeypatch.setenv("MBA_RUNTIME_BD_STUB_ARMED", "1")

    # Introspection: the override is not installed by the
    # env vars alone. There is no env-var bootstrap in round 2.
    assert bd_client.get_subprocess_invoker_override() is None, (
        "the round-2 env-var bootstrap is back; the security "
        "boundary is broken"
    )

    # End-to-end: a fresh-process-style call to the configured
    # ``bd_binary`` must not invoke the stub. Use a portable
    # interpreter invocation so the test is meaningful on any
    # host; the stub's marker file must not be created.
    proc = bd_client.call(
        sys.executable, args=[str(tmp_path / "present_binary.py")]
    )
    # The configured ``bd_binary`` (sys.executable + .py) is a
    # non-zero exit because the file doesn't exist; what
    # matters is that the stub was never invoked.
    assert "STUB_INVOKED" not in proc.stdout, (
        "the round-2 ambient env attack succeeded; the stub ran "
        "despite no in-process override"
    )
    assert not (tmp_path / "evil_stub.INVOKED").exists(), (
        "the stub marker file was created; the runtime executed "
        "the attacker-controlled path"
    )


def test_explicit_override_activates_stub(tmp_path: Path, monkeypatch) -> None:
    """Calling ``set_subprocess_invoker_override`` is the opt-in.

    After the override is installed the seam is active for the
    rest of the process (and propagates to subprocesses via the
    ``MBA_RUNTIME_BD_STUB_ARMED=1`` marker).
    """

    stub = _write_marker(tmp_path / "test_stub.py")

    previous = bd_client.set_subprocess_invoker_override(stub)
    try:
        assert previous is None, (
            "the override should not be set before this test"
        )
        result = bd_client.call("bd", args=[])
        assert "STUB_INVOKED" in result.stdout, (
            "the explicit override must activate the stub"
        )
    finally:
        bd_client.set_subprocess_invoker_override(None)


def test_clearing_override_restores_production_path(
    tmp_path: Path,
) -> None:
    """``set_subprocess_invoker_override(None)`` returns to ``bd_binary``."""

    stub = _write_marker(tmp_path / "cleared_stub.py")

    bd_client.set_subprocess_invoker_override(stub)
    cleared = bd_client.set_subprocess_invoker_override(None)
    assert cleared is not None and cleared == stub.resolve()

    # The bootstrap from env returns None now.
    assert bd_client.get_subprocess_invoker_override() is None

    # The clearing must also wipe both env vars so a sibling
    # subprocess cannot bootstrap the seam from stale state.
    assert "MBA_RUNTIME_BD_STUB" not in os.environ
    assert "MBA_RUNTIME_BD_STUB_ARMED" not in os.environ


def test_override_persists_to_subprocess_via_path_wrapper(tmp_path: Path) -> None:
    """A forked Python subprocess routes ``bd`` via a PATH wrapper.

    Round-2 (example-017.1): the in-process override does not
    cross process boundaries; the cross-process seam is the
    ``fake_bd_dir`` PATH-based ``bd`` wrapper. A forked
    ``python`` invocation that calls :func:`bd_client.call`
    consults its own module state (no override set) and its
    own PATH (which the fixture prepends with the wrapper
    directory), so the wrapper resolves ``bd`` to the stub.
    """

    stub = tmp_path / "subprocess_stub.py"
    stub.write_text(
        "import sys\n"
        "sys.stdout.write('SUBPROC_STUB_INVOKED\\n')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

    # Build a PATH wrapper identical to what ``fake_bd_dir``
    # would do.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    if sys.platform == "win32":
        wrapper = bin_dir / "bd.cmd"
        wrapper.write_text(
            "@echo off\r\n"
            f'"{sys.executable}" "{stub}" %*\r\n',
            encoding="utf-8",
        )
    else:
        wrapper = bin_dir / "bd"
        wrapper.write_text(
            f"#!/bin/sh\nexec {sys.executable!r} {str(stub)!r} \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)

    env = os.environ.copy()
    path = env.get("PATH", "")
    env["PATH"] = str(bin_dir) + os.pathsep + path

    # Fork a child process that imports ``bd_client`` and
    # calls ``call("bd", args=[])``. The child has its own
    # module state; the in-process override does not
    # transfer. PATH resolution does.
    child_script = tmp_path / "_child.py"
    child_script.write_text(
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from mba_runtime import bd_client\n"
        "import subprocess as _sp\n"
        "_completed = bd_client.call('bd', args=[])\n"
        "sys.stdout.write(_completed.stdout)\n",
        encoding="utf-8",
    )
    project_root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, str(child_script), str(project_root)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert "SUBPROC_STUB_INVOKED" in proc.stdout, (
        f"the PATH wrapper did not route the subprocess "
        f"through the stub; stdout={proc.stdout!r} "
        f"stderr={proc.stderr!r}"
    )
