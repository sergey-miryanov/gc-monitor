"""Perfetto protobuf field numbers and enum values.

Hand-maintained against Perfetto's ``.proto`` files (ADR-0001). A wrong
number writes a different message and fails silently, so
``tests/exporters/test_perfetto_proto.py`` guards this module.
"""

from enum import IntEnum

__all__ = [
    "ChildTracksOrdering",
    "CounterDescriptorField",
    "DebugAnnotationField",
    "ProcessDescriptorField",
    "ProcessOrdering",
    "ThreadDescriptorField",
    "ThreadOrdering",
    "TraceField",
    "TracePacketField",
    "TrackDescriptorField",
    "TrackEventField",
    "TrackEventType",
]


class TraceField(IntEnum):
    PACKET = 1


class TracePacketField(IntEnum):
    TIMESTAMP = 8
    SEQUENCE_ID = 10
    TRACK_EVENT = 11
    TRACK_DESCRIPTOR = 60


class TrackDescriptorField(IntEnum):
    UUID = 1
    NAME = 2
    PROCESS = 3
    THREAD = 4
    PARENT_UUID = 5
    COUNTER = 8
    CHILD_ORDERING = 11
    SIBLING_ORDER_RANK = 12
    DESCRIPTION = 14
    PROCESS_ORDERING = 19
    THREAD_ORDERING = 20


class ChildTracksOrdering(IntEnum):
    UNKNOWN = 0
    LEXICOGRAPHIC = 1
    CHRONOLOGICAL = 2
    EXPLICIT = 3


class ProcessOrdering(IntEnum):
    UNSPECIFIED = 0
    EXPLICIT = 1


class ThreadOrdering(IntEnum):
    UNSPECIFIED = 0
    EXPLICIT = 1


class ThreadDescriptorField(IntEnum):
    PID = 1
    TID = 2
    THREAD_NAME = 5


class ProcessDescriptorField(IntEnum):
    PID = 1
    CMDLINE = 2
    PROCESS_NAME = 6
    START_TIMESTAMP_NS = 7


class CounterDescriptorField(IntEnum):
    TYPE = 1
    CATEGORIES = 2
    UNIT = 3
    UNIT_MULTIPLIER = 4
    IS_INCREMENTAL = 5
    UNIT_NAME = 6
    Y_AXIS_SHARE_KEY = 7


class TrackEventField(IntEnum):
    TYPE = 9
    TRACK_UUID = 11
    DEBUG_ANNOTATIONS = 4
    CATEGORIES = 22
    NAME = 23
    COUNTER_VALUE = 30
    DOUBLE_COUNTER_VALUE = 44
    TIMESTAMP_DELTA_US = 1
    TIMESTAMP_ABSOLUTE_US = 16


class DebugAnnotationField(IntEnum):
    # NAME lives at field 10, not field 1. The proto defines
    # `oneof name_field { uint64 name_iid = 1; string name = 10; }`;
    # only one variant of a oneof may be set, so we must use the current
    # `name` slot. Field 1 is now an interned IID (uint64); writing a
    # string there would be misinterpreted as a garbage IID. Do not
    # "fix" this back to 1.
    NAME = 10
    BOOL_VALUE = 2
    INT_VALUE = 4
    STRING_VALUE = 6


class TrackEventType(IntEnum):
    SLICE_BEGIN = 1
    SLICE_END = 2
    INSTANT = 3
    COUNTER = 4
