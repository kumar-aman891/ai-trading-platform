"""Proves tests/integration/db/conftest.py's ATP_REQUIRE_INTEGRATION_STACK
switch actually works, without needing Docker or a reachable database
(Phase 1 Step 11).

This is the load-bearing test of Step 11: it is what makes "the harness
cannot report a false pass" a tested claim rather than an assertion in a
docstring. `tests/integration/db/conftest.py`'s `_require_dsn`/`_connect`
are imported directly (not via fixture indirection) so this module needs
neither Docker nor a database - only the pure control-flow logic of the
switch itself.
"""

from __future__ import annotations

import pytest
from _pytest.outcomes import Failed, Skipped

from tests.integration.db.conftest import REQUIRE_STACK_ENV_VAR, _connect, _require_dsn

_MISSING_ENV_VAR = "TEST_ATP_STACK_GATE_DOES_NOT_EXIST"

# A syntactically valid DSN pointing at a port nothing listens on, so the
# connection attempt fails fast rather than hanging - same idiom as
# tests/unit/api/conftest.py's UNREACHABLE_DATABASE_URL.
_UNREACHABLE_DSN = "postgresql://baduser:badpass@127.0.0.1:1/baddb"


def test_missing_dsn_skips_when_stack_not_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REQUIRE_STACK_ENV_VAR, raising=False)
    monkeypatch.delenv(_MISSING_ENV_VAR, raising=False)

    with pytest.raises(Skipped):
        _require_dsn(_MISSING_ENV_VAR)


def test_missing_dsn_fails_when_stack_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REQUIRE_STACK_ENV_VAR, "1")
    monkeypatch.delenv(_MISSING_ENV_VAR, raising=False)

    with pytest.raises(Failed):
        _require_dsn(_MISSING_ENV_VAR)


def test_unreachable_dsn_fails_when_stack_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REQUIRE_STACK_ENV_VAR, "1")

    with pytest.raises(Failed):
        _connect(_UNREACHABLE_DSN, label="test")


def test_unreachable_dsn_skips_when_stack_not_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REQUIRE_STACK_ENV_VAR, raising=False)

    with pytest.raises(Skipped):
        _connect(_UNREACHABLE_DSN, label="test")
