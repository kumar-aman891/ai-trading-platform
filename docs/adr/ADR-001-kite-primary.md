# ADR-001: Kite as Primary Broker Data/Execution Adapter

## Decision
Use Zerodha Kite/Kite MCP as the primary broker-facing adapter for Indian trading data and execution.

## Rationale
The official Kite MCP already provides market data, historical data, portfolio, orders and GTT functionality and implements most Kite Connect endpoints. citeturn101632view0

Maintain direct Kite Connect/WebSocket adapters beneath a common interface for deterministic services, streaming and resilience where necessary.

## Consequences
The system remains portable to another broker later because domain code depends on a broker port rather than Kite-specific objects.
