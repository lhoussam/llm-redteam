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
        """Envoie un prompt à la cible et retourne la réponse."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Vérifie que la cible est accessible."""
        ...
