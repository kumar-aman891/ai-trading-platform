"""LLM port. No implementation in Phase 1 - no LLM provider is integrated
(config.assert_no_forbidden_credentials rejects LLM_* env vars entirely).

Deliberately minimal: a completion request/response pair only. This port
has no method that could produce anything resembling
atp_domain.intents.ApprovedOrderIntent or reach a broker - per ADR-003, an
LLM never determines final execution permission, and nothing in this
protocol gives it the means to try.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    prompt_version: str
    system_prompt: str
    user_content: str


@dataclass(frozen=True, slots=True)
class CompletionResponse:
    text: str
    model: str
    token_usage: int | None


class LLMPort(Protocol):
    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...
