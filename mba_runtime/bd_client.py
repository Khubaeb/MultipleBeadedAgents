"""`bd` subprocess wrapper.

A small helper around ``subprocess.run`` so tests can monkeypatch a
single function rather than reaching into every module that calls
``subprocess``. The wrapper composes the argv from the supplied
``bd_binary`` and an argument list, threads ``cwd`` through, and
returns the :class:`CompletedProcess` so callers can inspect
stdout/stderr and route the result through the Primitives'
``read_back`` / ``assert_field_matches``.

Production code calls :func:`call` exactly the same way as a test
would; the default implementation shells out to ``bd``. The wrapper
never falls back to shell quoting — it always invokes ``subprocess``
with ``shell=False``.

Subprocess invoker override (explicit test seam, example-017.1 R2):

Round 1 shipped an ambient-bootstrap seam (the env vars
``MBA_RUNTIME_BD_STUB`` and ``MBA_RUNTIME_BD_STUB_ARMED=1``). The
Auditor demonstrated that an attacker who controls the environment
also controls both vars — the ARMED marker added no security boundary.

Round 2 closes the hole completely:

* **The production code path no longer consults any environment
  variable to pick the ``bd`` invoker.** :func:`call` uses either
  the configured ``bd_binary`` (production) or the in-process
  override explicitly installed by tests.
* :func:`set_subprocess_invoker_override(path)` is the **only**
  test seam. Production code never calls it; a fresh process with
  both env vars set and no in-process override now falls through
  to ``bd_binary`` (the attack fails).
* Subprocess tests (CLI smoke tests that fork
  ``python -m mba_runtime``) preserve the seam via a
  per-test PATH-based ``bd`` wrapper installed by the
  ``fake_bd_dir`` fixture. PATH is the standard cross-platform
  command resolution mechanism; the wrapper is an explicit
  per-test artefact, not an ambient env-var.

When ``bd_binary`` is a bare command name (no path separator) the
helper resolves it through ``shutil.which(bd_binary, path=...)``
against the supplied ``env['PATH']`` (or the process PATH) and
passes the absolute path as ``executable=`` to ``subprocess.run``.
This:

* Honours test-side PATH modifications on Windows (where
  ``CreateProcess`` with bare names does not always re-read the
  inherited ``PATH`` env var — see the round-2 working log);
* Is a no-op in production when the PATH already resolves ``bd``
  correctly (the resolved path is the same).

Encoding: ``utf-8`` is forced via ``encoding="utf-8"`` so
non-ASCII ``bd`` output (e.g. the literal ``✓ No dependency
cycles detected`` from ``bd dep cycles``) round-trips
byte-for-byte. ``errors="backslashreplace"`` keeps ``bd``
replies that contain a stray non-UTF-8 byte from raising.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence


# Module-level test seam. ``None`` means production path; tests
# install a ``Path`` via :func:`set_subprocess_invoker_override`.
_INVOKER_OVERRIDE: Path | None = None

# Env-var names retained ONLY for documentation / debug purposes —
# they are no longer consulted by :func:`call`. A future contributor
# who reintroduces env-var bootstrap will see these names already
# taken and is forced to consider the security boundary explicitly.
_LEGACY_STUB_ENV_VAR: str = "MBA_RUNTIME_BD_STUB"  # noqa: F841
_LEGACY_STUB_ARMED_VAR: str = "MBA_RUNTIME_BD_STUB_ARMED"  # noqa: F841


def _has_path_separator(name: str) -> bool:
    """Return True when ``name`` contains a path separator.

    Used to decide whether ``bd_binary`` is a bare command name
    (resolved via PATH) or an absolute path (passed through).
    """

    if not name:
        return False
    if os.sep and os.sep in name:
        return True
    if os.altsep and os.altsep in name:
        return True
    return False


def set_subprocess_invoker_override(path: Path | None) -> Path | None:
    """Install (or clear) the in-process ``bd`` subprocess invoker override.

    This is the **only** way to activate the test seam. The
    production code path does not consult any environment variable
    (round 2 hardening — see module docstring). Subprocess tests
    that need a fake ``bd`` either pass through this seam (when
    they execute in-process) or use the PATH-based wrapper the
    ``fake_bd_dir`` fixture installs (when they fork
    ``python -m mba_runtime``).

    Pass ``None`` to clear. The function does **not** write to
    the environment — there is no cross-process seam to leak via
    this setter.

    Returns the previous override (``None`` when none was set).
    """

    global _INVOKER_OVERRIDE
    previous = _INVOKER_OVERRIDE
    if path is None:
        _INVOKER_OVERRIDE = None
    else:
        _INVOKER_OVERRIDE = Path(path).resolve()
    return previous


def get_subprocess_invoker_override() -> Path | None:
    """Return the in-process override (``Path``) or ``None``.

    Round-2 note: there is no env-var bootstrap. The function is
    retained as the public introspection seam (used by tests to
    assert the override state without exercising ``call``).
    """

    return _INVOKER_OVERRIDE


def _resolve_bd_binary(
    bd_binary: str, *, env: dict[str, str] | None
) -> str:
    """Return the absolute path to ``bd_binary`` when it is a bare name.

    When ``bd_binary`` contains a path separator (absolute or
    relative) the value is returned unchanged. Otherwise the
    helper looks the name up in ``env['PATH']`` (or the process
    PATH when ``env`` is ``None``) via :func:`shutil.which`. A
    failed lookup returns ``bd_binary`` unchanged; ``subprocess``
    will raise the appropriate error.

    Resolving through :func:`shutil.which` (rather than relying
    on ``CreateProcess`` PATH resolution) lets the round-2
    fixture's PATH-based ``bd`` wrapper intercept the call,
    including on Windows hosts where bare-name command
    resolution may bypass the inherited ``PATH``.
    """

    if _has_path_separator(bd_binary):
        return bd_binary
    path_value = env["PATH"] if env is not None and "PATH" in env else os.environ.get("PATH", "")
    resolved = shutil.which(bd_binary, path=path_value)
    return resolved if resolved else bd_binary


def call(
    bd_binary: str,
    *,
    args: Sequence[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run ``bd_binary`` with ``args`` via :func:`subprocess.run`.

    ``shell=False`` is the multiline-safety guarantee the Primitives
    rely on for every ``bd`` write. ``check=False`` means callers
    inspect the returncode (or route through ``read_back``) instead
    of relying on exceptions.

    Subprocess invoker resolution (example-017.1 R2):

    1. If the in-process override installed by
       :func:`set_subprocess_invoker_override` is set, use it.
       Tests that run in-process get the stub.
    2. Otherwise, use the configured ``bd_binary`` (production
       path). When ``bd_binary`` is a bare command name the helper
       resolves it via :func:`shutil.which` against the supplied
       ``env['PATH']`` and passes the absolute path as
       ``executable=`` — this is what makes the round-2 PATH-based
       wrapper seam work on Windows hosts where bare-name
       resolution otherwise ignores the inherited ``PATH``.

    The function does **not** consult any environment variable
    to pick the invoker; a fresh process with both
    ``MBA_RUNTIME_BD_STUB=<evil.py>`` and
    ``MBA_RUNTIME_BD_STUB_ARMED=1`` set falls through to
    ``bd_binary`` (the override check finds ``None``) and
    ``subprocess.run`` invokes whatever ``shutil.which`` resolves
    in the inherited PATH — never ``<evil.py>``.
    """

    override = _INVOKER_OVERRIDE
    if override is not None:
        argv = [sys.executable, str(override), *args]
        executable = sys.executable
    else:
        argv = [bd_binary, *args]
        executable = _resolve_bd_binary(bd_binary, env=env)
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        check=check,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        encoding="utf-8",
        errors="backslashreplace",
        executable=executable,
    )
