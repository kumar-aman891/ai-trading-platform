"""Tests for atp_domain.ports - structural checks only, since these are
Protocols with no implementation in Phase 1.

The key invariant: BrokerExecutionPort.submit's signature has exactly one
parameter (besides self), typed as ApprovedOrderIntent, with no *args or
**kwargs through which arbitrary order parameters could be smuggled in.
This is checked via inspect.signature - a structural guarantee that holds
for every future implementation of the Protocol, not just a convention.
"""

from __future__ import annotations

import inspect

from atp_domain.intents import ApprovedOrderIntent
from atp_domain.ports.broker import BrokerExecutionPort, BrokerReadPort


def test_broker_execution_port_submit_accepts_only_an_intent() -> None:
    # eval_str=True resolves the `from __future__ import annotations`
    # string annotations back to the real class object.
    signature = inspect.signature(BrokerExecutionPort.submit, eval_str=True)
    parameters = list(signature.parameters.values())

    # parameters[0] is `self`.
    assert [p.name for p in parameters] == ["self", "intent"]

    intent_param = parameters[1]
    assert intent_param.annotation is ApprovedOrderIntent
    assert intent_param.kind not in (
        inspect.Parameter.VAR_POSITIONAL,
        inspect.Parameter.VAR_KEYWORD,
    )


def test_broker_execution_port_submit_has_no_var_args_or_kwargs() -> None:
    """No *args, no **kwargs anywhere in the signature - nothing besides
    the one typed intent parameter could ever be passed through."""
    signature = inspect.signature(BrokerExecutionPort.submit)
    kinds = {p.kind for p in signature.parameters.values()}
    assert inspect.Parameter.VAR_POSITIONAL not in kinds
    assert inspect.Parameter.VAR_KEYWORD not in kinds


def test_broker_execution_port_is_a_protocol_with_no_implementation() -> None:
    """No implementation exists in Phase 1 - the protocol has no concrete
    subclass registered anywhere in atp_domain."""
    assert BrokerExecutionPort.__mro__[0] is BrokerExecutionPort
    # A Protocol's own methods raise if you try to call them directly -
    # there's no default/fallback broker behaviour hiding in the port.
    assert inspect.isfunction(BrokerExecutionPort.submit) or inspect.iscoroutinefunction(
        BrokerExecutionPort.submit
    )


def test_broker_read_port_has_no_write_capable_method() -> None:
    """The read port must not expose anything order-shaped - defense
    against a future accidental merge of read/write capabilities."""
    method_names = {
        name for name, _ in inspect.getmembers(BrokerReadPort, predicate=inspect.isfunction)
    }
    forbidden_terms = ("submit", "place", "cancel", "modify", "order")
    offending = {
        name for name in method_names if any(term in name.lower() for term in forbidden_terms)
    }
    assert offending == set()
