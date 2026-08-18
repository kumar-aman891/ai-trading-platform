"""API routers: health, system, kill-switches, audit, auth, instruments,
paper (Phase 1 Steps 7, 8, 10).

No router in this package contains trading/business logic - each route
delegates to a function in `atp_api.services` and converts its return
value to a Pydantic response model. `paper.py` performs no risk
evaluation of its own (ADR-012). No `live` router exists, and never will
(ADR-005, ADR-008).
"""

from __future__ import annotations
