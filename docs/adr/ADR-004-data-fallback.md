# ADR-004: Explicit Data Provenance and Fallback

## Decision
Use a provider abstraction and explicit fallback policy.

Kite is canonical for Indian broker/market context. Secondary providers can enrich or cross-check but may not silently replace canonical data during live execution.

Every fallback is recorded as a data-quality event.
