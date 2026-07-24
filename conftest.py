"""Project-root pytest fixtures and helpers shared across mba_* tests.

The only fixture defined here is the **test-only exception** for the
three real-Beads integration tests:

* ``mba_primitives/tests/test_disposable_repo.py::test_round_trip_description_and_labels_on_disposable_repo``
* ``mba_runtime/tests/test_disposable_repo.py::test_disposable_repo_isolated_from_live_project``
* ``mba_runtime/tests/test_external_dispatch.py::test_external_dispatch_in_disposable_bd_repo``

Each of these tests requires a uniquely-created OS temporary workspace
**outside** the live Beads repository, so ``bd init`` does not
collide with the project's existing Dolt database. Every ordinary
test continues to use repository-local ``--basetemp``.

The ``assert_outside_repository`` fixture exposes an exact boundary
assertion tied to ``PROJECT_ROOT``; it replaces the prior fragile
``is_pytest_temp`` substring check.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent


def _has_beads_ancestor(path: Path) -> bool:
    """Return True when ``path`` is inside any existing Beads workspace."""

    here = path.resolve()
    for parent in [here, *here.parents]:
        if (parent / ".beads").exists():
            return True
    return False


def _candidate_workspace_roots() -> list[Path]:
    """Preferred roots for real-Beads disposable workspaces.

    Windows ``tempfile`` often lives under ``C:\\Users\\<user>``. If that
    user folder has a ``.beads/`` workspace, ``bd init`` will discover the
    ancestor instead of creating a disposable repo. Prefer a sibling of the
    project, then fall back to OS temp, then the drive root on Windows.
    """

    roots: list[Path] = []
    configured = os.environ.get("MBA_TEST_WORKSPACE_ROOT")
    if configured:
        roots.append(Path(configured))

    roots.append(PROJECT_ROOT.parent / ".mba-test-workspaces")
    roots.append(Path(tempfile.gettempdir()) / "mba-test-workspaces")

    if os.name == "nt":
        drive = os.environ.get("SystemDrive")
        if drive:
            roots.append(Path(drive + "\\") / "mba-test-workspaces")

    return roots


def _safe_workspace_root() -> Path:
    """Return a writable root outside every existing Beads workspace."""

    for root in _candidate_workspace_roots():
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if _has_beads_ancestor(root):
            continue
        return root

    pytest.skip("no writable temporary root outside an ancestor Beads workspace")


def _cleanup_workspace(workspace: Path) -> None:
    """Delete ``workspace`` robustly on Windows.

    ``bd init`` runs ``git init`` and ``git add``, which produces
    read-only object files under ``.git/objects/``. Plain
    :func:`shutil.rmtree` raises ``PermissionError`` on those files
    and ``ignore_errors=True`` then silently leaves the directory in
    place. This helper clears the read-only bit before retrying and
    falls back to ``cmd /c rmdir /s /q`` for any residual lock.
    """

    def _onerror(func, path, _exc_info):  # type: ignore[no-untyped-def]
        try:
            os.chmod(path, stat.S_IWRITE)
        except OSError:
            pass
        try:
            func(path)
        except OSError:
            pass

    if not workspace.exists():
        return

    shutil.rmtree(workspace, onerror=_onerror)
    if workspace.exists() and os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "rmdir", "/s", "/q", str(workspace)],
            check=False,
            capture_output=True,
            timeout=30,
        )


@pytest.fixture()
def authorized_workspace():
    """Yield a uniquely-created OS temporary workspace OUTSIDE the
    active repository.

    Created via :func:`tempfile.mkdtemp` (typically
    ``C:\\Users\\<user>\\AppData\\Local\\Temp\\`` on Windows) with the
    prefix ``mba-real-bd-``. The workspace's parent chain does not
    include the project's ``.beads/``, so ``bd init`` succeeds and
    the writes stay inside the workspace. Cleanup happens
    automatically at test teardown via :func:`_cleanup_workspace`.

    Used only by the three real-Beads integration tests listed in
    the module docstring; every other test remains under
    repository-local ``--basetemp``.
    """

    workspace_root = _safe_workspace_root()
    workspace = Path(
        tempfile.mkdtemp(prefix="mba-real-bd-", dir=str(workspace_root))
    )
    try:
        yield workspace
    finally:
        _cleanup_workspace(workspace)


@pytest.fixture()
def assert_outside_repository():
    """Return a callable that asserts ``path`` is NOT under
    :data:`PROJECT_ROOT`. The check uses :meth:`Path.relative_to`
    (raises :class:`ValueError` when ``path`` is not under the
    target), so it is exact and OS-portable.

    Replaces the fragile ``"pytest-of-" in cwd_str`` substring
    check used previously.
    """

    project = PROJECT_ROOT.resolve()

    def _assert(path: Path) -> None:
        resolved = path.resolve()
        try:
            resolved.relative_to(project)
        except ValueError:
            return
        raise AssertionError(
            f"path {resolved} is inside the active repository {project}; "
            f"this fixture/test must use an authorized workspace "
            f"outside the project."
        )

    return _assert
