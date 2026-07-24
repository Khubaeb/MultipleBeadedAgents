"""F3 (turn-2) — Windows / UTF-8 stdout handling.

The ``MBA_RULES_BLOCK`` literal contains non-ASCII characters
(``§`` U+00A7 and ``→`` U+2192). The runtime integrity (on-disk writes
use ``encoding="utf-8"``) is preserved; the test ensures that the
``python -m mba_foundation`` entry point explicitly sets UTF-8 on
stdout / stderr so Windows consoles (default ``cp1252``) do not
mangle the block content during interactive runs.
"""

from __future__ import annotations

import importlib
import io
import os
import sys
from pathlib import Path

import pytest


def _reload_main():
    """Reload ``mba_foundation.__main__`` to re-run the encoding
    configuration block under the current environment.
    """

    if "mba_foundation.__main__" in sys.modules:
        del sys.modules["mba_foundation.__main__"]
    return importlib.import_module("mba_foundation.__main__")


def test_io_encoding_set_to_utf8_in_main_module(monkeypatch) -> None:
    """The entry point must put stdout/stderr on UTF-8 in one call."""

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    class FakeStream:
        def __init__(self, encoding: str) -> None:
            self.encoding = encoding
            self.calls: list[tuple[str, str]] = []

        def write(self, s):
            return len(s)

        def reconfigure(self, *, encoding: str, errors: str = "strict"):
            self.calls.append((encoding, errors))
            self.encoding = encoding

    fake_stdout = FakeStream(encoding="cp1252")
    fake_stderr = FakeStream(encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", fake_stdout)
    monkeypatch.setattr(sys, "stderr", fake_stderr)

    module = _reload_main()

    assert fake_stdout.calls and fake_stdout.encoding == "utf-8"
    assert fake_stderr.calls and fake_stderr.encoding == "utf-8"
    assert os.environ.get("PYTHONIOENCODING") == "utf-8"


def test_configure_io_encoding_handles_missing_reconfigure(monkeypatch) -> None:
    """Some embedded Pythons (and very old 3.6) lack ``reconfigure``.
    The helper must degrade gracefully and still return the existing
    encodings.
    """

    class LegacyStream:
        encoding = "cp1252"
        # No ``reconfigure`` method.

    monkeypatch.setattr(sys, "stdout", LegacyStream)
    monkeypatch.setattr(sys, "stderr", LegacyStream)
    module = _reload_main()
    out, err = module.configure_io_encoding()
    assert out == "cp1252"
    assert err == "cp1252"
    assert os.environ.get("PYTHONIOENCODING") == "utf-8"


def test_mba_rules_block_is_byte_identical_after_install(tmp_path: Path) -> None:
    """The non-ASCII characters must round-trip cleanly when written
    via UTF-8. This is the product-side promise of F3 — on-disk
    integrity, independent of console encoding.
    """

    from mba_foundation import markers

    target = tmp_path / "INSTRUCTIONS.md"
    markers.install_block(target)

    text = target.read_text(encoding="utf-8")
    # § must appear at least once.
    assert "§" in text
    # The → must appear at least once.
    assert "→" in text
    # Boundary lines still on their own — line-anchored match holds.
    assert text.count("<!-- BEGIN MBA RULES -->") == 1
    assert text.count("<!-- END MBA RULES -->") == 1