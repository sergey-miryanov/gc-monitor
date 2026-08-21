"""How gcmon is driven from a terminal.

The entry point, the subcommands and the environment defaults. ``main`` is not
re-exported here: ``pyproject.toml`` names ``gcmon.cli.main:main`` as the
console script, so the module answers for it and the name means one thing.
"""
