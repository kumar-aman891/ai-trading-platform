"""Safety invariant #3 (tests/safety/README.md):
`test_no_foreign_key_crosses_mode_schemas`.

A foreign key directly between `paper.*` and `live.*` would let PAPER and
LIVE data reference each other, undermining the schema-level isolation
ADR-005 relies on. This is a static, no-database walk of `Base.metadata` -
so it holds even though `live` currently has zero tables
(`atp_persistence.models.live`) and there is nothing yet for a real FK to
reference. It exists precisely so the invariant is already enforced
*before* `live.*` gains its first table, not bolted on afterwards.

Note what this deliberately does *not* flag: both `paper.*` and (in a
future phase) `live.*` legitimately reference shared, mode-neutral `core.*`
reference data (`core.instruments`, `core.users`, `core.risk_config`) - see
`atp_persistence/models/paper.py`. `core` is not itself a mode, so a
`paper -> core` or `live -> core` foreign key is expected and safe; only a
foreign key directly crossing `paper <-> live` is the invariant this test
enforces.

Must never be skipped, xfailed, or removed without an ADR.
"""

from __future__ import annotations

import pytest

from atp_persistence.models import Base

pytestmark = pytest.mark.safety

_MODE_SCHEMAS = frozenset({"paper", "live"})


def test_no_foreign_key_crosses_mode_schemas() -> None:
    violations: list[str] = []

    for table in Base.metadata.tables.values():
        owning_schema = table.schema
        for fk in table.foreign_keys:
            referenced_schema = fk.column.table.schema
            if (
                owning_schema in _MODE_SCHEMAS
                and referenced_schema in _MODE_SCHEMAS
                and owning_schema != referenced_schema
            ):
                violations.append(
                    f"{owning_schema}.{table.name}.{fk.parent.name} -> "
                    f"{referenced_schema}.{fk.column.table.name}.{fk.column.name}"
                )

    assert not violations, (
        "Foreign key(s) cross the PAPER/LIVE mode boundary directly, "
        f"undermining ADR-005's schema-level isolation: {violations}"
    )


def test_at_least_one_foreign_key_exists_so_this_test_is_not_vacuous() -> None:
    """Guards against the walk above silently checking nothing (e.g. if
    `Base.metadata` were ever populated incompletely by a missing model
    import)."""
    total_foreign_keys = sum(len(table.foreign_keys) for table in Base.metadata.tables.values())
    assert total_foreign_keys > 0
