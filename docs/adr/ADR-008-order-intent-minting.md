# ADR-008: ApprovedOrderIntent — the Only Thing a Broker Adapter May Accept

## Status
Accepted — Phase 1 (the type is declared in Phase 1; no broker adapter
consumes it in Phase 1).

## Context
[ADR-003](ADR-003-llm-boundary.md) establishes that no LLM determines final
live order permission. That principle needs a concrete enforcement point:
something that makes it structurally impossible — not merely a code-review
convention — for an AI, a generic API caller, or a compromised upstream
service to hand a broker adapter a set of order parameters directly.

## Decision
Introduce a fourth typed artifact between `RiskDecision` and the broker:

```
TradeProposal -> RiskDecision -> ApprovedOrderIntent -> BrokerExecutionPort.submit()
```

- `ApprovedOrderIntent` is minted **only** by code holding a `MintingCapability`
  (`atp_domain.intents`), and only from a `RiskDecision` whose outcome is
  `APPROVED`. `MintingCapability` cannot be constructed directly -
  `issue_minting_capability()` is the sole factory, it succeeds *at most
  once per process*, and `atp_domain.risk.engine` is the only module that
  ever calls it, at import time, holding the single resulting capability
  in a module-private variable for the process's lifetime.
  `mint_approved_order_intent` (also in `atp_domain.intents`) requires that
  capability as an argument - not stored on the returned intent (an
  `InitVar`, absent from equality/repr/`dataclasses.replace`) - so
  presenting anything other than the one real instance fails the
  `isinstance` check. This is an in-process architectural boundary, not a
  cryptographic one: it defends against ordinary code elsewhere in the
  domain accidentally or casually constructing an intent, not against a
  determined actor rewriting `atp_domain.intents`'s own source.
- It is **single-use** (`UNIQUE(decision_id)` in the paper schema; the same
  constraint pattern applies to `live` when that schema is populated) and
  **short-TTL** (default 30s), so a stale or replayed intent cannot be
  submitted.
- `domain.ports.BrokerExecutionPort.submit(self, intent: ApprovedOrderIntent) -> BrokerAck`
  is the **entire** write interface any broker adapter implements. It takes
  no other parameter. An adapter cannot accept "arbitrary order parameters"
  because its method signature has nowhere to put them — this is enforced
  by the type system, not by the adapter's internal discipline.
- Minting is restricted at three layers: the capability/issuance mechanism
  above, `tests/unit/domain/test_intents.py`'s direct proofs of it
  (`test_minting_capability_cannot_be_constructed_directly`,
  `test_mint_function_rejects_a_forged_capability_lookalike`, and others),
  and `tests/safety/test_no_execution_path_in_atp_exec_paper.py::
  test_atp_exec_paper_never_imports_the_low_level_minting_primitives`,
  which proves the one real call site outside `atp_domain.risk.engine`
  itself (`atp_exec_paper.gateway`, below) never imports the primitives
  needed to mint one directly.
- **No implementation of `BrokerExecutionPort` exists in Phase 1.** No
  `execution/live/` package is created. `atp_exec_paper` (Phase 1 Step 9,
  ADR-011) exercises the identical `TradeProposal -> RiskDecision -> intent`
  pipeline against a fake fill simulator - `atp_exec_paper.gateway`'s
  `mint_intent_for_decision` call, inside `_execute_approved`, is the one
  and only call site outside `atp_domain.risk.engine` itself and the test
  suite - so the pipeline is genuinely exercised, but it does not implement
  `BrokerExecutionPort` against a real broker.

## Consequences
Every future broker integration — Kite direct, or any other broker adopted
later per ADR-001's portability rationale — is constrained to the same
narrow interface. Adding broker-specific order fields later requires
widening `ApprovedOrderIntent`'s `canonical_payload`, an explicit,
reviewable schema change, rather than a call site quietly passing through
extra parameters. This is the concrete mechanism referenced in
[docs/ARCHITECTURE.md](../ARCHITECTURE.md) §2's critical execution path,
which is updated to name `ApprovedOrderIntent` explicitly.
