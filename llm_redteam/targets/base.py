from abc import ABC, abstractmethod

from pydantic import BaseModel


class TargetResponse(BaseModel):
    content: str
    model: str
    latency_ms: float
    tokens_used: int | None = None
    raw: dict = {}


class BaseTarget(ABC):
    name: str
    model: str

    @abstractmethod
    async def send(self, prompt: str, system_prompt: str | None = None) -> TargetResponse:
        """Send a prompt (with an optional system prompt) to the target and return its response."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the target is reachable and answers."""
        ...
