"""Shared test-isolation helpers for the mba_* test suites.

When pytest runs under a repository-local ``--basetemp`` (e.g.
``.mba-work/<bead>/test-temp/``), every ``tmp_path`` lives inside the
active repository. The project's own ``.beads/`` is therefore an
ancestor of ``tmp_path``, which surfaces two pre-existing test-design
defects:

1. ``detect.check_nested_init`` walks up from ``tmp_path``, finds the
   project's ``.beads/`` (which owns ``sync.remote``), and correctly
   refuses. The guard cannot be exercised in the way the unit tests
   expect.
2. ``bd init`` correctly refuses to initialise a workspace nested
   under an ancestor Beads project (the project's Dolt database is
   already initialised).

These product behaviors are correct (per Foundation AC #3 / Charter §9
nested-init guard). The fix is to **isolate the tests from the
project's own ``.beads/``** without changing the product:

* :func:`has_live_beads_ancestor` — detect whether a path is under a
  live Beads project (one with ``sync.remote`` configured).
* :func:`stub_init_beads` — synthesise the ``.beads/`` structure that
  real ``bd init`` produces (mirrors the existing inline stub pattern
  in ``mba_foundation/tests/test_disposable_repo.py``).
* :func:`init_disposable_beads` — try real ``bd init`` when (a) ``bd``
  is on PATH AND (b) ``repo`` has no live ``.beads/`` ancestor;
  otherwise fall back to stub mode.
* :func:`isolate_nested_init_walk` (fixture) — hide the project's
  ``.beads/config.yaml`` from the nested-init guard's upward walk by
  patching ``detect._read_yaml_scalar`` for that one path.

The helpers only affect tests; no production code is touched.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest


def has_live_beads_ancestor(path: Path) -> bool:
    """Return True iff ``path`` has an ancestor ``.beads/`` with
    ``sync.remote`` configured.

    A "live" Beads workspace is one that owns a ``sync.remote`` — the
    same signal the nested-init guard keys off. Used to detect when
    a test's ``tmp_path`` is inside an active Beads project (the
    typical case under a repository-local ``--basetemp``).
    """

    from mba_foundation import detect

    here = path.resolve()
    for parent in [here, *here.parents]:
        candidate = parent / ".beads"
        if not candidate.exists():
            continue
        remote = detect._read_yaml_scalar(
            candidate / "config.yaml", "sync.remote"
        )
        if remote:
            return True
    return False


def stub_init_beads(repo: Path) -> None:
    """Synthesise the ``.beads/`` structure that real
    ``bd init --non-interactive`` would produce.

    The synthesised structure is enough for ``detect.detect_beads``,
    ``sync_guard.check_push_safety``, and the nested-init guard's
    walk, but it does NOT create a Dolt database — any subsequent
    ``bd create`` / ``bd close`` / ``bd show`` will still fail unless
    the caller installs a stub for them too. Mirrors the existing
    inline stub branches in ``test_disposable_repo.py``.
    """

    beads = repo / ".beads"
    beads.mkdir()
    (beads / "metadata.json").write_text(
        '{"database":"dolt","backend":"dolt","dolt_mode":"embedded"}',
        encoding="utf-8",
    )
    (beads / "issues.jsonl").write_text("", encoding="utf-8")


def init_disposable_beads(
    repo: Path,
    *,
    bd_binary: str = "bd",
    prefix: str = "disp",
) -> bool:
    """Initialise a disposable Beads workspace at ``repo``.

    Returns ``True`` when real ``bd init`` succeeded and ``False``
    when stub mode (:func:`stub_init_beads`) was used.

    Real ``bd init`` is used iff **both**:

    * ``bd`` is on PATH (``shutil.which(bd_binary) is not None``); and
    * ``repo`` has no live ``.beads/`` ancestor (see
      :func:`has_live_beads_ancestor`).

    Otherwise the function falls back to stub mode. This keeps the
    tests hermetic under a repository-local ``--basetemp`` where the
    live project's ``.beads/`` would otherwise make real ``bd init``
    correctly refuse.
    """

    if shutil.which(bd_binary) is None or has_live_beads_ancestor(repo):
        stub_init_beads(repo)
        return False

    isolated_prefix = f"{prefix}-{uuid.uuid4().hex[:8]}"
    proc = subprocess.run(
        [bd_binary, "init", "--non-interactive", "--prefix", isolated_prefix],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(repo),
    )
    if proc.returncode == 0:
        return True

    stub_init_beads(repo)
    return False


@pytest.fixture()
def isolate_nested_init_walk(monkeypatch: "pytest.MonkeyPatch") -> None:
    """Hide the project's ``.beads/config.yaml`` from the nested-init
    guard's upward walk for the duration of the test.

    The nested-init guard walks from ``tmp_path`` upward looking for
    ``.beads/`` with ``sync.remote``. Under a repository-local
    ``--basetemp``, the project's ``.beads/`` is the first one found
    and short-circuits the guard. Tests that construct a synthetic
    ``.beads/`` ancestor to exercise specific guard logic need the
    project's ``.beads/`` to be invisible during the test.

    This fixture monkeypatches ``detect._read_yaml_scalar`` to return
    ``None`` for the project's ``.beads/config.yaml``, leaving all
    other paths unaffected. The patch is reverted automatically at
    test teardown via ``monkeypatch``.
    """

    from mba_foundation import detect as detect_mod

    project_root = Path(__file__).resolve().parents[2]
    project_config = (project_root / ".beads" / "config.yaml").resolve()
    original = detect_mod._read_yaml_scalar

    def patched(path: Path, key: str):
        if path.resolve() == project_config:
            return None
        return original(path, key)

    monkeypatch.setattr(detect_mod, "_read_yaml_scalar", patched)
