"""Step 5 test infrastructure: reproducible startup, clean shutdown, for the
ephemeral stack in docker-compose.test.yml.

Distinct from the rest of tests/integration/db/, which connect to an
*already-running* stack via TEST_* DSNs: this module drives `docker compose`
itself, so it needs the `docker` CLI on PATH, not just network reachability.
It is opt-in (RUN_DOCKER_LIFECYCLE_TESTS=1) rather than autodetected, so a
plain `pytest` run never silently shells out to Docker - and skips (never
fails, never fakes a pass) whenever that opt-in isn't present, which is the
case in the environment this suite was authored in (Docker itself is
unavailable there - see the Step 5 completion report).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "docker-compose.test.yml"
ENV_FILE = REPO_ROOT / "ops" / "docker" / "test.env.example"

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_LIFECYCLE_TESTS") != "1",
    reason=(
        "Opt-in only (set RUN_DOCKER_LIFECYCLE_TESTS=1) - drives `docker compose` "
        "directly rather than connecting to an already-running stack. Docker is "
        "unavailable in the environment this suite was authored in."
    ),
)


def _require_docker() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not found on PATH")


def test_compose_config_is_valid() -> None:
    _require_docker()
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "--env-file", str(ENV_FILE), "config"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_stack_starts_becomes_healthy_and_stops_cleanly() -> None:
    _require_docker()
    up = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--env-file",
            str(ENV_FILE),
            "up",
            "--wait",
            "postgres",
            "redis",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    try:
        assert up.returncode == 0, up.stderr
    finally:
        down = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE_FILE),
                "--env-file",
                str(ENV_FILE),
                "down",
                "-v",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert down.returncode == 0, down.stderr
