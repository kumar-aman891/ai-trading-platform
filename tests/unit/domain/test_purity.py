"""Domain purity: atp_domain and every submodule must import cleanly
without pulling in any framework/adapter package.

Runs in a *fresh subprocess*, not the current pytest process - platform
tests (Step 3) already import pydantic/structlog/prometheus_client into
this process's sys.modules, which would make a same-process check pass or
fail for the wrong reason regardless of what atp_domain itself imports. A
subprocess starts with an empty application sys.modules, so this is a
genuine test of atp_domain's own import graph.
"""

from __future__ import annotations

import subprocess
import sys

_FORBIDDEN_PACKAGES = (
    "fastapi",
    "starlette",
    "pydantic",
    "pydantic_settings",
    "sqlalchemy",
    "alembic",
    "redis",
    "structlog",
    "prometheus_client",
    "httpx",
    "requests",
    "aiohttp",
)

_CHECK_SCRIPT = """
import sys

import atp_domain
import atp_domain.audit
import atp_domain.clock
import atp_domain.errors
import atp_domain.ids
import atp_domain.intents
import atp_domain.killswitch
import atp_domain.money
import atp_domain.orders
import atp_domain.proposals
import atp_domain.types
import atp_domain.ports.broker
import atp_domain.ports.calendar
import atp_domain.ports.fundamentals
import atp_domain.ports.llm
import atp_domain.ports.marketdata
import atp_domain.ports.news
import atp_domain.ports.notifications
import atp_domain.ports.storage
import atp_domain.risk.catalog
import atp_domain.risk.config
import atp_domain.risk.engine
import atp_domain.risk.outcomes
import atp_domain.risk.registry
import atp_domain.risk.rule

forbidden = {forbidden!r}
loaded = set(sys.modules)
hits = sorted(m for m in forbidden if m in loaded)
print(",".join(hits))
"""


def test_domain_import_in_isolated_process_pulls_in_no_forbidden_package() -> None:
    script = _CHECK_SCRIPT.format(forbidden=_FORBIDDEN_PACKAGES)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    hits = [h for h in result.stdout.strip().split(",") if h]
    assert hits == [], f"Forbidden packages loaded by importing atp_domain: {hits}"
