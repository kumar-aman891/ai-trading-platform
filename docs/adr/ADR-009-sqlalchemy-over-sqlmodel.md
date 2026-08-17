# ADR-009: SQLAlchemy 2.x, Not SQLModel

## Status
Accepted — Phase 1.

## Context
[docs/TECH_STACK.md](../TECH_STACK.md) permits either "SQLAlchemy 2.x or
SQLModel." The choice determines every model file's shape and is needed
before the first migration.

## Decision
Use **SQLAlchemy 2.x** directly (declarative mapping, typed `Mapped[...]`
columns), not SQLModel.

## Rationale
[.claude/rules/01-architecture.md](../../.claude/rules/01-architecture.md)
requires domain logic to stay independent of framework and persistence
details. SQLModel fuses a Pydantic model with an ORM-mapped table into one
class, which is convenient for small CRUD services but pulls persistence
concerns (table metadata, relationships, session behavior) into the same
type used for API validation and, by extension, invites the same type to
leak into domain code. SQLAlchemy 2.x keeps ORM entities confined to
`atp_persistence.models`, entirely separate from the plain-dataclass domain
types in `atp_domain` (`TradeProposal`, `RiskDecision`, `Order`, ...) and
from the Pydantic DTOs at the API boundary in `atp_api.schemas`. Three
distinct types for three distinct concerns is the deliberate cost; it is
what makes the "domain has zero I/O, zero framework imports" import-linter
contract in the root `pyproject.toml` enforceable at all.

## Consequences
Repository implementations in `atp_persistence.repositories` are
responsible for mapping between ORM entities and domain objects at the
boundary. This mapping code is Phase 1 Step 8 and does not exist yet.
