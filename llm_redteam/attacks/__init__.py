from .base import BaseAttack
from .data_extraction import DataExtractionAttack
from .hallucination import HallucinationAttack
from .jailbreak import JailbreakAttack
from .prompt_injection import PromptInjectionAttack

ATTACK_REGISTRY: dict[str, type[BaseAttack]] = {
    "jailbreak": JailbreakAttack,
    "prompt_injection": PromptInjectionAttack,
    "data_extraction": DataExtractionAttack,
    "hallucination": HallucinationAttack,
}

__all__ = [
    "BaseAttack", "ATTACK_REGISTRY",
    "JailbreakAttack", "PromptInjectionAttack", "DataExtractionAttack", "HallucinationAttack",
]
