"""`atp_strategy.__main__` - the service entrypoint (Milestone 2C poll
loop). No Docker needed: `--help` exits via argparse before any settings
are loaded or engine constructed."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_help_exits_zero_without_requiring_any_configuration() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "atp_strategy", "--help"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "atp_strategy" in result.stdout
    assert "Traceback" not in result.stderr


def test_default_evaluation_interval_matches_the_approved_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from atp_strategy.__main__ import _parse_args
    from atp_strategy.runner import DEFAULT_EVALUATION_INTERVAL_SECONDS

    monkeypatch.delenv("ATP_STRATEGY_EVALUATION_INTERVAL_SECONDS", raising=False)
    args = _parse_args([])
    assert args.evaluation_interval_seconds == DEFAULT_EVALUATION_INTERVAL_SECONDS == 60.0


def test_evaluation_interval_env_var_overrides_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from atp_strategy.__main__ import _parse_args

    monkeypatch.setenv("ATP_STRATEGY_EVALUATION_INTERVAL_SECONDS", "15")
    args = _parse_args([])
    assert args.evaluation_interval_seconds == 15.0


def test_evaluation_interval_cli_flag_overrides_the_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from atp_strategy.__main__ import _parse_args

    monkeypatch.setenv("ATP_STRATEGY_EVALUATION_INTERVAL_SECONDS", "15")
    args = _parse_args(["--evaluation-interval-seconds", "5"])
    assert args.evaluation_interval_seconds == 5.0


def test_main_module_defines_no_strategy_evaluation_or_scheduling_flags() -> None:
    """Only the evaluation-interval knob exists - no --strategy-key,
    --once, or scheduling-framework flag has been added."""
    from atp_strategy.__main__ import _parse_args

    args = _parse_args([])
    assert set(vars(args)) == {"evaluation_interval_seconds"}
