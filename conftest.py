import sys
from pathlib import Path

# Ensure the src/ directory is on sys.path so imports work when testing
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if SRC.exists():
    sys.path.insert(0, str(SRC))
