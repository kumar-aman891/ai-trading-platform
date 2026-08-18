"""API routers (Phase 1 Step 7): health, system, kill-switches, audit.

No router in this package contains trading/business logic - each route
delegates to a function in `atp_api.services` and converts its return
value to a Pydantic response model. No `auth`/`session`/`paper`/`live`
router exists yet; `live` never will (ADR-005, ADR-008).
"""

from __future__ import annotations
