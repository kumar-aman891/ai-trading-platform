"""Step 5 database security item (i): Redis requires authentication."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import pytest
import redis as redis_lib


def test_redis_authenticates_with_the_configured_password(redis_url: str) -> None:
    client = redis_lib.Redis.from_url(redis_url, socket_connect_timeout=3)
    try:
        assert client.ping() is True
    finally:
        client.close()


def test_redis_rejects_connections_without_a_password(redis_url: str) -> None:
    """Strip credentials from the DSN and confirm the same server refuses
    an unauthenticated PING - proves auth is actually enforced server-side,
    not merely present as an unused client-side option."""
    parts = urlsplit(redis_url)
    netloc_without_auth = parts.hostname or "localhost"
    if parts.port:
        netloc_without_auth += f":{parts.port}"
    unauthenticated_url = urlunsplit(
        (parts.scheme, netloc_without_auth, parts.path, parts.query, parts.fragment)
    )

    client = redis_lib.Redis.from_url(unauthenticated_url, socket_connect_timeout=3)
    try:
        with pytest.raises(redis_lib.exceptions.AuthenticationError):
            client.ping()
    finally:
        client.close()


def test_redis_has_no_disk_persistence_configured(redis_url: str) -> None:
    """No persistent trading state in Redis (Step 5 item 5): RDB snapshots
    and AOF are both disabled, so nothing written to this instance survives
    a restart under any circumstance, not just by application convention."""
    client = redis_lib.Redis.from_url(redis_url, socket_connect_timeout=3)
    try:
        save_config = client.config_get("save")
        assert save_config.get("save", "") == ""

        appendonly_config = client.config_get("appendonly")
        assert appendonly_config.get("appendonly") == "no"
    finally:
        client.close()
