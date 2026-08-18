"""FastAPI application — the only public-facing service.

Phase 1 Step 7: application/service foundation (`create_app`, health/
readiness, system status, read-only kill-switch and audit endpoints).
Auth, RBAC, CSRF, and the paper-trading router land in Phase 1 Step 8+.

Never imports `atp_exec_paper` (enforced by import-linter, see
`pyproject.toml` at the repo root). No live route, no live DTO, and no
code path constructs `Mode.LIVE` - the LIVE route is a static "not
implemented" page in the frontend only, with no backing API surface.
"""

from __future__ import annotations
