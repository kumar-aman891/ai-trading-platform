"""Cross-cutting platform concerns: config, secrets, logging, redaction,
correlation IDs, metrics, health primitives (docs/SECURITY.md, docs/OBSERVABILITY.md).

Populated in Phase 1 Step 3. Startup refuses to proceed if TRADING_MODE=LIVE
or any KITE_*/LLM_* credential is present in the environment (ADR-006) - see
`atp_platform.config.load_settings`.
"""
