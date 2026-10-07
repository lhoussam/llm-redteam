import os
import time
import litellm
from litellm.exceptions import AuthenticationError, RateLimitError

from .base import BaseTarget, TargetResponse


class OpenAITarget(BaseTarget):
    name = "openai"

    def __init__(self, model: str = "gpt-4o-mini", timeout: float = 30.0) -> None:
        self.model = model
        self.timeout = timeout
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key:
            litellm.openai_key = api_key

    async def send(self, prompt: str, system_prompt: str | None = None) -> TargetResponse:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        start = time.perf_counter()
        try:
            response = await litellm.acompletion(
                model=self.model,
                messages=messages,
                timeout=self.timeout,
                temperature=0,
            )
        except AuthenticationError as e:
            raise RuntimeError(f"OpenAI authentication failed: {e}") from e
        except RateLimitError as e:
            raise RuntimeError(f"OpenAI rate limit exceeded: {e}") from e
        except Exception as e:
            raise RuntimeError(f"OpenAI request failed: {e}") from e

        latency_ms = (time.perf_counter() - start) * 1000
        content = response.choices[0].message.content or ""
        tokens_used = (
            response.usage.total_tokens
            if response.usage
            else None
        )

        return TargetResponse(
            content=content,
            model=response.model or self.model,
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
