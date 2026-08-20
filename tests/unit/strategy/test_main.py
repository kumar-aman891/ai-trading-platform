"""`atp_strategy.__main__` - the service entrypoint scaffold (Milestone
2B). No Docker needed: `--help` exits via argparse before any settings are
loaded or engine constructed."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

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


def test_main_module_defines_no_unexpected_cli_arguments() -> None:
    """Milestone 2B ships a placeholder main with no strategy-evaluation
    or scheduling flags - proves the scaffold hasn't grown a --strategy-key
    or --poll-interval-seconds argument prematurely."""
    from atp_strategy.__main__ import _parse_args

    args = _parse_args([])
    assert vars(args) == {}
