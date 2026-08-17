# ADR-006: AI-Facing Kite MCP Is Read-Only; Execution Uses a Dedicated Gateway

## Status
Accepted — Phase 1 (no Kite integration is built in Phase 1; this ADR fixes
the boundary before any is built).

## Context
[ADR-001](ADR-001-kite-primary.md) names the Kite MCP as the primary
broker-facing adapter, and [docs/DATA_SOURCES.md](../DATA_SOURCES.md) notes
the official Kite MCP exposes order, GTT, and trade-modification tools
alongside read operations. CLAUDE.md rule #1 ("never allow an LLM response
to directly call a live broker order endpoint") and
[ADR-003](ADR-003-llm-boundary.md) require that no LLM call ever reach a
broker write endpoint. As written, ADR-001 and CLAUDE.md #1 were in tension:
naming the MCP as primary broker infrastructure without excluding its write
tools would leave the platform's central safety invariant with no
enforcement point.

## Decision
1. The AI-facing Kite MCP registration is **read-only**. Order placement,
   modification, cancellation, and GTT tools are **excluded at
   tool-registration time** — not merely unused by prompting convention.
   Only read tools (quotes, LTP, OHLC, historical data, instrument search,
   holdings, positions, margins) are registered for AI/agent use.
2. Live broker execution will use a **dedicated, direct Kite Connect
   execution gateway** — a deterministic service, not MCP-mediated, holding
   its own broker write credentials, reachable only from the execution
   gateway process (see ADR-005 on service identity, ADR-008 on how orders
   reach it).
3. This gateway is **not implemented in Phase 1**. No `execution/live/`
   package exists (ADR-008), no Kite adapter of any kind exists, and no
   broker credentials are present in any Phase 1 environment.
4. Per [docs/SECURITY.md](../SECURITY.md)'s supply-chain rule, any MCP
   server is reviewed before its write-capable tools are enabled anywhere in
   the system. This review has not occurred and gates any future decision to
   register order-placement tools for any caller, human or AI.

## Consequences
This amends [ADR-001](ADR-001-kite-primary.md): Kite/Kite MCP remains
primary for read-facing data and account context, but the write path is
carved out to a separate, non-MCP adapter under deterministic control. The
platform's portability to another broker (ADR-001's stated rationale) is
undiminished — the execution gateway still depends on a broker port, not
Kite-specific objects.

`atp_platform.config` refuses to start any Phase 1 process if a `KITE_*`
credential is present in its environment (see
[security/SECRET_HANDLING.md](../../security/SECRET_HANDLING.md)), since no
Phase 1 service has a legitimate use for one.
