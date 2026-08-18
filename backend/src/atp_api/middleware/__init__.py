"""ASGI middleware: security headers, request logging, rate limiting.

Correlation-ID propagation reuses `atp_platform.asgi.CorrelationIdMiddleware`
directly (see `atp_api.app`) rather than duplicating it here.
"""

from __future__ import annotations
