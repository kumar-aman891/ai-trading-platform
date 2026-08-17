"""RiskConfig - versioned, immutable risk configuration
(docs/schemas/risk_config.md). A RiskDecision binds to the exact config
version that produced it via `config_hash`, so an evaluation is always
reproducible.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from atp_domain.money import Money
from atp_domain.types import Mode, RiskConfigId


@dataclass(frozen=True, slots=True)
class RiskConfig:
    risk_config_id: RiskConfigId
    mode: Mode
    version: int
    max_order_notional: Money
    created_at: datetime

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("version must be >= 1.")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware.")

    @property
    def config_hash(self) -> str:
        """A deterministic hash of this config's values - never stored as
        a separate field, always derived, so it can never drift from the
        config it describes."""
        canonical = f"{self.mode.value}|{self.version}|{self.max_order_notional.value}"
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
