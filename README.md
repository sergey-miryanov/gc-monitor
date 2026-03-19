# examplepkg

A minimal, modern Python package scaffold following best practices:
- src layout
- pyproject-based packaging (PEP 621)
- lightweight tests with pytest
- basic tooling for build, test, and install

## Structure
- `src/gc_monitor/` - package source
- `tests/` - test suite
- `pyproject.toml` - packaging configuration
- `README.md` - project description
- `LICENSE` - license
- `.gitignore` - common ignores

## Getting started
1. Create a virtual environment and activate it
2. Build the distribution: `python -m build`
3. Install in editable mode: `pip install -e .`
4. Run tests: `pytest`
