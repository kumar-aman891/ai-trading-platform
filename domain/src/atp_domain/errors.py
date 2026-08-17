"""Domain exception hierarchy.

A single root (`DomainError`) so callers can catch every domain-raised
failure uniformly, with specific subclasses for callers that need to
distinguish cases (e.g. the risk engine catching an invalid transition
differently from a validation failure).
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for every exception raised by atp_domain."""


class InvalidMoneyValueError(DomainError):
    """A Price/Quantity/Money value failed validation: wrong type, NaN,
    infinite, wrong sign, or too many fractional digits."""


class InvalidTradeProposalError(DomainError):
    """A TradeProposal's field relationships are invalid (e.g. a LIMIT
    order without a limit_price, or a timezone-naive created_at)."""


class InvalidOrderStateTransitionError(DomainError):
    """An Order status transition is not permitted by the state machine."""


class IntentMintingNotPermittedError(DomainError):
    """ApprovedOrderIntent construction was attempted from outside the
    sanctioned atp_domain.risk.engine boundary, or from a non-APPROVED
    RiskDecision (ADR-008)."""


class ExpiredIntentError(DomainError):
    """An ApprovedOrderIntent is being used after its TTL has elapsed."""


class DuplicateRuleRegistrationError(DomainError):
    """The same (rule_id, mode) pair was registered twice in the risk
    rule registry."""


class InvalidSwitchIdError(DomainError):
    """A SwitchId was constructed with an invalid scope/qualifier
    combination (rules/07-kill-switch-taxonomy.md)."""
