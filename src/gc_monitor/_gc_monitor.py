
import random


class GCMonitorStatsItem:
    def __init__(
        self,
        gen: int,
        ts: int,
        collections: int,
        collected: int,
        uncollectable: int,
        candidates: int,
        object_visits: int,
        objects_transitively_reachable: int,
        objects_not_transitively_reachable: int,
        heap_size: int,
        work_to_do: int,
        duration: float,
        total_duration: float,
    ) -> None:
        self.gen: int = gen
        self.ts: int = ts
        self.collections: int = collections
        self.collected: int = collected
        self.uncollectable: int = uncollectable
        self.candidates: int = candidates
        self.object_visits: int = object_visits
        self.objects_transitively_reachable: int = objects_transitively_reachable
        self.objects_not_transitively_reachable: int = objects_not_transitively_reachable
        self.heap_size: int = heap_size
        self.work_to_do: int = work_to_do
        self.duration: float = duration
        self.total_duration: float = total_duration


class GCMonitorHandler:
    def __init__(self) -> None:
        self._connected = True
        self._length = random.randint(1, 10)

    def read(self) -> list[GCMonitorStatsItem]:
        if not self._connected:
            raise RuntimeError("Handler is not connected")
        # Simulate potential read failure (terminal - handler broken after this)
        if random.random() < 0.1:
            self._connected = False
            raise RuntimeError("Read failed - connection broken")
        return [
            GCMonitorStatsItem(
                gen=random.randint(0, 2),
                ts=i,
                collections=random.randint(1, 100),
                collected=random.randint(10, 100),
                uncollectable=random.randint(0, 10),
                candidates=random.randint(5, 50),
                object_visits=random.randint(100, 1000),
                objects_transitively_reachable=random.randint(50, 500),
                objects_not_transitively_reachable=random.randint(50, 500),
                heap_size=random.randint(10000, 100000),
                work_to_do=random.randint(0, 100),
                duration=random.uniform(0.1, 5.0),
                total_duration=random.uniform(1.0, 50.0),
            )
            for i in range(self._length)
        ]

    def close(self) -> None:
        self._connected = False

    def __enter__(self) -> "GCMonitorHandler":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


def connect() -> GCMonitorHandler:
    return GCMonitorHandler()


def disconnect(handler: GCMonitorHandler) -> None:
    handler.close()
