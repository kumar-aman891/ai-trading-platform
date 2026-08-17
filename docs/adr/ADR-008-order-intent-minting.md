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

- `ApprovedOrderIntent` is minted **only** inside `atp_domain.risk.engine`,
  and only from a `RiskDecision` whose outcome is `APPROVED`.
- It is **single-use** (`UNIQUE(decision_id)` in the paper schema; the same
  constraint pattern applies to `live` when that schema is populated) and
  **short-TTL** (default 30s), so a stale or replayed intent cannot be
  submitted.
- `domain.ports.BrokerExecutionPort.submit(self, intent: ApprovedOrderIntent) -> BrokerAck`
  is the **entire** write interface any broker adapter implements. It takes
  no other parameter. An adapter cannot accept "arbitrary order parameters"
  because its method signature has nowhere to put them — this is enforced
  by the type system, not by the adapter's internal discipline.
- Minting is restricted at three layers: a module-private factory function,
  an import-linter contract forbidding every package except
  `atp_domain.risk.engine` from importing it, and a dedicated test
  (`test_approved_intent_minted_only_by_risk_engine`).
- **No implementation of `BrokerExecutionPort` exists in Phase 1.** No
  `execution/live/` package is created. `atp_exec_paper` exists and
  exercises the identical `TradeProposal -> RiskDecision -> intent` pipeline
  against a fake fill simulator, so the pipeline is genuinely exercised, but
  it does not implement `BrokerExecutionPort` against a real broker.

## Consequences
Every future broker integration — Kite direct, or any other broker adopted
later per ADR-001's portability rationale — is constrained to the same
narrow interface. Adding broker-specific order fields later requires
widening `ApprovedOrderIntent`'s `canonical_payload`, an explicit,
reviewable schema change, rather than a call site quietly passing through
extra parameters. This is the concrete mechanism referenced in
[docs/ARCHITECTURE.md](../ARCHITECTURE.md) §2's critical execution path,
which is updated to name `ApprovedOrderIntent` explicitly.
