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
    "LossTrack",
    "ProcessTrack",
    "SliceBegin",
    "SliceEnd",
    "ThreadTrack",
    "TraceEvent",
    "Track",
]


class ProcessTrack(msgspec.Struct, frozen=True):
    """The process's own row: its marks, and its RSS.

    Nothing here belongs to an interpreter, so nothing here names one.
    """

    pid: int


class ThreadTrack(msgspec.Struct, frozen=True):
    """Interpreter *iid*'s row, carrying its collections."""

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


type Track = ProcessTrack | ThreadTrack | LossTrack


type ArgGroup = dict[str, int | str]

# Perfetto and the trace processor flatten a group's names onto the slice's
# own, so a second level of nesting reads as noise.
type EventArgs = dict[str, int | str | ArgGroup]


class SliceBegin(msgspec.Struct):
    track: Track
    name: str
    cat: str
    ts: int
    # The slice owns *args*: every caller builds the dict for this one event
    # and drops it, so the event keeps it rather than copying it. A capture
    # holds one of these per phase of every collection, and the copy was the
    # largest single cost of converting one.
    args: EventArgs


class SliceEnd(msgspec.Struct):
    """Closes the slice open on *track*.

    Carries no name: the encoder closes a slice with the track uuid alone,
    and a trace processor pairs an END with the BEGIN below it on the row.
    """

    track: Track
    ts: int


class Instant(msgspec.Struct):
    track: ProcessTrack
    name: str
    ts: int


class Counter(msgspec.Struct):
    track: Track
    metric: str
    display_name: str
    ts: int
    value: int | float


type TraceEvent = SliceBegin | SliceEnd | Instant | Counter
