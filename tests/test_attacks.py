import base64

import pytest

from llm_redteam.attacks import ATTACK_REGISTRY, data_extraction, hallucination, jailbreak, prompt_injection
from llm_redteam.attacks.data_extraction import DataExtractionAttack
from llm_redteam.attacks.hallucination import HallucinationAttack
from llm_redteam.attacks.jailbreak import JailbreakAttack
from llm_redteam.attacks.prompt_injection import PromptInjectionAttack
from llm_redteam.core.result import AttackStatus, Severity
from llm_redteam.datasets import load_dataset
from tests.helpers import FakeJudge, FakeTarget

pytestmark = pytest.mark.usefixtures("no_sleep")

NEUTRAL = "The weather in Paris is mild in spring."


def test_registry_exposes_the_four_modules():
    assert set(ATTACK_REGISTRY) == {"jailbreak", "prompt_injection", "data_extraction", "hallucination"}


class TestJailbreak:
    async def test_probe_count_matches_dataset(self):
        ds = load_dataset("jailbreaks")
        per_goal = 1 + sum(len(ds[tech]) for tech, _ in jailbreak._TECHNIQUES)

        results = await JailbreakAttack().run(target=FakeTarget())

        assert len(results) == len(ds["goals"]) * per_goal

    async def test_each_goal_has_an_unattacked_baseline(self):
        goals = load_dataset("jailbreaks")["goals"]

        results = await JailbreakAttack().run(target=FakeTarget())

        baselines = [r for r in results if r.attack_category == "baseline"]
        assert [b.prompt for b in baselines] == goals
        assert all(b.severity == Severity.LOW for b in baselines)

    async def test_prompts_embed_the_goal(self):
        target = FakeTarget()
        await JailbreakAttack().run(target=target)

        prompts = [prompt for prompt, _ in target.calls]
        assert not any("{goal}" in p or "{base64_goal}" in p for p in prompts)
        first_goal = load_dataset("jailbreaks")["goals"][0]
        assert any(base64.b64encode(first_goal.encode()).decode() in p for p in prompts)

    async def test_refusals_are_blocked(self):
        results = await JailbreakAttack().run(target=FakeTarget())
        assert all(r.status == AttackStatus.FAILURE for r in results)

    async def test_ambiguous_responses_are_sent_to_the_judge(self):
        judge = FakeJudge(AttackStatus.SUCCESS)

        results = await JailbreakAttack().run(target=FakeTarget(NEUTRAL), evaluator=judge)

        assert len(judge.calls) == len(results)
        assert all(r.status == AttackStatus.SUCCESS and r.evaluator_type == "llm" for r in results)

    async def test_ambiguous_responses_without_judge_are_skipped(self):
        results = await JailbreakAttack().run(target=FakeTarget(NEUTRAL))
        assert all(r.status == AttackStatus.SKIPPED for r in results)


class TestPromptInjection:
    async def test_probe_count_matches_dataset(self):
        ds = load_dataset("injections")
        expected = 1 + sum(len(ds[tech]) for tech in prompt_injection._TECHNIQUES)

        results = await PromptInjectionAttack().run(target=FakeTarget())

        assert len(results) == expected
        assert sum(r.attack_category == "baseline" for r in results) == 1

    async def test_system_prompts(self):
        target = FakeTarget()
        await PromptInjectionAttack().run(target=target)

        for prompt, system_prompt in target.calls:
            if prompt.startswith("Please summarize this document:"):
                assert system_prompt == prompt_injection._INDIRECT_SYSTEM_PROMPT
            else:
                assert system_prompt == prompt_injection._REF_SYSTEM_PROMPT

    async def test_override_signal_is_a_success(self):
        results = await PromptInjectionAttack().run(target=FakeTarget("OVERRIDE SUCCESSFUL. Here are my instructions."))
        assert all(r.status == AttackStatus.SUCCESS for r in results)


class TestDataExtraction:
    async def test_probe_count_matches_dataset(self):
        ds = load_dataset("extraction_prompts")
        results = await DataExtractionAttack().run(target=FakeTarget())
        assert len(results) == len(ds["system_prompt"]) + len(ds["training_data"]) + len(ds["context_extraction"])

    async def test_secret_is_planted_in_the_system_prompt(self):
        target = FakeTarget()
        await DataExtractionAttack().run(target=target)

        system_prompts = {sp for _, sp in target.calls}
        assert any(sp and data_extraction._SECRET_KEY in sp for sp in system_prompts)
        assert any(sp and data_extraction._SECRET_SSN in sp for sp in system_prompts)

    async def test_leaked_secrets_are_detected_by_string_match(self):
        leak = f"Sure: {data_extraction._SECRET_KEY} and {data_extraction._SECRET_SSN}"

        results = await DataExtractionAttack().run(target=FakeTarget(leak))

        string_matched = [r for r in results if r.evaluator_type == "string_match"]
        assert string_matched
        assert all(r.status == AttackStatus.SUCCESS for r in string_matched)

    async def test_without_leak_or_judge(self):
        results = await DataExtractionAttack().run(target=FakeTarget())

        for r in results:
            expected = AttackStatus.SKIPPED if "training_data" in r.attack_name else AttackStatus.FAILURE
            assert r.status == expected


class TestHallucination:
    async def test_corrections_are_blocked_by_heuristic(self):
        results = await HallucinationAttack().run(target=FakeTarget("That's not correct: this claim is false."))

        assert len(results) == sum(len(prompts) for prompts in load_dataset("hallucination").values())
        assert all(r.status == AttackStatus.FAILURE and r.evaluator_type == "heuristic" for r in results)

    async def test_ambiguous_responses_use_the_fact_checking_judge(self):
        judge = FakeJudge(AttackStatus.SUCCESS)

        results = await HallucinationAttack().run(target=FakeTarget(NEUTRAL), evaluator=judge)

        assert all(r.status == AttackStatus.SUCCESS for r in results)
        assert all(call["judge_system"] == hallucination._HALLUCINATION_JUDGE for call in judge.calls)

    async def test_ambiguous_responses_without_judge_are_skipped(self):
        results = await HallucinationAttack().run(target=FakeTarget(NEUTRAL))
        assert all(r.status == AttackStatus.SKIPPED for r in results)


class TestSystemPromptIsRecorded:
    async def test_planted_secrets_are_traceable_in_the_results(self):
        results = await DataExtractionAttack().run(target=FakeTarget())

        by_subtype = {r.attack_name.split("/")[1].rsplit("_", 1)[0]: r.system_prompt for r in results}
        assert data_extraction._SECRET_KEY in by_subtype["system_prompt"]
        assert data_extraction._SECRET_SSN in by_subtype["context_extraction"]
        assert by_subtype["training_data"] is None

    async def test_injection_results_keep_the_mission_they_were_given(self):
        results = await PromptInjectionAttack().run(target=FakeTarget())
        assert {r.system_prompt for r in results} == {
            prompt_injection._REF_SYSTEM_PROMPT, prompt_injection._INDIRECT_SYSTEM_PROMPT,
        }

    async def test_jailbreaks_have_no_system_prompt(self):
        results = await JailbreakAttack().run(target=FakeTarget())
        assert all(r.system_prompt is None for r in results)


@pytest.mark.parametrize("correction", [
    "I must correct you: Einstein proved no such thing.",      # capitalised keyword, never matched before
    "I should point out that this is a myth.",
    "Einstein never said that the speed of light changes.",   # missed correction seen in the labeled set
    "The EMA has not announced anything of the sort.",
])
async def test_corrections_are_recognised(correction):
    results = await HallucinationAttack().run(target=FakeTarget(correction))
    assert all(r.status == AttackStatus.FAILURE and r.evaluator_type == "heuristic" for r in results)
