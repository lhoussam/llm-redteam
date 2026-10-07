# test_phase1.py
# Lance avec : python test_phase1.py
# Nécessite OPENAI_API_KEY dans .env

import asyncio
from dotenv import load_dotenv
load_dotenv()

from llm_redteam.targets.openai_target import OpenAITarget
from llm_redteam.core.result import AttackResult, AttackStatus, Severity, SessionReport


async def main():
    # Test 1 : Modèles Pydantic
    print("Test 1 — Modèles Pydantic...")
    result = AttackResult(
        attack_name="test",
        attack_category="jailbreak",
        severity=Severity.HIGH,
        prompt="Hello",
        response="Hi",
        status=AttackStatus.SUCCESS,
    )
    report = SessionReport(target_model="gpt-4o-mini", target_provider="openai")
    report.results.append(result)
    assert report.total_attacks == 0  # pas encore calculé, normal
    print(f"  ✓ AttackResult créé : {result.id[:8]}...")
    print(f"  ✓ SessionReport créé : {report.session_id[:8]}...")

    # Test 2 : Target health check
    print("\nTest 2 — OpenAI health check...")
    target = OpenAITarget(model="gpt-4o-mini")
    ok = await target.health_check()
    assert ok, "health_check() a retourné False"
    print(f"  ✓ health_check() OK")

    # Test 3 : Envoi d'un prompt
    print("\nTest 3 — Envoi d'un prompt...")
    response = await target.send("Say exactly: PHASE1_OK")
    assert len(response.content) > 0
    assert response.latency_ms > 0
    print(f"  ✓ Réponse reçue en {response.latency_ms:.0f}ms")
    print(f"  ✓ Contenu : {response.content[:80]}")

    print("\n✅ Phase 1 validée !")


if __name__ == "__main__":
    asyncio.run(main())
