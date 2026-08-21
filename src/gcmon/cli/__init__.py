"""How gcmon is driven from a terminal.

The entry point, the subcommands and the environment defaults. ``main`` is
re-exported here because ``pyproject.toml`` names ``gcmon.cli:main`` as the
console script, and that string is what an installed gcmon resolves. The module
holding it is ``entry`` rather than ``main``, so that the name of the module and
the name of the function cannot be confused for each other.
"""

from .entry import main

__all__ = ["main"]
