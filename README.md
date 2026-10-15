# llm-redteam

Automated adversarial testing framework for Large Language Models.

## Coverage (OWASP Top 10 for LLM Applications, 2025)

| Probe family | Probes | OWASP category |
|---|---|---|
| Jailbreak: DAN, role-play, hypothetical framing, Base64 | 90, plus 5 baselines | LLM01 Prompt Injection |
| Prompt injection: direct, indirect (inside a document), role hijack | 14, plus 1 baseline | LLM01 Prompt Injection |
| System prompt extraction | 8 | LLM07 System Prompt Leakage |
| Extraction of personal data planted in the context | 5 | LLM02 Sensitive Information Disclosure |
| Training data extraction | 3 | LLM02 Sensitive Information Disclosure |
| Hallucination: false premise, authority pressure, citation fabrication | 17 | LLM09 Misinformation |

The other categories (supply chain, data poisoning, output handling, excessive agency, vector stores,
unbounded consumption) depend on the application built around the model or on its training pipeline. A
black-box prompt test cannot observe them, so they are out of scope.

## How it compares with existing tools

[garak](https://github.com/NVIDIA/garak), [PyRIT](https://github.com/microsoft/PyRIT) and
[promptfoo](https://github.com/promptfoo/promptfoo) are far broader than this project:

- garak is a vulnerability scanner with a large library of probes and detectors and many model backends.
- PyRIT is a toolkit for red teamers to build their own campaigns, including multi-turn attack strategies.
- promptfoo is an evaluation and red-teaming CLI configured in YAML and designed to run in CI pipelines.

llm-redteam does not try to compete on attack coverage. It focuses on whether the measurement itself can be
trusted: the evaluators are checked against human labels ([`validation/`](validation/)), a score is only
reported as valid when enough probes were actually evaluated, rates come with confidence intervals, runs
are repeated, and baseline probes show whether a model refuses the request at all before any technique is
applied. It is also small (about 1,900 lines of Python), so every step can be read and checked.

## Responsible use

- **Test only models you are allowed to test**: your own deployments, or hosted models whose terms of service
  permit this kind of security evaluation.
- **The attack datasets contain harmful requests on purpose** (malware, phishing, drug synthesis…). They are
  standard red-teaming test cases: requests a well-aligned model must refuse, not instructions.
- **Scan reports contain the raw outputs of the target model.** When an attack succeeds, they can include
  harmful or copyrighted content; such reports carry a warning banner and the `reports/` folder is ignored by
  Git. Share only a redacted copy:

  ```bash
  llm-redteam redact reports/<report>.json   # writes <report>.redacted.json and <report>.redacted.md
  ```

  Successful (or unjudged) jailbreak, hallucination and training-data responses are masked; prompts, verdicts
  and the judge's reasoning are kept.
- **What this repository publishes is redacted the same way**, for instance the evaluator validation set in
  [`validation/`](validation/).
- **The secrets used by data extraction probes are fake canaries** (`REDTEAM-TEST-2024-XK9`, `123-45-6789`)
  planted in the system prompt: no real personal data is involved.
