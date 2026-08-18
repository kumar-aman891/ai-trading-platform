"""`GET /api/v1/audit/events` response shape - read-only, paginated
(Phase 1 Step 7).

`payload` (docs/schemas/audit_event.md's free-form, write-time-redacted
JSON blob) and `actor_id`/`input_hash`/`broker_order_id`/`broker_provider`
are deliberately not exposed by this Step 7 foundation endpoint - the
safest default for a first read-only audit surface is to expose only the
fields explicitly documented as safe (docs/OBSERVABILITY.md's event
fields minus the free-form payload), not to thread a redaction pass
through a field this step does not need to show at all. `source_refs` is
kept (it is documented as "evidence references, not payloads") but is
still re-passed through `atp_platform.redaction.redact_mapping` before
serialization as defense-in-depth, in case a future writer ever puts
something secret-shaped in it despite the field's intended contract.
"""

from __future__ import annotations

from datetime import datetime

from atp_api.schemas.common import ApiModel


class AuditEventResponse(ApiModel):
    event_id: str
    correlation_id: str
    occurred_at: datetime
    actor_type: str
    action: str
    mode: str | None
    decision: str | None
    instrument_id: str | None
    risk_rule_ids: list[str]
    error_code: str | None
    error_class: str | None
    source_refs: dict[str, str]


class AuditEventPage(ApiModel):
    items: list[AuditEventResponse]
    next_before: datetime | None
    limit: int
