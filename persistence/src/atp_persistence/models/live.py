"""`live` schema: deliberately empty in Phase 1 (ADR-005 §5.4, ADR-008).

No ORM model is declared here. The `live` schema itself is created and left
ungranted to every application role by `ops/sql/roles_and_schemas.sql.tmpl`
(bootstrap) and re-asserted, idempotently, by migration 0001 - but no table
exists inside it, so there is nothing to map. When a real LIVE execution
phase is built, this module gains a table set that mirrors `paper`'s; until
then its emptiness is itself a Phase 1 safety control, not an oversight.
"""

from __future__ import annotations
