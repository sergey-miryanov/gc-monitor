"""The words `--stats` and `--table-format` take (ADR-0018)."""

from enum import Enum


class TableFormat(Enum):
    PLAIN = "plain"
    MARKDOWN = "markdown"


# Words that ask for no table, and so for no view.
STATS_OFF_WORDS = ("no", "off", "false", "0")


class StatsView(Enum):
    """Which blocks the table prints: `FULL` is `TOTAL` plus one per ring.

    Each member's value is the word typed after `--stats`. No table is `None`.
    """

    TOTAL = "total"
    FULL = "full"

    @classmethod
    def words(cls) -> list[str]:
        """Every word `--stats` and `GCMON_STATS` take, views first."""
        return [view.value for view in cls] + list(STATS_OFF_WORDS)

    @classmethod
    def parse(cls, word: str) -> StatsView | None:
        """Map a typed word to its view, or to None for no table.

        Case-insensitive, and surrounding whitespace is stripped.

        Raises:
            ValueError: the word is neither a view nor one of
                `STATS_OFF_WORDS`.
        """
        normalized = word.strip().lower()
        if normalized in STATS_OFF_WORDS:
            return None
        return cls(normalized)
