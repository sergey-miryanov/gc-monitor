import msgspec

OVERRUN_SHARE = 0.1
"""How much of a run has to go missing before gcmon calls it an overrun (ADR-0019)."""


class RunReport(msgspec.Struct):
    """What one run of the monitoring loop did with its schedule.

    ``ticks_scheduled`` is how many ticks the rate asked for, ``ticks_run`` how
    many ran. See ADR-0019.
    """

    ticks_run: int
    ticks_scheduled: int

    @property
    def overran(self) -> bool:
        if self.ticks_scheduled <= 0:
            return False
        missed = self.ticks_scheduled - self.ticks_run
        return missed / self.ticks_scheduled > OVERRUN_SHARE
