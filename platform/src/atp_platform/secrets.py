"""SecretProvider port and its Phase 1 implementation.

`EnvSecretProvider` is the only implementation in Phase 1. A production
secret-manager-backed implementation slots in later behind this same
`Protocol` without touching call sites (security/SECRET_HANDLING.md).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class SecretProvider(Protocol):
    """Retrieves a secret value by key. Returns None if the key is unset -
    callers decide whether an unset secret is fatal, this port never guesses."""

    def get(self, key: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class EnvSecretProvider:
    """Reads secrets from process environment variables."""

    def get(self, key: str) -> str | None:
        return os.environ.get(key)
