"""``mba-runtime`` console-script entry point.

Wired so ``python -m mba_runtime`` and (after install) ``mba-runtime``
reach the same dispatcher in ``cli.main``.
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
