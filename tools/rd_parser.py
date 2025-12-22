#!/usr/bin/env python3
"""
CLI wrapper for the Ruida RD parser.

The parser implementation lives in laserdove.rd_parser so runtime code can
reuse it without depending on tools/.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running directly from the repository without editable install.
ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
for path in (SRC_ROOT, ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from laserdove.rd_parser import main  # noqa: E402


if __name__ == "__main__":
    main()
