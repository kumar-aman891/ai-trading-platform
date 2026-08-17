"""Static verification of docker-compose.yml / docker-compose.test.yml -
does not require Docker to be installed or running (parses the YAML files
directly), so it runs in every environment, including this one (Step 5 was
authored where Docker itself was unavailable - see the completion report).

Covers Step 5 database-security items (j) "database and Redis are not
exposed on host ports" and the item 10 Docker-security checklist, to the
extent that checklist is statically verifiable from the compose file text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_compose(filename: str) -> dict[str, Any]:
    text = (REPO_ROOT / filename).read_text(encoding="utf-8")
    loaded: dict[str, Any] = yaml.safe_load(text)
    return loaded


@pytest.fixture(scope="module")
def main_compose() -> dict[str, Any]:
    return _load_compose("docker-compose.yml")


@pytest.fixture(scope="module")
def test_compose() -> dict[str, Any]:
    return _load_compose("docker-compose.test.yml")


def test_main_compose_defines_postgres_and_redis(main_compose: dict[str, Any]) -> None:
    assert set(main_compose["services"]) == {"postgres", "redis"}


def test_no_service_publishes_a_host_port(main_compose: dict[str, Any]) -> None:
    for name, service in main_compose["services"].items():
        assert "ports" not in service, f"{name} must not publish a host port (Step 5 item j)"


def test_test_compose_services_do_not_publish_a_host_port(test_compose: dict[str, Any]) -> None:
    for name, service in test_compose["services"].items():
        assert "ports" not in service, f"{name} must not publish a host port (Step 5 item j)"


def test_no_service_is_privileged(
    main_compose: dict[str, Any], test_compose: dict[str, Any]
) -> None:
    for compose in (main_compose, test_compose):
        for name, service in compose["services"].items():
            assert service.get("privileged") is not True, f"{name} must not be privileged"


def test_no_service_uses_host_network_mode(
    main_compose: dict[str, Any], test_compose: dict[str, Any]
) -> None:
    for compose in (main_compose, test_compose):
        for name, service in compose["services"].items():
            assert service.get("network_mode") != "host", f"{name} must not use host networking"


def test_postgres_and_redis_networks_are_internal_only(
    main_compose: dict[str, Any], test_compose: dict[str, Any]
) -> None:
    for compose in (main_compose, test_compose):
        networks = compose["networks"]
        for _name, definition in networks.items():
            assert definition.get("internal") is True, (
                "the compose network must be internal:true - no outbound route to "
                "the internet, and no route in from the host (docs/ARCHITECTURE.md "
                "§5, security/THREAT_MODEL.md: no broker/Kite/MCP/market-data/LLM "
                "network access exists in Phase 1)"
            )


def test_postgres_has_a_healthcheck(
    main_compose: dict[str, Any], test_compose: dict[str, Any]
) -> None:
    for compose in (main_compose, test_compose):
        assert "healthcheck" in compose["services"]["postgres"]


def test_redis_has_a_healthcheck(
    main_compose: dict[str, Any], test_compose: dict[str, Any]
) -> None:
    for compose in (main_compose, test_compose):
        assert "healthcheck" in compose["services"]["redis"]


def test_no_secret_looking_value_is_hardcoded_in_compose_files(
    main_compose: dict[str, Any], test_compose: dict[str, Any]
) -> None:
    """Every credential-shaped environment entry must be a `${VAR...}`
    reference, never a literal value, in both tracked compose files
    (CLAUDE.md rule #4 / security/SECRET_HANDLING.md)."""
    credential_keys = {
        "POSTGRES_PASSWORD",
        "ATP_OWNER_PASSWORD",
        "ATP_API_PASSWORD",
        "ATP_PAPER_EXEC_PASSWORD",
        "ATP_WORKER_PASSWORD",
        "REDIS_PASSWORD",
    }
    for compose in (main_compose, test_compose):
        for name, service in compose["services"].items():
            env = service.get("environment") or {}
            if isinstance(env, dict):
                items = env.items()
            else:  # list form ["KEY=value", ...] - not used in these files, guarded anyway
                items = (tuple(entry.split("=", 1)) for entry in env)
            for key, value in items:
                if key in credential_keys:
                    assert isinstance(value, str) and value.strip().startswith("${"), (
                        f"{name}.environment.{key} must reference an env var (${{...}}), "
                        f"not a literal value"
                    )


def test_postgres_volume_mounts_are_limited_to_data_and_readonly_sql(
    main_compose: dict[str, Any],
) -> None:
    volumes = main_compose["services"]["postgres"]["volumes"]
    for entry in volumes:
        assert isinstance(entry, str)
        if entry.endswith(":ro"):
            assert "ops/sql" in entry
        else:
            assert entry.startswith("postgres_data:")
