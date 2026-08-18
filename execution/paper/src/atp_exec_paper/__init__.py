"""Paper execution gateway (Phase 1 Step 9, ADR-011).

The sole choke point through which a PAPER `TradeProposal` becomes
order/fill/position/cash-ledger state (ADR-008). Every public entry point
in `atp_exec_paper.gateway` accepts a `proposal_id` only - never a symbol,
instrument, quantity, price, order type, side, product, or any other order
field directly. See `gateway`'s own module docstring for the full
authoritative sequence, and `tests/safety/test_no_execution_path_in_atp_exec_paper.py`
for the mechanical enforcement of both invariants.

This process is invoked only by a DB-polled claim loop (ADR-011) - never
imported by `atp_api`, never reachable over HTTP, never invoked through a
broker/MCP tool or the worker. No sibling `execution/live/` package exists,
and none is created here.
"""

from __future__ import annotations
