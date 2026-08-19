"""Unit tests for `atp_worker.registry` (Phase 1 Step 12 Phase B, ADR-013 §11).

The load-bearing assertion here is **bidirectional**: `HANDLER_REGISTRY`'s
keys must equal the `job_type` allowlist `core.job_queue`'s `valid_job_type`
CHECK constraint enumerates - no key the database would reject, and no
allowed type left unhandled. The allowlist is *parsed out of the constraint*
rather than restated as a literal, so this test cannot pass by agreeing with
a stale copy of itself: if either side changes, the two stop matching.

Safety invariant #17 (a later step) restates this in the safety tier
alongside the `atp_worker` import-boundary assertions. Having it here too
is deliberate, not duplication for its own sake - it is the registry's own
contract, and it should fail in the unit tier the moment the registry
changes, without waiting for a safety-suite run.
"""

from __future__ import annotations

import re

from atp_persistence.models.core import JobQueueRow
from atp_worker.registry import (
    HANDLER_REGISTRY,
    JOB_TYPE_AUDIT_INTEGRITY_CHECK,
    JOB_TYPE_RETENTION,
    JOB_TYPE_SESSION_REAP,
)

#: The rendered constraint name, not the bare `name="valid_job_type"`
#: passed to `CheckConstraint`: `Base.metadata`'s naming convention
#: (`"ck": "ck_%(table_name)s_%(constraint_name)s"`,
#: `atp_persistence.models.base`) prefixes it, and the rendered form is
#: what both SQLAlchemy's metadata and PostgreSQL's catalog actually hold.
_VALID_JOB_TYPE_CONSTRAINT = "ck_job_queue_valid_job_type"


def _job_type_allowlist_from_check_constraint() -> set[str]:
    """Parses the `valid_job_type` CHECK constraint's SQL text - the
    database's own declaration of what `job_type` may hold."""
    for constraint in JobQueueRow.__table__.constraints:
        if getattr(constraint, "name", None) == _VALID_JOB_TYPE_CONSTRAINT:
            sqltext = str(constraint.sqltext)  # type: ignore[attr-defined]
            return set(re.findall(r"'([A-Z_]+)'", sqltext))
    raise AssertionError(f"core.job_queue has no {_VALID_JOB_TYPE_CONSTRAINT} CHECK constraint")


def test_the_check_constraint_allowlist_is_parseable_and_non_empty() -> None:
    """Guards the parser itself: a silently-empty allowlist would make
    every assertion below vacuous."""
    allowlist = _job_type_allowlist_from_check_constraint()

    assert allowlist == {"SESSION_REAP", "AUDIT_INTEGRITY_CHECK", "RETENTION"}


def test_registry_covers_exactly_the_database_job_type_allowlist() -> None:
    """ADR-013 Section 11, bidirectional: neither side may drift."""
    assert set(HANDLER_REGISTRY) == _job_type_allowlist_from_check_constraint()


def test_registry_registers_no_job_type_the_database_would_reject() -> None:
    """The forward direction stated on its own, so a failure message
    names which extra key is the problem."""
    extra = set(HANDLER_REGISTRY) - _job_type_allowlist_from_check_constraint()

    assert extra == set(), f"registry has handlers the CHECK constraint forbids: {extra}"


def test_registry_leaves_no_allowed_job_type_unhandled() -> None:
    """The reverse direction: a claimable job_type with no handler would
    reach `NoHandlerRegisteredError` and fail terminally in production."""
    missing = _job_type_allowlist_from_check_constraint() - set(HANDLER_REGISTRY)

    assert missing == set(), f"CHECK constraint allows job types with no handler: {missing}"


def test_job_type_constants_match_their_registry_keys() -> None:
    assert set(HANDLER_REGISTRY) == {
        JOB_TYPE_SESSION_REAP,
        JOB_TYPE_AUDIT_INTEGRITY_CHECK,
        JOB_TYPE_RETENTION,
    }
