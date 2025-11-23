# tests/conftest.py
from pathlib import Path
import sys

# Ensure repository root is on sys.path for imports when running pytest directly.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (str(SRC), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)
