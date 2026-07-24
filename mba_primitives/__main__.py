"""``mba-primitives`` console-script entry point.

Wired so ``python -m mba_primitives`` and (after install) ``mba-primitives``
reach the same dispatcher in ``cli.main``.
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
