"""Port protocols: broker (read + execution), market data, news, fundamentals,
LLM, storage, notifications, clock/calendar (rules/01-architecture.md).

Declared as Protocol types in Phase 1 Step 4. No implementation of any of
these exists in Phase 1. BrokerExecutionPort.submit accepts only an
ApprovedOrderIntent (ADR-008).
"""
