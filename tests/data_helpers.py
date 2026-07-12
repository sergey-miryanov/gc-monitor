from gcmon.data import InstantMsg


def create_instant_msg(name: str = "start GC monitor", ts: int = 5_000_000) -> InstantMsg:
    return InstantMsg(type="i", name=name, ts=ts)
