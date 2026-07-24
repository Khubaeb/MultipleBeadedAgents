"""``mba-foundation`` console-script entry point.

Wired in ``pyproject.toml`` so ``python -m mba_foundation`` and
``mba-foundation`` (after install) reach the same dispatcher in
``cli.main``.

Windows / UTF-8 handling (Audit finding F3, turn-2 correction):

* The ``MBA_RULES_BLOCK`` literal contains non-ASCII characters
  (``§`` U+00A7 and ``→`` U+2192). File integrity on disk is
  preserved because every ``write_text`` call uses ``encoding="utf-8"``.
* Console readability on Windows depends on the stdout encoding. The
  default ``cp1252`` Windows code page renders non-ASCII as mojibake;
  the entry point sets ``PYTHONIOENCODING=utf-8`` and reconfigures
  stdout/stderr to UTF-8 before ``cli.main`` runs, so JSON output
  carries the characters through.
"""

from __future__ import annotations

import os
import sys


def configure_io_encoding() -> tuple[str, str]:
    """Make stdout / stderr UTF-8-capable on every platform.

    Belt-and-braces:

    * ``os.environ["PYTHONIOENCODING"] = "utf-8"`` so child processes
      inherit the right default.
    * ``sys.stdout.reconfigure(encoding="utf-8")`` (Python 3.7+) on
      stdout and stderr so the current process renders non-ASCII.

    Returns the (stdout, stderr) encodings that are now in effect so a
    test can confirm the configuration actually applied.
    """

    os.environ["PYTHONIOENCODING"] = "utf-8"
    reconfigured: list[str] = []
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            reconfigured.append(getattr(stream, "encoding", "<none>"))
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (ValueError, OSError):
            reconfigured.append(getattr(stream, "encoding", "<none>"))
            continue
        reconfigured.append(stream.encoding or "<unknown>")
    return reconfigured[0], reconfigured[1]


# Apply the encoding configuration at import time so that any caller
# invoking ``python -m mba_foundation`` (including test drivers) gets
# UTF-8 stdout without an extra opt-in.
_STDOUT_ENCODING, _STDERR_ENCODING = configure_io_encoding()


from .cli import main  # noqa: E402  (import after env configuration)


if __name__ == "__main__":
    sys.exit(main())
