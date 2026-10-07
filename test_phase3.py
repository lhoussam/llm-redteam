# test_phase3.py — smoke test for all attack modules
# Lance avec : python test_phase3.py

import asyncio
from dotenv import load_dotenv
load_dotenv()

from llm_redteam.targets.groq_target import GroqTarget
from llm_redteam.attacks.jailbreak import JailbreakAttack
from llm_redteam.attacks.prompt_injection import PromptInjectionAttack
from llm_redteam.attacks.data_extraction import DataExtractionAttack
from llm_redteam.attacks.hallucination import HallucinationAttack
from llm_redteam.core.scoring import compute_score, compute_summary, score_label


async def main():
    target = GroqTarget(model="llama-3.1-8b-instant")

    print("Test 1 — Jailbreak...")
    results_jb = await JailbreakAttack().run(target=target)
    assert len(results_jb) > 0
    print(f"  ✓ {len(results_jb)} attaques")

    print("Test 2 — Prompt Injection...")
    results_pi = await PromptInjectionAttack().run(target=target)
    assert len(results_pi) > 0
    print(f"  ✓ {len(results_pi)} attaques")

    print("Test 3 — Data Extraction...")
    results_de = await DataExtractionAttack().run(target=target)
    assert len(results_de) > 0
    print(f"  ✓ {len(results_de)} attaques")

    print("Test 4 — Hallucination...")
    results_ha = await HallucinationAttack().run(target=target)
    assert len(results_ha) > 0
    print(f"  ✓ {len(results_ha)} attaques")

    print("Test 5 — Scoring global...")
    all_results = results_jb + results_pi + results_de + results_ha
    score = compute_score(all_results)
    summary = compute_summary(all_results)
    label = score_label(score)
    assert 0 <= score <= 100
    print(f"  ✓ Score : {score:.1f}/100 {label}")
    for cat, stats in summary.items():
        print(f"  ✓ {cat}: {stats['successful']}/{stats['evaluated']} réussis ({stats['success_rate']:.0f}%)")

    print("\n✅ Tous les tests passent.")
    print("\nCommande de scan complète :")
    print("  llm-redteam scan --target groq --model llama-3.1-8b-instant --judge-model llama-3.3-70b-versatile")


if __name__ == "__main__":
    asyncio.run(main())
