# Safety suite

Per the approved Phase 1 plan §14, this suite must pass in full, and no test
in it may ever be skipped or xfailed.

| # | Test | Status |
|---|---|---|
| 1 | `test_no_live_execution_module_exists` | ✅ implemented (`test_no_live_execution.py`) |
| 2 | `test_api_db_role_has_zero_privileges_on_live_schema` | deferred — Step 7 (needs migration 0002) |
| 3 | `test_no_foreign_key_crosses_mode_schemas` | deferred — Step 6 (needs migration 0001) |
| 4 | `test_paper_table_rejects_live_mode_row` | deferred — Step 6 |
| 5 | `test_live_proposal_rejects_on_every_rule` | deferred — Step 11 (risk engine) |
| 6 | `test_risk_engine_rejects_when_any_rule_indeterminate` | deferred — Step 11 |
| 7 | `test_kill_switch_fails_closed_on_{db,redis,missing_row,corrupt_value}` | deferred — Step 10 |
| 8 | `test_secret_never_appears_in_logs` | deferred — Step 3 (needs logging/redaction) |
| 9 | `test_executor_rejects_payload_containing_order_parameters` | deferred — Step 14 |
| 10 | `test_intent_is_single_use_under_concurrency` | deferred — Step 12 |
| 11 | `test_duplicate_proposal_submission_creates_exactly_one_order` | deferred — Step 14 |
| 12 | `test_no_route_lacks_explicit_permission` | deferred — Step 13 |
| 13 | `test_settings_refuse_to_start_with_live_mode_or_broker_credentials` | deferred — Step 3 |
| 14 | `test_audit_event_and_state_change_share_a_transaction` | deferred — Step 9 |
| 15 | `test_audit_row_cannot_be_updated_or_deleted` | deferred — Step 6 |
| 16 | `test_approved_intent_minted_only_by_risk_engine` | deferred — Step 12 |

Two additional tests exist now as an early down payment on invariant #1:
`test_no_live_execution_directory_exists` and
`test_no_live_execution_module_is_importable`.
