import asyncio
import os
import time
from typing import Any

import litellm
from litellm.exceptions import (
    APIConnectionError,
    AuthenticationError,
    BadGatewayError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)

from .base import BaseTarget, TargetResponse

# Errors worth retrying: they may disappear on their own.
# Anything else (invalid key, unknown model, rejected request...) fails immediately.
TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    RateLimitError,
    Timeout,
    APIConnectionError,
    InternalServerError,
    BadGatewayError,
    ServiceUnavailableError,
)

# Per-provider settings: LiteLLM model prefix and default request timeout (seconds).
# API keys are read by LiteLLM from the standard environment variables (OPENAI_API_KEY, GROQ_API_KEY, ...).
PROVIDERS: dict[str, dict] = {
    "openai":    {"prefix": "",           "timeout": 30.0},
    "anthropic": {"prefix": "anthropic/", "timeout": 30.0},
    "mistral":   {"prefix": "mistral/",   "timeout": 30.0},
    "groq":      {"prefix": "groq/",      "timeout": 30.0},
    "ollama":    {"prefix": "ollama/",    "timeout": 60.0},  # local models are slower
}

_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY = 5.0  # seconds, growing with each attempt: 5 s, then 10 s


async def complete_with_retries(**kwargs: Any) -> Any:
    """Call `litellm.acompletion`, retrying transient errors only; any other error is raised at once.

    Shared by the targets and the LLM judge so that both follow the same retry policy.
    """
    for attempt in range(1, _MAX_ATTEMPTS):
        try:
            return await litellm.acompletion(**kwargs)
        except TRANSIENT_ERRORS:
            await asyncio.sleep(_RETRY_BASE_DELAY * attempt)
    return await litellm.acompletion(**kwargs)  # last attempt: errors propagate to the caller


class LiteLLMTarget(BaseTarget):
    """Any chat model reachable through LiteLLM, selected by provider name."""

    def __init__(self, provider: str, model: str) -> None:
        if provider not in PROVIDERS:
            raise ValueError(f"Unknown provider '{provider}'. Available: {', '.join(PROVIDERS)}")
        self.name = provider
        self.model = model
        self.litellm_model = PROVIDERS[provider]["prefix"] + model
        self.timeout = PROVIDERS[provider]["timeout"]
        self.api_base = (
            os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") if provider == "ollama" else None
        )

    async def send(self, prompt: str, system_prompt: str | None = None) -> TargetResponse:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict = dict(model=self.litellm_model, messages=messages, timeout=self.timeout, temperature=0)
        if self.api_base:
            kwargs["api_base"] = self.api_base

        start = time.perf_counter()
        try:
            response = await complete_with_retries(**kwargs)
        except AuthenticationError as e:
            raise RuntimeError(f"{self.name} authentication failed — check the API key: {e}") from e
        except Exception as e:
            raise RuntimeError(f"{self.name} request failed: {e}") from e

        latency_ms = (time.perf_counter() - start) * 1000
        content = response.choices[0].message.content or ""
        tokens_used = response.usage.total_tokens if response.usage else None

        return TargetResponse(
            content=content,
            model=response.model or self.litellm_model,
            latency_ms=latency_ms,
            tokens_used=tokens_used,
            raw=dict(response),
        )
