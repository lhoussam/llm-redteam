import asyncio
import os
import time
import litellm
from litellm.exceptions import AuthenticationError, RateLimitError

from .base import BaseTarget, TargetResponse

_MAX_RETRIES = 2
_RETRY_DELAY = 3.0


class OllamaTarget(BaseTarget):
    name = "ollama"

    def __init__(self, model: str = "llama3.2", timeout: float = 60.0) -> None:
        self.model = model
        self.timeout = timeout
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    async def send(self, prompt: str, system_prompt: str | None = None) -> TargetResponse:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        last_exc: Exception | None = None
        start = time.perf_counter()
        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = await litellm.acompletion(
                    model=f"ollama/{self.model}",
                    messages=messages,
                    timeout=self.timeout,
                    api_base=self.base_url,
                    temperature=0,
                )
                break
            except AuthenticationError as e:
                raise RuntimeError(f"Ollama authentication error: {e}") from e
            except RateLimitError as e:
                raise RuntimeError(f"Ollama rate limit: {e}") from e
            except Exception as e:
                last_exc = e
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_RETRY_DELAY)
        else:
            raise RuntimeError(f"Ollama request failed: {last_exc}") from last_exc

        latency_ms = (time.perf_counter() - start) * 1000
        content = response.choices[0].message.content or ""
        tokens_used = response.usage.total_tokens if response.usage else None

        return TargetResponse(
            content=content,
            model=response.model or f"ollama/{self.model}",
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
