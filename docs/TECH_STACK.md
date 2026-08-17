# Technical Stack Decision

## Frontend

React + Vite + TypeScript.
Tailwind CSS for styling.
React Query/TanStack Query for server state.
Zustand or equivalent for small client state only.
Trading chart library behind an interface so licensing/replacement is easy.

## Backend

Python 3.12+ target where supported by dependencies.
FastAPI.
Pydantic v2.
SQLAlchemy 2.x or SQLModel.
Alembic for migrations.
Async HTTP/WebSocket clients.

## Persistence

PostgreSQL = source of truth.
Redis = cache, locks, ephemeral queues.
Optional object storage = raw documents and large research artifacts.

Do not introduce MongoDB initially. Trading/order/account data benefits from relational integrity, transactions, unique constraints and reconciliation. MongoDB can be added later for document-heavy research if a real workload justifies it.

## Analytics

Pandas/NumPy/SciPy.
pandas-ta-classic.
Vectorbt for research acceleration where appropriate.
Custom event-driven simulator for final backtest truth.

## Background work

Start with one worker using a durable job table or task queue. Move to Celery/Arq/RQ/Redpanda/Kafka only when concurrency or durability needs justify it.

## Security

Local .env only for development.
Production secret manager.
Network segmentation so only the execution gateway can access broker write credentials.
