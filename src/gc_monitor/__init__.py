"""gc_monitor package init."""
__version__ = "0.1.0"
from .core import greet  # re-export for convenience
__all__ = ["greet", "__version__"]
