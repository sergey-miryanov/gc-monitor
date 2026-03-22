"""Allow running gc_monitor as a module: python -m gc_monitor."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
