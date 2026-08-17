"""IdGenerator port: a UUIDv7 implementation for runtime use, and a
deterministic fake for tests.

UUID generation is never scattered inline through entity constructors -
every ID-bearing type in this package takes an already-minted ID string as
a constructor argument; only code that has an IdGenerator injected creates
new ones.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@runtime_checkable
class IdGenerator(Protocol):
    def new_id(self) -> str: ...


@dataclass(frozen=True, slots=True)
class UUIDv7Generator:
    """RFC 9562 UUIDv7: a 48-bit millisecond Unix timestamp followed by
    random bits, version and variant fields set per spec. Time-ordered, so
    IDs minted later sort after IDs minted earlier - useful for primary
    keys (docs/schemas/README.md's convention)."""

    def new_id(self) -> str:
        return str(self._generate())

    @staticmethod
    def _generate() -> uuid.UUID:
        unix_ts_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF  # 48 bits
        rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF  # 12 bits
        rand_b = int.from_bytes(os.urandom(8), "big") & 0x3FFFFFFFFFFFFFFF  # 62 bits

        value = unix_ts_ms << 80
        value |= 0x7 << 76  # version nibble
        value |= rand_a << 64
        value |= 0b10 << 62  # variant bits
        value |= rand_b

        return uuid.UUID(int=value)


@dataclass(slots=True)
class SequentialIdGenerator:
    """Deterministic fake for tests: predictable, sequential,
    UUID-shaped-but-fixed-prefix IDs. Never random, so a test asserting on
    an exact generated ID is reproducible."""

    prefix: str = "00000000-0000-7000-8000"
    _counter: int = field(default=0, init=False)

    def new_id(self) -> str:
        self._counter += 1
        return f"{self.prefix}-{self._counter:012d}"
