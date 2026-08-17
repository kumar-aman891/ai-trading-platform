"""Safety invariant: no LIVE execution path exists (approved Phase 1 plan §14,
test_no_live_execution_module_exists).

This test must never be skipped, xfailed, or deleted without an ADR — it is
the first of the 16 Phase 1 safety-suite invariants and asserts, mechanically
rather than by review, that LIVE order placement is structurally impossible
because the code to do it does not exist.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.safety


def test_no_live_execution_directory_exists() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    assert not (repo_root / "execution" / "live").exists()


def test_no_live_execution_module_is_importable() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("atp_exec_live")
