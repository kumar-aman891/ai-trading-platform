# AI Trading Platform — Claude Code Starter Specification

This repository is a **non-code product and engineering specification** intended to be given to Claude Code as the starting context for building an AI-assisted trading platform around Zerodha/Kite.

## Design principle

AI proposes; deterministic software decides whether an action is permissible; the broker executes.

The system must support two isolated modes:
- PAPER: simulated capital, simulated fills, no broker order side effects.
- LIVE: real Zerodha account, real orders, additional hard risk gates, explicit activation, complete audit trail.

## Primary stack

Frontend: React + Vite + TypeScript + Tailwind CSS + TradingView Lightweight Charts (or equivalent permissive charting library after license review).

Backend: Python + FastAPI + Pydantic v2 + SQLAlchemy/SQLModel where appropriate + async workers.

Database: PostgreSQL as system-of-record. Redis for cache, locks, queues and short-lived state. Optional object storage for raw research documents and datasets.

Research/backtesting: pandas, numpy, scipy, pandas-ta-classic, vectorbt where useful, plus a deterministic event-driven simulation layer for final validation.

Broker: Zerodha Kite MCP as the AI-facing broker tool layer, with direct Kite Connect/WebSocket integration available as a lower-level adapter for deterministic services and resilience.

AI: Claude Code for development; runtime AI should use a dedicated model provider abstraction so the production application is not coupled to the coding agent.

## Important boundary

Never place broker credentials, access tokens, API secrets, private personal data, or raw account identifiers into LLM prompts unless specifically required by a tool contract. Prefer opaque references and server-side secret retrieval.

## Build order

1. Architecture and contracts.
2. Data ingestion + instrument master + market data normalization.
3. Paper trading engine and backtesting engine.
4. Research/TA/fundamental/news modules.
5. Deterministic risk engine.
6. Live broker adapter behind feature flags.
7. AI agents and MCP orchestration.
8. Production hardening, observability and compliance controls.
9. Only then enable live order execution.
