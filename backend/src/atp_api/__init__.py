"""FastAPI application — the only public-facing service.

Empty in Phase 1 Step 2. Auth, RBAC, CSRF, security headers, and the paper
trading + audit + kill-switch + health routers land in Phase 1 Step 13.

Never imports atp_exec_paper (enforced by import-linter, see pyproject.toml
at the repo root). No live route, no live DTO, and no code path constructs
Mode.LIVE — the LIVE route is a static "not implemented" page in the
frontend only, with no backing API surface.
"""
