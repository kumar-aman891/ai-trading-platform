"""ApprovedOrderIntent - the only thing a broker adapter may ever accept
(docs/adr/ADR-008-order-intent-minting.md).

"Only atp_domain.risk.engine may mint one" is enforced by an explicit
capability/issuance mechanism, not by call-stack introspection:

1. `MintingCapability` cannot be constructed by ordinary code -
   `MintingCapability()` raises unless called from inside this module's own
   `issue_minting_capability()` factory, which briefly unlocks construction
   for the duration of that one call.
2. `issue_minting_capability()` succeeds *at most once per process*. The
   second call, from anywhere, raises.
3. `atp_domain.risk.engine` calls it exactly once, at module import time,
   and holds the single resulting capability in a module-private variable
   for the lifetime of the process. No other module in this codebase calls
   it, so no other module ever holds a valid capability.
4. `ApprovedOrderIntent.__init__` requires a `capability: MintingCapability`
   argument (an `InitVar` - consumed by `__post_init__`, never stored as a
   field, so it never appears in equality, repr, or `dataclasses.replace`).
   Presenting anything other than the one real capability instance fails
   the `isinstance` check.

This is an in-process architectural boundary, not a cryptographic one -
deliberately so (nothing here defends against a determined actor rewriting
this module's own source; it defends against ordinary code elsewhere in
the domain accidentally or casually constructing an intent it has no
business creating). There is no classmethod factory, no `create()`, no
alternate constructor - `mint_approved_order_intent` is the only supported
way to obtain an instance, and it is itself gated by the same capability.
"""

from __future__ import annotations

import hashlib
from dataclasses import InitVar, dataclass
from datetime import datetime, timedelta

from atp_domain.clock import Clock
from atp_domain.errors import ExpiredIntentError, IntentMintingNotPermittedError
from atp_domain.ids import IdGenerator
from atp_domain.money import Price, Quantity
from atp_domain.types import (
    DecisionId,
    InstrumentId,
    IntentId,
    Mode,
    OrderType,
    Product,
    ProposalId,
    Side,
)

DEFAULT_INTENT_TTL_SECONDS = 30


class MintingCapability:
    """An unforgeable-by-construction token. At most one instance ever
    exists per process (see `issue_minting_capability`). Possession of a
    genuine instance is the entire proof of authorization - there is
    nothing further to check.

    Not a cryptographic control. `_construction_unlocked` is an ordinary
    mutable class attribute; this defends against accidental or casual
    misuse elsewhere in this trusted codebase, not against an adversary
    willing to edit this module's own source.
    """

    _construction_unlocked = False

    def __new__(cls) -> MintingCapability:
        if not cls._construction_unlocked:
            raise IntentMintingNotPermittedError(
                "MintingCapability cannot be constructed directly - obtain it "
                "via atp_domain.intents.issue_minting_capability(), which "
                "atp_domain.risk.engine calls exactly once, at import time."
            )
        return super().__new__(cls)


_capability_issued = False


def issue_minting_capability() -> MintingCapability:
    """Returns the single MintingCapability instance for this process.
    Succeeds exactly once, ever - every subsequent call, from any caller,
    raises. atp_domain.risk.engine is the only module in this codebase
    that calls this function."""
    global _capability_issued
    if _capability_issued:
        raise IntentMintingNotPermittedError(
            "The minting capability has already been issued once for this "
            "process (to atp_domain.risk.engine at import time) and cannot "
            "be issued again."
        )
    _capability_issued = True
    MintingCapability._construction_unlocked = True
    try:
        return MintingCapability()
    finally:
        MintingCapability._construction_unlocked = False


@dataclass(frozen=True, slots=True)
class CanonicalOrderPayload:
    """The *only* order parameters that exist for a minted intent."""

    instrument_id: InstrumentId
    side: Side
    quantity: Quantity
    order_type: OrderType
    limit_price: Price | None
    trigger_price: Price | None
    product: Product

    def __post_init__(self) -> None:
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("LIMIT orders require a limit_price.")
        if self.order_type is OrderType.MARKET and self.limit_price is not None:
            raise ValueError("MARKET orders must not carry a limit_price.")

    def canonical_string(self) -> str:
        return "|".join(
            (
                str(self.instrument_id),
                self.side.value,
                str(self.quantity),
                self.order_type.value,
                str(self.limit_price) if self.limit_price is not None else "",
                str(self.trigger_price) if self.trigger_price is not None else "",
                self.product.value,
            )
        )


@dataclass(frozen=True, slots=True)
class ApprovedOrderIntent:
    intent_id: IntentId
    mode: Mode
    decision_id: DecisionId
    proposal_id: ProposalId
    canonical_payload: CanonicalOrderPayload
    payload_hash: str
    minted_at: datetime
    expires_at: datetime
    capability: InitVar[MintingCapability]

    def __post_init__(self, capability: MintingCapability) -> None:
        if not isinstance(capability, MintingCapability):
            raise IntentMintingNotPermittedError(
                "ApprovedOrderIntent requires a genuine MintingCapability - "
                "obtain one via atp_domain.intents.issue_minting_capability() "
                "(atp_domain.risk.engine holds the only one)."
            )
        if self.minted_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("minted_at and expires_at must be timezone-aware.")
        if self.expires_at <= self.minted_at:
            raise ValueError("expires_at must be after minted_at.")

    def is_expired(self, *, at: datetime) -> bool:
        if at.tzinfo is None:
            raise ValueError("`at` must be timezone-aware.")
        return at >= self.expires_at

    def require_not_expired(self, *, at: datetime) -> None:
        if self.is_expired(at=at):
            raise ExpiredIntentError(
                f"ApprovedOrderIntent {self.intent_id} expired at {self.expires_at} "
                f"(checked at {at})."
            )


def mint_approved_order_intent(
    *,
    capability: MintingCapability,
    id_generator: IdGenerator,
    clock: Clock,
    decision_id: DecisionId,
    mode: Mode,
    proposal_id: ProposalId,
    canonical_payload: CanonicalOrderPayload,
    ttl_seconds: int = DEFAULT_INTENT_TTL_SECONDS,
) -> ApprovedOrderIntent:
    """The sole supported way to obtain an ApprovedOrderIntent. Requires
    the genuine MintingCapability - see module docstring."""
    if not isinstance(capability, MintingCapability):
        raise IntentMintingNotPermittedError(
            "mint_approved_order_intent requires a genuine MintingCapability."
        )
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive.")

    minted_at = clock.now()
    expires_at = minted_at + timedelta(seconds=ttl_seconds)
    payload_hash = hashlib.sha256(canonical_payload.canonical_string().encode("utf-8")).hexdigest()

    return ApprovedOrderIntent(
        intent_id=IntentId(id_generator.new_id()),
        mode=mode,
        decision_id=decision_id,
        proposal_id=proposal_id,
        canonical_payload=canonical_payload,
        payload_hash=payload_hash,
        minted_at=minted_at,
        expires_at=expires_at,
        capability=capability,
    )
