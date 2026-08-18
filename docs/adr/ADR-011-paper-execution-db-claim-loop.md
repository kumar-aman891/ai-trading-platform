# ADR-011: Paper Execution Gateway Invocation via a Database Claim Loop

## Status
Accepted — Phase 1 (Step 9).

## Context
`atp_exec_paper` (ADR-005 §2, ADR-008) existed as an empty package stub
through Steps 2–8: the risk engine, `ApprovedOrderIntent` minting, and every
`paper.*` table it needs to write to were built, but nothing ever invoked
it. Three existing constraints jointly rule out the obvious invocation
mechanisms:

1. **No import edge.** `atp_api ↛ atp_exec_paper` is an enforced
   import-linter contract (root `pyproject.toml`) - `atp_api` cannot call
   the gateway in-process.
2. **No egress.** `atp_api`, `atp_exec_paper`, and `atp_worker` may hold no
   outbound HTTP client (root `pyproject.toml`'s no-egress contract,
   ADR-006) - the gateway cannot be invoked over HTTP from any Phase 1
   service, and no broker/MCP tool exists to call it either.
3. **No worker grant.** `atp_worker` has no `USAGE` on the `paper` schema
   at all (`ops/sql/roles_and_schemas.sql.tmpl` §4) - routing invocation
   through `core.job_queue` would require granting the worker order-path
   privileges it deliberately does not hold.

A fourth constraint surfaced during implementation: `atp_paper_exec` holds
only `SELECT` on `paper.trade_proposals` (migration `0003_table_grants`
revokes `INSERT`/`UPDATE`, documented there as "`atp_paper_exec` never
inserts/updates a proposal"). PostgreSQL requires `UPDATE` or `DELETE`
privilege to run `SELECT ... FOR UPDATE`/`FOR SHARE`, so a literal
row-locking claim ("`SELECT ... FOR UPDATE SKIP LOCKED`" against
`trade_proposals`) cannot run under this role without widening a grant that
was deliberately narrowed and documented in Step 6/7.

## Decision
1. `atp_exec_paper` is invoked by a **separate process** running a
   **database-polled claim loop** (`atp_exec_paper.gateway.run_poll_loop`),
   not by any in-process call, HTTP request, broker/MCP tool, or worker job.
2. The loop lists candidate proposals with a **plain, unlocked `SELECT`**
   (`SqlAlchemyTradeProposalRepository.list_unevaluated_paper_proposal_ids`):
   PAPER-mode rows in `paper.trade_proposals` with no matching
   `paper.risk_decisions` row, oldest first. No row lock is taken - none is
   needed, and none is grantable without widening `atp_paper_exec`'s
   privileges.
3. **Exclusivity between concurrent claimants is enforced by the existing
   `UNIQUE (proposal_id)` constraint on `paper.risk_decisions`**, not by a
   row lock. Two processes may both list the same candidate and both
   evaluate risk for it (a pure computation, no side effect until write
   time); only the first `INSERT` into `risk_decisions` succeeds. The
   second raises `IntegrityError`, which `atp_exec_paper.gateway.run_once`
   catches - after `PaperExecutionUnitOfWork`'s transaction has already
   rolled back - and reports as `already_claimed=True`, never as an error
   or a duplicate order/intent.
4. Two entry points exist: `run_once(session_factory, proposal_id, ...)`
   (one-shot; also used by the poll loop, per candidate) and
   `run_poll_loop(session_factory, ...)` (the production mode -
   `python -m atp_exec_paper`, no arguments).
5. The one external input to either entry point is a bare `proposal_id`.
   The gateway reloads the canonical `TradeProposal` from the database
   itself; no function anywhere in `atp_exec_paper` accepts a symbol,
   instrument, quantity, price, order type, side, product, or any other
   order field as a parameter (`tests/safety/test_no_execution_path_in_atp_exec_paper.py`
   asserts this against the actual function signatures).

## Consequences
No grant was widened to make this milestone possible - `atp_paper_exec`'s
privileges are exactly what migration 0003 already established. The
exclusivity guarantee is still a hard database constraint (not merely
"assumed safe in practice"), just enforced at the point actual state is
written rather than at the point candidates are read. The cost is that two
processes can transiently duplicate the (side-effect-free) risk evaluation
computation for the same proposal under real contention; this is
deliberately accepted rather than widening `atp_paper_exec`'s grant to
`UPDATE` on `paper.trade_proposals`, which would contradict Step 6/7's
documented security boundary for that table. If a future phase's
throughput requirements make this duplicated computation costly, revisit
with a new ADR - the fix is a schema/grant change, not a code-only one.

This does not touch `atp_api`, `atp_worker`, or any kill-switch/live
invariant. `docs/ARCHITECTURE.md` §2's critical execution path
(`TradeProposal -> RiskDecision -> ApprovedOrderIntent -> ...`) is realized
end to end for PAPER by this ADR; the equivalent LIVE path remains entirely
unbuilt (ADR-005, ADR-008).

**Amendment (Phase 1 Step 10, ADR-012):** at the time this ADR was written,
the claim loop had no production producer - `paper.trade_proposals` had no
writer outside tests. `POST /api/v1/paper/proposals` (ADR-012) is now that
producer. The claim loop itself is unchanged by Step 10 (`execution/paper/`
carries a zero diff for that milestone); only the number of real candidate
rows `list_unevaluated_paper_proposal_ids` can find changed, from
permanently zero to whatever `atp_api` has recorded.
