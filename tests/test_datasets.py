from importlib import resources

import pytest

from llm_redteam.attacks import hallucination, jailbreak, prompt_injection
from llm_redteam.datasets import load_dataset

DATASETS = {
    "jailbreaks": ["goals", *(tech for tech, _ in jailbreak._TECHNIQUES)],
    "injections": ["goals", *prompt_injection._TECHNIQUES],
    "extraction_prompts": ["system_prompt", "training_data", "context_extraction"],
    "hallucination": hallucination._TECHNIQUES,
}


@pytest.mark.parametrize("name", DATASETS)
def test_dataset_ships_inside_the_package(name):
    # Datasets must live in the package so that a regular (non-editable) install includes them.
    assert resources.files("llm_redteam.datasets").joinpath(f"{name}.yaml").is_file()


@pytest.mark.parametrize(("name", "sections"), DATASETS.items())
def test_dataset_sections_are_non_empty_lists_of_prompts(name, sections):
    dataset = load_dataset(name)
    for section in sections:
        prompts = dataset[section]
        assert prompts and all(isinstance(p, str) and p.strip() for p in prompts), section


def test_attack_templates_contain_their_goal_placeholder():
    jailbreaks = load_dataset("jailbreaks")
    for tech, is_base64 in jailbreak._TECHNIQUES:
        placeholder = "{base64_goal}" if is_base64 else "{goal}"
        assert all(placeholder in seed for seed in jailbreaks[tech]), tech

    injections = load_dataset("injections")
    for tech in prompt_injection._TECHNIQUES:
        assert all("{goal}" in seed for seed in injections[tech]), tech


def test_datasets_are_read_once():
    assert load_dataset("jailbreaks") is load_dataset("jailbreaks")


def test_datasets_are_decoded_as_utf8():
    # The datasets must be read as UTF-8 explicitly, whatever the OS: the default encoding is UTF-8 on Linux
    # and macOS but cp1252 on Windows, where it would silently corrupt non-ASCII text (this test catches it there).
    dan_seeds = load_dataset("jailbreaks")["dan"]
    assert any("STAN \u2014 Strive" in seed for seed in dan_seeds)  # em dash
