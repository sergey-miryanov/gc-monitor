"""The `#N` an operator reads when a pid was handed out twice.

One definition, read by the `--stats` table and by the Perfetto trace, so
the two cannot disagree about which process a record belonged to.
"""


def epoch_suffix(pid_epoch: int) -> str:
    """Mark *pid_epoch* on a name, leaving the first process to hold a pid
    unmarked so an ordinary run reads as it always has."""
    return "" if pid_epoch == 1 else f"#{pid_epoch}"
