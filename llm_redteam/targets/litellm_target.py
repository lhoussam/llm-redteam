import asyncio
import os
import time

import litellm
from litellm.exceptions import AuthenticationError, RateLimitError

from .base import BaseTarget, TargetResponse

# Per-provider settings: LiteLLM model prefix and default request timeout (seconds).
# API keys are read by LiteLLM from the standard environment variables (OPENAI_API_KEY, GROQ_API_KEY, ...).
PROVIDERS: dict[str, dict] = {
    "openai":    {"prefix": "",           "timeout": 30.0},
    "anthropic": {"prefix": "anthropic/", "timeout": 30.0},
    "mistral":   {"prefix": "mistral/",   "timeout": 30.0},
    "groq":      {"prefix": "groq/",      "timeout": 30.0},
    "ollama":    {"prefix": "ollama/",    "timeout": 60.0},  # local models are slower
}

_MAX_RETRIES = 2
_RETRY_DELAY = 5.0


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

        last_exc: Exception | None = None
        start = time.perf_counter()
        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = await litellm.acompletion(**kwargs)
                break
            except AuthenticationError as e:
                raise RuntimeError(f"{self.name} authentication failed — check the API key: {e}") from e
            except RateLimitError as e:
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_RETRY_DELAY)
                    last_exc = e
                else:
                    raise RuntimeError(f"{self.name} rate limit exceeded: {e}") from e
            except Exception as e:
                last_exc = e
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_RETRY_DELAY)
        else:
            raise RuntimeError(f"{self.name} request failed: {last_exc}") from last_exc

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

    async def health_check(self) -> bool:
        try:
            response = await self.send("ping")
            return len(response.content) > 0
        except Exception:
            return False
