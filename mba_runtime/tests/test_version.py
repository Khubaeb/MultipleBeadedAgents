from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from mba_version import __version__

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_version_source_is_public_release() -> None:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", __version__)
    assert match is not None
    assert int(match.group(1)) == 0


def test_runtime_cli_prints_version_from_shared_source() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "mba_runtime", "--version"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == f"mba-runtime {__version__}"
    assert proc.stderr == ""
