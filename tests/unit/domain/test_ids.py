"""Tests for atp_domain.ids - UUIDv7 generator and the deterministic
SequentialIdGenerator fake."""

from __future__ import annotations

import uuid

from atp_domain.ids import IdGenerator, SequentialIdGenerator, UUIDv7Generator


def test_uuidv7_generator_produces_parseable_uuids() -> None:
    generator = UUIDv7Generator()
    value = generator.new_id()
    parsed = uuid.UUID(value)
    assert parsed.version == 7


def test_uuidv7_generator_produces_unique_values() -> None:
    generator = UUIDv7Generator()
    ids = {generator.new_id() for _ in range(50)}
    assert len(ids) == 50


def test_uuidv7_generator_satisfies_id_generator_protocol() -> None:
    assert isinstance(UUIDv7Generator(), IdGenerator)


def test_sequential_id_generator_is_deterministic_across_instances() -> None:
    """Two freshly-constructed generators produce the same sequence - this
    is what makes tests using it reproducible."""
    first = SequentialIdGenerator()
    second = SequentialIdGenerator()
    assert [first.new_id() for _ in range(5)] == [second.new_id() for _ in range(5)]


def test_sequential_id_generator_never_repeats_within_one_instance() -> None:
    generator = SequentialIdGenerator()
    ids = [generator.new_id() for _ in range(10)]
    assert len(set(ids)) == 10


def test_sequential_id_generator_satisfies_id_generator_protocol() -> None:
    assert isinstance(SequentialIdGenerator(), IdGenerator)
