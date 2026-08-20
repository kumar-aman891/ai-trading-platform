"""Exception types for the `atp_strategy` runtime (ADR-014, ADR-015).

Deliberately small - Milestone 2B ships no runner, so nothing here is
raised in production yet. Declared now so Milestone 2C's runner has a
settled error type to catch, mirroring `atp_worker.errors`'s precedent of
a single package-rooted base class.
"""

from __future__ import annotations


class StrategyServiceError(Exception):
    """Base for every error this package raises. Never raised directly."""
