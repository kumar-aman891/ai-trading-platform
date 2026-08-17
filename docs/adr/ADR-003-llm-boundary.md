# ADR-003: LLM Outside Execution Authority

## Decision
No LLM call may directly determine final live order permission.

The LLM creates a typed proposal. Deterministic services make the final execution decision.

## Rationale
This reduces hallucination, prompt-injection, stale-data and non-determinism risks at the point where capital is exposed.
