# System Architecture

## 1. Logical layers

### Presentation
React/Vite application with route-level feature modules:
- Overview dashboard
- Live trading
- Paper trading
- Chart & technical analysis
- Fundamental analysis
- News & events
- Stock screener/search
- Strategy lab
- Backtesting
- Portfolio/risk
- Orders/executions
- AI activity/audit
- System health
- Settings/security

### API / application layer
FastAPI services provide authentication, query APIs, command APIs, streaming endpoints, and job submission. Keep HTTP concerns separate from domain logic.

### Domain services
- Instrument service
- Market data service
- Technical analysis service
- Fundamental research service
- News/event service
- Strategy registry
- Signal engine
- Portfolio service
- Risk engine
- Order proposal service
- Execution gateway
- Backtesting engine
- P&L/analytics service
- Audit/event service
- AI orchestration service

### Adapters
- Zerodha Kite MCP adapter
- Direct Kite Connect adapter
- Kite WebSocket adapter
- yfinance adapter
- optional licensed/official NSE/BSE adapter
- news provider adapters
- fundamentals adapters
- PostgreSQL adapter
- Redis adapter
- object storage adapter
- LLM adapter

## 2. Critical execution path

AI proposal -> TradeProposal -> deterministic strategy validation -> deterministic risk engine -> RiskDecision -> ApprovedOrderIntent (minted only on APPROVED, single-use, TTL-bound — ADR-008) -> idempotency check -> execution gateway -> broker -> broker acknowledgement -> reconciliation -> portfolio/P&L update -> immutable audit event -> UI.

No LLM sits between the final risk approval and broker call. The execution
gateway's broker-facing method accepts only an `ApprovedOrderIntent` — it
has no parameter through which an AI, or any other caller, could pass raw
order fields (ADR-008). The risk engine runs in-process within each
execution gateway (paper today; live, if ever built, later) rather than as
a separate network service, so the gateway never trusts a caller's
risk-check claim — it re-evaluates risk itself from the proposal and
current config.

## 3. Data flow

Kite/WebSocket -> market-data ingestor -> normalization -> cache + time-series storage -> indicator engine -> signal engine -> strategy evaluation.

Kite REST/MCP -> account/portfolio service -> normalized account state.

News/fundamentals sources -> ingestion -> source/provenance store -> normalized research records -> retrieval layer.

All AI tool calls -> AI audit stream.

## 4. Runtime separation

Use separate process/container/service identities for:
- web frontend
- API
- worker
- market-data stream
- execution gateway
- scheduler
- AI orchestration

The execution gateway should have the narrowest permissions.

## 5. Recommended initial infrastructure

For a single-user/developer deployment:
- Docker Compose
- PostgreSQL
- Redis
- FastAPI
- React/Vite
- one background worker
- one market-data process
- one execution gateway

Scale to Kafka/Redpanda and ClickHouse only when data volume or latency demands it.
