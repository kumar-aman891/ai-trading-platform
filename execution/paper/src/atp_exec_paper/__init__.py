"""Paper execution gateway.

Empty in Phase 1 Step 2. gateway.py, risk_runner.py, and the DELIBERATELY
FAKE simulator.py land in Phase 1 Step 14, after the risk engine (Step 11)
and ApprovedOrderIntent minting (Step 12) exist.

There is no sibling `execution/live/` package. It is not created in Phase 1
and its absence is asserted by a safety test once the test suite exists
(test_no_live_execution_module_exists, Phase 1 Step 19).
"""
