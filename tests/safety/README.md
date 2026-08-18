# Safety suite

Per the approved Phase 1 plan §14, this suite must pass in full, and no test
in it may ever be skipped or xfailed.

**Numbering note (reconciled Phase 1 Step 10):** the "deferred — Step N"
annotations below previously named steps that have since shipped, which
made a genuinely-missing test read as merely not-yet-reached. Every row was
re-verified against the actual test suite for this reconciliation (Step 10)
rather than trusted at face value - see `planning/CURRENT_PROGRESS.md`'s
own numbering note for why "Step N" in this repository does not match any
external plan's numbering.

| # | Test | Status |
|---|---|---|
| 1 | `test_no_live_execution_module_exists` | ✅ implemented (`test_no_live_execution.py`) |
| 2 | `test_api_db_role_has_zero_privileges_on_live_schema` | ✅ implemented, Docker-gated (`tests/integration/db/test_schema_isolation.py::test_api_role_has_zero_privileges_on_live_schema` + `test_paper_exec_role_has_zero_privileges_on_live_schema` + `test_worker_role_has_no_privileges_on_live_schema`) |
| 3 | `test_no_foreign_key_crosses_mode_schemas` | still missing — no test exists yet. Structurally true today only because `live` holds zero tables (`persistence/src/atp_persistence/models/live.py`) for any FK to reference, not because anything asserts it; write a static (no-DB) test walking `Base.metadata` once `live.*` gains its first table |
| 4 | `test_paper_table_rejects_live_mode_row` | ✅ implemented, Docker-gated (`tests/integration/db/test_table_constraints.py::test_trade_proposal_rejects_wrong_mode`) |
| 5 | `test_live_proposal_rejects_on_every_rule` | ✅ implemented (`tests/unit/domain/test_risk_engine.py::test_live_proposal_can_never_be_approved`) |
| 6 | `test_risk_engine_rejects_when_any_rule_indeterminate` | ✅ implemented (`tests/unit/domain/test_risk_engine.py::test_any_indeterminate_causes_overall_reject`) |
| 7 | `test_kill_switch_fails_closed_on_{db,redis,missing_row,corrupt_value}` | ✅ implemented for db/missing_row/corrupt_value (`tests/unit/exec_paper/test_kill_switch_adapter.py::test_missing_switch_resolves_to_unavailable_via_domain_default`, `test_read_failure_yields_empty_mapping_failing_closed`, `test_unparseable_switch_id_is_skipped_not_raised`) — the "redis" case does not apply to this architecture: kill-switch state is PostgreSQL-backed only, never Redis-backed (ADR-002, `docs/TECH_STACK.md`) |
| 8 | `test_secret_never_appears_in_logs` | still missing at the invariant's actual granularity — `tests/unit/test_redaction.py` proves the redaction *function* strips secrets from a mapping, but no test yet proves a secret never reaches the rendered log line through `atp_platform.logging`'s actual structlog pipeline end to end |
| 9 | `test_executor_rejects_payload_containing_order_parameters` | ✅ implemented (`test_no_execution_path_in_atp_exec_paper.py::test_gateway_public_functions_accept_no_raw_order_field`, Phase 1 Step 9) |
| 10 | `test_intent_is_single_use_under_concurrency` | ✅ implemented, unit level (`tests/unit/exec_paper/test_gateway.py::test_concurrent_execution_of_the_same_proposal_produces_exactly_one_order`, Phase 1 Step 9); real-DB concurrency Docker-gated in `tests/integration/db/test_paper_execution_gateway.py` |
| 11 | `test_duplicate_proposal_submission_creates_exactly_one_order` | ✅ implemented (same test as #10, Phase 1 Step 9) |
| 12 | `test_no_route_lacks_explicit_permission` | ✅ implemented for every business/ledger route (`test_rbac_server_side.py::test_every_protected_router_declares_require_permission`, Phase 1 Step 10) — `health.py`'s liveness/readiness probes and `auth.py`'s login/logout/`/me` remain a documented, deliberate exception (pre-auth or self-identity, not a permission-gated business action), not a gap |
| 13 | `test_settings_refuse_to_start_with_live_mode_or_broker_credentials` | ✅ implemented (`tests/unit/test_config.py::test_live_trading_mode_is_rejected_with_explicit_message`, `test_kite_credential_presence_rejects_startup`, `test_llm_credential_presence_rejects_startup`) |
| 14 | `test_audit_event_and_state_change_share_a_transaction` | ✅ implemented, unit level (Phase 1 Step 8 auth events; Phase 1 Step 9 extends this to a business state change - `atp_exec_paper`'s single `PaperExecutionUnitOfWork` transaction per proposal; Phase 1 Step 10 extends it again to `POST /api/v1/paper/proposals`, `atp_api.services.paper_proposals.submit_proposal`); real-DB atomicity proof Docker-gated in `tests/integration/db/test_paper_execution_gateway.py` |
| 15 | `test_audit_row_cannot_be_updated_or_deleted` | ✅ implemented, Docker-gated (`tests/integration/db/test_audit_immutability.py::test_audit_events_reject_update_even_as_owner`, `test_audit_events_reject_delete_even_as_owner`, `test_audit_events_api_role_has_no_update_or_delete_grant`) |
| 16 | `test_approved_intent_minted_only_by_risk_engine` | ✅ implemented (`test_no_execution_path_in_atp_exec_paper.py::test_atp_exec_paper_never_imports_the_low_level_minting_primitives`, Phase 1 Step 9; `test_proposal_intake_is_not_a_risk_gate.py::test_atp_api_never_imports_the_risk_evaluation_or_intent_minting_operations`, Phase 1 Step 10, extends this to the intake side of the same boundary - ADR-012) |

Two additional tests exist now as an early down payment on invariant #1:
`test_no_live_execution_directory_exists` and
`test_no_live_execution_module_is_importable`.

**Genuinely still missing after this reconciliation:** #3 (no FK
cross-schema test - low priority while `live` has zero tables) and #8 (no
end-to-end log-line redaction proof, as opposed to the already-tested
redaction function itself). Both are pre-existing gaps, not introduced by
Phase 1 Step 10.
