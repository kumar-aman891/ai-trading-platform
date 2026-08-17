# Live Order Review Skill

This skill only prepares an order proposal for deterministic validation.

The proposal must include:
- instrument
- exchange/segment
- side
- quantity
- order type
- price/trigger if applicable
- product
- strategy ID/version
- rationale ID
- source timestamp
- expected risk
- stop/invalidation if strategy requires one

It must never contain credentials and must never claim execution.
