# ADR-012: Proposal Intake Is Not a Risk Gate

## Status
Accepted — Phase 1 (Step 10).

## Context
Through Step 9, `paper.trade_proposals` had no production writer -
`SqlAlchemyTradeProposalRepository.save()` was reachable only from tests
(ADR-011's context section). The complete
`TradeProposal -> RiskDecision -> ApprovedOrderIntent -> Order -> Fill ->
Position -> Cash -> Audit` pipeline (ADR-008, ADR-011) had no way to
receive a proposal in production. Step 10 adds
`POST /api/v1/paper/proposals` in `atp_api` to close that gap.

`atp_api` and `atp_exec_paper` are two separate service identities with two
separate database roles (ADR-005 §2), and `atp_api ↛ atp_exec_paper` is an
enforced import-linter contract (ADR-011's context section, point 1). It
would therefore be structurally impossible for the new intake route to call
the deterministic risk engine even if it wanted to - but "impossible by
accident of the import graph" is not the same guarantee as "impossible by
design, and asserted mechanically." This ADR states the boundary
explicitly and fixes it before any code depends on it, the same way
ADR-011 fixed the gateway's invocation boundary before code depended on
that.

A second question this ADR resolves: should intake be gated on the `PAPER`
kill switch (`ADR-007`), rejecting a submission outright while the switch
is engaged? The `docs/RISK_AND_GUARDRAILS.md` "Hard exits" list includes
"GLOBAL_LIVE kill switch engaged (or, for a PAPER proposal, the PAPER kill
switch engaged)" - but that list describes what must reject a *trade*, not
necessarily where in the pipeline the rejection is recorded.

## Decision
1. **A 2xx response from `POST /api/v1/paper/proposals` means recorded,
   not approved.** The route never returns a risk outcome for a freshly
   submitted proposal - the only way to learn whether a proposal was
   approved, rejected, or not yet evaluated is
   `GET /api/v1/paper/proposals/{proposal_id}`, which reflects whatever
   `atp_exec_paper` has (or has not yet) written.
2. **`atp_api` may never call `atp_domain.risk.engine.evaluate` or
   `atp_domain.risk.engine.mint_intent_for_decision`, and may never import
   `atp_domain.intents` at all.** Asserted mechanically by
   `tests/safety/test_proposal_intake_is_not_a_risk_gate.py` (an AST import
   scan, mirroring `tests/safety/test_no_execution_path_in_atp_exec_paper.py`'s
   existing approach for the gateway side of this same boundary) -
   complementing, not duplicating, the existing `atp_api ↛ atp_exec_paper`
   import-linter contract, since these operations live in `atp_domain`, not
   `atp_exec_paper`, and the import-linter contract alone would not catch a
   direct call from `atp_api`. The boundary is drawn at the *operation*,
   not the *module*: `atp_api.services.paper_ledger` legitimately imports
   the plain, frozen `atp_domain.risk.engine.RiskDecision` dataclass to
   type the already-computed decisions it reads back for the ledger view
   (`GET /api/v1/paper/proposals/{proposal_id}`) - reading a decision
   someone else computed is not evaluating one, and `RiskDecision` carries
   no method that could ever compute or mint anything.
3. **Intake is not gated on the kill switch.** An engaged `PAPER` kill
   switch still accepts the HTTP submission (the proposal is only a typed
   record of intent, not yet a trade), but `atp_exec_paper`'s claim loop
   then evaluates it and produces a persisted, auditable `RiskDecision`
   naming the kill-switch rule that rejected it
   (`PaperKillSwitchRule`/`kill_switch_adapter.py`). This is a strictly
   better audit trail than a silent 403 at the door: every submitted
   proposal gets exactly one `RiskDecision` row either way
   (`docs/schemas/risk_decision.md`: "written for every evaluation"), and
   the platform keeps exactly one authoritative place risk is decided
   (`atp_domain.risk.engine`, run in-process inside `atp_exec_paper` -
   `docs/ARCHITECTURE.md` §2), not two.
4. **Four fields are always server-set, never caller-supplied:** `mode`
   (always `PAPER` - `atp_api` has no LIVE-mode code path at all), `
   created_by` (the authenticated principal's `user_id`, from the session,
   never a request field), `proposal_id` and `created_at` (minted by the
   injected `IdGenerator`/`Clock`, never accepted from the request body).
   `atp_api.schemas.paper.SubmitProposalRequest` has no field for any of
   the four.

## Consequences
`POST /api/v1/paper/proposals` performs only structural validation: request
DTO parsing, `TradeProposal.__post_init__`'s own invariants (LIMIT requires
`limit_price`, MARKET forbids it, non-empty `client_request_id`), and an
`instrument_id` existence check against `core.instruments`. It does not
call the risk engine, does not mint an `ApprovedOrderIntent`, and does not
write to `paper.orders`/`paper.fills`/`paper.positions`/`paper.cash_ledger`
- `atp_exec_paper`'s ADR-011 claim loop remains the only writer of those
four tables, unchanged, with zero diff to `execution/paper/` from this
milestone.

The trade-off accepted here: a submitted proposal briefly exists with no
decision - genuinely `PENDING_EVALUATION`, not a UI illusion - between the
`POST` returning and the claim loop picking it up. This is inherent to
ADR-011's separate-process invocation model and is not new; Step 10 simply
makes it observable through `GET /api/v1/paper/proposals/{proposal_id}`
for the first time.
