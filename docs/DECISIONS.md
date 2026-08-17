# Key Product Decisions

1. Kite/Kite MCP is primary broker-facing infrastructure.
2. Kite is also primary source for Indian market data where its API coverage is sufficient.
3. yfinance is secondary/research-only, not a live execution source.
4. pandas-ta-classic is default technical indicator layer.
5. PostgreSQL is system-of-record; MongoDB is not required initially.
6. Redis is used for cache/locks/ephemeral state.
7. React/Vite/TypeScript is frontend.
8. FastAPI/Python is backend.
9. Paper and Live trading are separate execution domains.
10. AI proposes; deterministic risk decides; broker executes.
11. Everything relevant is auditable.
12. Default state is safe/off/read-only.
13. Compliance constraints are treated as architecture inputs, not release notes.
14. PAPER/LIVE isolation is separate schemas + separate service identities + separate risk config, not a mode column alone (ADR-005).
15. The AI-facing Kite MCP is read-only; order/GTT tools are excluded at registration, and live execution uses a dedicated direct Kite gateway (ADR-006).
16. Kill switches are six independently-scoped switches; the global switch scopes LIVE only, not PAPER (ADR-007).
17. A broker execution adapter accepts only a minted, single-use ApprovedOrderIntent — never raw order parameters (ADR-008).
18. SQLAlchemy 2.x is used directly, not SQLModel, to keep persistence concerns out of domain and API types (ADR-009).
19. Operational state stays ordinary mutable relational rows; only the audit trail is append-only. The platform is not fully event-sourced (ADR-010).
