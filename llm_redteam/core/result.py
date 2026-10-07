import uuid
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class AttackStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"
    SKIPPED = "skipped"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AttackResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    attack_name: str
    attack_category: str
    severity: Severity
    prompt: str
    response: str
    status: AttackStatus
    confidence: float = 0.0
    evaluator_reason: str = ""
    evaluator_type: str = ""
    latency_ms: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SessionReport(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    target_model: str
    target_provider: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    total_attacks: int = 0
    successful_attacks: int = 0
    robustness_score: float = 100.0
    results: list[AttackResult] = []
    summary: dict = {}
    metadata: dict = {}
