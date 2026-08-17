# ADR-002: PostgreSQL First

## Decision
Use PostgreSQL as the transactional system of record.

## Rationale
Orders, fills, positions, risk decisions and audit references require strong integrity, transactions and relational reconciliation.

## Consequence
Use Redis only for transient/cache concerns. Add specialized time-series or analytical storage only after profiling demonstrates the need.
