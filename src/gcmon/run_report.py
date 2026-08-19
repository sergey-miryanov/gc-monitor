import msgspec

OVERRUN_SHARE = 0.1
"""How much of a run has to go missing before gcmon calls it an overrun.

Not one skipped position: the loop waits on an event whose timeout the platform
rounds up to its scheduler tick, so a long healthy run is near certain to skip a
few. See ADR-0019.
"""


class RunReport(msgspec.Struct):
    """What one run of the monitoring loop did with its schedule.

    ``ticks_scheduled`` counts the positions the schedule offered, which is
    larger than ``ticks_run`` whenever a tick outlasted its own position and the
    loop skipped to the next one rather than making the missed ones up. See
    ADR-0019.
    """

    ticks_run: int
    ticks_scheduled: int

    @property
    def overran(self) -> bool:
        """True when enough of the run went missing that a smaller rate cannot help."""
        if self.ticks_scheduled <= 0:
            return False
        missed = self.ticks_scheduled - self.ticks_run
        return missed / self.ticks_scheduled > OVERRUN_SHARE
