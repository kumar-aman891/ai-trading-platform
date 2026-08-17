# Live Trading Activation Runbook

- [ ] Confirm broker/API account and required subscription are active.
- [ ] Confirm current broker/exchange algo/API requirements.
- [ ] Confirm static IP and network configuration where applicable.
- [ ] Confirm current session lifecycle and daily logout behavior.
- [ ] Confirm LIVE execution gateway is isolated from research workers.
- [ ] Confirm global kill switch works.
- [ ] Confirm order idempotency works.
- [ ] Confirm broker reconciliation works.
- [ ] Confirm daily loss and exposure limits are configured.
- [ ] Confirm paper/live separation tests pass.
- [ ] Confirm secrets are stored in a secret manager.
- [ ] Confirm logs redact secrets and sensitive account data.
- [ ] Enable LIVE with the smallest allowed capital allocation.
- [ ] Monitor first executions manually.
