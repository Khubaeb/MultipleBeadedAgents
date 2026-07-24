"""Packaging tests for the Multiple Beaded Agents (MBA) distribution.

These tests pin the public packaging surface that ``example-019.1`` ships:

* ``pyproject.toml`` exists at the repo root and parses.
* It declares the four console scripts and the three packages.
* The dynamic version is wired to ``mba_version.__version__`` (single
  source of truth).
* The root ``README.md`` exists and is non-trivial.
* Every per-package CLI supports ``--version`` and ``--help``.
* The ``mba`` console script resolves to ``mba_foundation.__main__:main``.
* When the package is installed (editable or otherwise), the four
  ``console_scripts`` resolve to callable ``main`` functions and their
  ``--version`` output comes from the shared ``__version__``.
* Excluded / packaging-sensitive paths are NOT pulled into
  ``setuptools`` packages: ``.beads``, ``.mba-work``, ``.claude``,
  ``.git``, ``__pycache__``, ``build``, ``dist``.

The tests run hermetically — they inspect ``pyproject.toml`` and the
installed distributions and do not invoke ``pip`` or ``bd``.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"


def _read_pyproject() -> str:
    assert PYPROJECT_PATH.is_file(), (
        f"missing {PYPROJECT_PATH}; the example-019.1 deliverable requires "
        "a pyproject.toml at the repo root."
    )
    return PYPROJECT_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# pyproject.toml shape
# ---------------------------------------------------------------------------


def test_pyproject_exists_and_uses_setuptools_backend() -> None:
    text = _read_pyproject()
    assert "[build-system]" in text
    assert "build-backend = \"setuptools.build_meta\"" in text
    assert "setuptools" in text


def test_pyproject_declares_required_metadata() -> None:
    text = _read_pyproject()
    assert "name = \"multiple-beaded-agents\"" in text
    assert "dynamic = [\"version\"]" in text
    assert "requires-python" in text
    assert "readme = \"README.md\"" in text
    assert "[project.scripts]" in text


def test_pyproject_declares_four_console_scripts() -> None:
    """The unified ``mba`` plus the three per-package entry points."""
    text = _read_pyproject()
    for script in ("mba", "mba-foundation", "mba-runtime", "mba-primitives"):
        assert re.search(rf'^{re.escape(script)}\s*=', text, re.MULTILINE), (
            f"console script {script!r} not declared in pyproject.toml"
        )


def test_pyproject_declares_three_packages_and_version_module() -> None:
    text = _read_pyproject()
    for pkg in ("mba_foundation", "mba_primitives", "mba_runtime"):
        assert pkg in text, f"package {pkg!r} not declared in pyproject.toml"
    assert "mba_version" in text


def test_pyproject_dynamic_version_reads_mba_version_module() -> None:
    text = _read_pyproject()
    assert "version = { attr = \"mba_version.__version__\" }" in text


def test_pyproject_has_no_runtime_dependencies() -> None:
    """MBA ships stdlib-only. Pip-empty ``dependencies`` keeps the
    install footprint at zero."""
    text = _read_pyproject()
    deps_match = re.search(
        r"^dependencies\s*=\s*\[(.*?)\]",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert deps_match is not None, "missing `dependencies = [...]` in [project]"
    deps_body = deps_match.group(1).strip()
    assert deps_body == "", f"dependencies must be empty, got {deps_body!r}"


# ---------------------------------------------------------------------------
# Root README — needed by the [project] readme field
# ---------------------------------------------------------------------------


def test_root_readme_exists_and_is_concise() -> None:
    readme = PROJECT_ROOT / "README.md"
    assert readme.is_file(), "root README.md missing; required by pyproject readme = ..."
    text = readme.read_text(encoding="utf-8")
    assert len(text) > 200, "root README.md is too short to be useful"
    assert "Multiple Beaded Agents" in text or "MBA" in text


# ---------------------------------------------------------------------------
# Per-package CLIs: --version + --help
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_name, prog_name",
    [
        ("mba_foundation", "mba-foundation"),
        ("mba_runtime", "mba-runtime"),
        ("mba_primitives", "mba-primitives"),
    ],
)
def test_module_cli_supports_version_and_help(module_name: str, prog_name: str) -> None:
    """``python -m <pkg> --version`` and ``--help`` must both work
    after the packaging change."""
    from mba_version import __version__

    for flag, expect_substr in (("--version", __version__), ("--help", prog_name)):
        proc = subprocess.run(
            [sys.executable, "-m", module_name, flag],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, (
            f"`python -m {module_name} {flag}` failed (rc={proc.returncode}): "
            f"stderr={proc.stderr!r}"
        )
        assert expect_substr in proc.stdout, (
            f"`python -m {module_name} {flag}` stdout missing {expect_substr!r}: "
            f"{proc.stdout!r}"
        )


# ---------------------------------------------------------------------------
# Installed distribution — only exercised when the package is on sys.path
# ---------------------------------------------------------------------------


_INSTALLED_PACKAGE_NAME = "multiple-beaded-agents"


@pytest.fixture()
def installed_metadata():
    try:
        return importlib.metadata.metadata(_INSTALLED_PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        pytest.skip(
            f"package {_INSTALLED_PACKAGE_NAME!r} is not installed; "
            "run `pip install -e .` to exercise the installed-surface tests."
        )


def test_installed_package_metadata_resolves(installed_metadata) -> None:
    assert installed_metadata["Name"] == _INSTALLED_PACKAGE_NAME
    from mba_version import __version__

    assert installed_metadata["Version"] == __version__


def test_installed_console_scripts_resolve_to_callable_main(installed_metadata) -> None:
    # Use the modern EntryPoints API; the metadata .get_all("Entry-Point")
    # accessor returns Message objects that are not directly partition-able.
    scripts: dict[str, str] = {}
    for ep in importlib.metadata.entry_points(group="console_scripts"):
        if ep.name in {"mba", "mba-foundation", "mba-runtime", "mba-primitives"}:
            scripts[ep.name] = ep.value
    for script in ("mba", "mba-foundation", "mba-runtime", "mba-primitives"):
        assert script in scripts, (
            f"console script {script!r} not registered in installed metadata"
        )
        module_name, _, attr = scripts[script].partition(":")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, attr)), (
            f"console script {script!r} target {module_name}:{attr} is not callable"
        )


def test_installed_mba_command_runs_version() -> None:
    """``mba --version`` on PATH (or the user-scripts dir) prints the
    shared ``__version__``."""
    mba_bin = shutil.which("mba")
    if mba_bin is None:
        pytest.skip("`mba` not on PATH; skipping console-script invocation check")
    proc = subprocess.run(
        [mba_bin, "--version"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    from mba_version import __version__

    assert __version__ in proc.stdout


# ---------------------------------------------------------------------------
# Boundary conformance — packaging data files stay in scope, excluded dirs do not
# ---------------------------------------------------------------------------


def test_packaging_excludes_dot_beads() -> None:
    """``.beads`` is local Beads state and MUST NOT be a declared package."""
    text = _read_pyproject()
    assert ".beads" not in text, (
        "pyproject.toml must not declare .beads/ as a package or data file"
    )


def test_packaging_excludes_mba_work() -> None:
    """``.mba-work`` is per-bead working evidence and MUST NOT be packaged."""
    text = _read_pyproject()
    assert ".mba-work" not in text, (
        "pyproject.toml must not declare .mba-work/ as a package or data file"
    )


def test_packaging_excludes_claude_and_git() -> None:
    """``.claude`` (Beads hooks) and ``.git`` (VCS metadata) MUST NOT be packaged."""
    text = _read_pyproject()
    for excluded in (".claude", ".git"):
        assert excluded not in text, (
            f"pyproject.toml must not reference excluded path {excluded!r}"
        )


def test_setuptools_packages_only_contains_three_mba_packages() -> None:
    """Belt-and-braces: the declared packages list is exactly the three
    runtime packages — no dogfooding state.

    The pyproject may carry multiple ``[tool.setuptools...]`` sections
    (``[tool.setuptools]``, ``[tool.setuptools.dynamic]``,
    ``[tool.setuptools.package-data]``, ``[tool.setuptools.data-files]``);
    we parse each section independently and assert the canonical
    ``[tool.setuptools]`` block lists exactly the three mba packages.
    """
    text = _read_pyproject()
    sections = re.findall(
        r"\[tool\.setuptools(?:\.[^\]]+)?\](.*?)(?=\n\[|\Z)",
        text,
        re.DOTALL,
    )
    target_body: str | None = None
    for body in sections:
        if re.search(r"^packages\s*=\s*\[", body, re.MULTILINE):
            target_body = body
            break
    assert target_body is not None, (
        "[tool.setuptools] packages = [...] stanza missing"
    )
    match = re.search(r"packages\s*=\s*\[(.*?)\]", target_body, re.DOTALL)
    assert match is not None
    declared: set[str] = set()
    for raw in match.group(1).splitlines():
        # Strip the trailing comma, surrounding quotes, and whitespace.
        cleaned = raw.strip().rstrip(",").strip()
        cleaned = cleaned.strip('"').strip("'")
        if cleaned and not cleaned.startswith("#"):
            declared.add(cleaned)
    assert declared == {"mba_foundation", "mba_primitives", "mba_runtime"}, declared