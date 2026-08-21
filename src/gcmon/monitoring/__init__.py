"""Reaching a process and reading what its collector did.

The monitor, the loop that paces it, the events reader that holds one
attachment per pid, the process handles, the wait and run policies, and the
RSS sampler. This layer writes through an exporter, which is why it imports
one: the direction is downward.
"""
