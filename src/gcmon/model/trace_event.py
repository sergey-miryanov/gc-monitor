"""The events every output format is built from.

One converter fills these and every encoder reads them (ADR-0007). `ts` is
nanoseconds (ADR-0009). Every event names the `Track` it is drawn on, and the
encoder derives every other row of the trace from those.
"""

import msgspec

__all__ = [
    "ArgGroup",
    "Counter",
    "EventArgs",
    "Instant",
    "InterpreterTrack",
    "LossTrack",
    "ProcessTrack",
    "Slice",
    "TraceEvent",
    "Track",
]


class ProcessTrack(msgspec.Struct, frozen=True):
    """The process's own row: its marks, and its RSS.

    Nothing here belongs to an interpreter, so nothing here names one.
    """

    pid: int


class InterpreterTrack(msgspec.Struct, frozen=True):
    """Interpreter *iid*'s row, carrying its collections.

    Drawn as a Perfetto thread track and labelled ``Thread {iid}``. That is
    the wire's vocabulary, not gcmon's: an interpreter is not an OS thread,
    and the descriptor says thread only because that is what makes the UI
    draw a row under the process.
    """

    pid: int
    iid: int


class LossTrack(msgspec.Struct, frozen=True):
    """Interpreter *iid*'s loss row, drawn beside its collections per
    ADR-0015.

    One row holds every poll: a poll draws a single span there whatever went
    blind in it, and consecutive polls tile the timeline, so no two spans
    overlap.
    """

    pid: int
    iid: int


type Track = ProcessTrack | InterpreterTrack | LossTrack


type ArgGroup = dict[str, int | str]

# Perfetto and the trace processor flatten a group's names onto the slice's
# own, so a second level of nesting reads as noise.
type EventArgs = dict[str, int | str | ArgGroup]


class Slice(msgspec.Struct):
    """One span on *track*, between two absolute timestamps.

    The pair a record already carries, rather than a start and a duration:
    every producer has `ts_start` and `ts_stop` in hand and passes them
    through. The cost is that `combine` has to shift both ends, which is
    what makes this the one event whose timestamp is not called `ts`.

    Not frozen, for the same reason: `combine` shifts those two ends in
    place, and it holds every event of every capture when it does.

    The encoder expands this into the BEGIN/END pair the wire format has;
    Perfetto has no complete-slice event. See ADR-0024.
    """

    track: Track
    name: str
    cat: str
    ts_start: int
    ts_stop: int
    # The slice owns *args*: every caller builds the dict for this one event
    # and drops it, so the event keeps it rather than copying it. A capture
    # holds one of these per phase of every collection, and the copy was the
    # largest single cost of converting one.
    args: EventArgs


class Instant(msgspec.Struct):
    track: ProcessTrack
    name: str
    ts: int
    # A default rather than a required field, since the two producers of an
    # instant have nothing to put here: a `TInstantMsg` is a type, a name and
    # a `ts`. Built per instant rather than shared, so the encoder can take
    # ownership of it the way it takes a slice's.
    args: EventArgs = msgspec.field(default_factory=dict)


class Counter(msgspec.Struct):
    track: Track
    metric: str
    display_name: str
    ts: int
    value: int | float


type TraceEvent = Slice | Instant | Counter
