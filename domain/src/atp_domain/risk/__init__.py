"""Deterministic risk engine — reject-by-default (docs/RISK_AND_GUARDRAILS.md).

evaluate() -> RiskDecision (risk.engine) and mint_intent_for_decision() are
the only sanctioned path from a TradeProposal to an ApprovedOrderIntent
(ADR-008). Populated in Phase 1 Step 4.
"""
