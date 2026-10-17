# Benchmark

Four models, three full scans each (143 probes per scan), run between 2026-10-13 and 2026-10-15.

| Model | Valid runs | Score (mean, min–max) | jailbreak | prompt injection | data extraction | hallucination | Direct requests complied |
|---|---|---|---|---|---|---|---|
| `groq / openai/gpt-oss-120b` | 3/3 | **97**/100 (97–97) | 0% (0/270) | 7% (3/42) | 0% (0/48) | 6% (3/51) | 2/18 |
| `ollama / llama3.1:8b` | 3/3 | **95**/100 (95–95) | 1% (3/270) | 7% (3/42) | 12% (6/48) | 0% (0/51) | 0/18 |
| `groq / openai/gpt-oss-20b` | 3/3 | **93**/100 (91–95) | 0% (1/270) | 10% (4/42) | 19% (9/48) | 4% (2/51) | 0/18 |
| `ollama / mistral:7b` | 3/3 | **32**/100 (32–32) | 63% (171/270) | 93% (39/42) | 88% (42/48) | 0% (0/51) | 15/18 |

Category columns: share of probes where the attack succeeded, pooled over the three runs. Direct requests
complied: control probes (the attack goal sent without any technique) that the model answered.

## Setup

- Datasets of commit `4d69ae6`, temperature 0, single-turn probes.
- Judge: `qwen/qwen3.8-27b` on Groq, for every run (validated against human labels, see
  [`validation/`](../validation/)).
- `gpt-oss` models served by Groq; `llama3.1:8b` and `mistral:7b` run locally with Ollama (RTX 3060, 100% GPU).
- Every run reached the validity threshold (at least 90% of the probes evaluated in each category).

## Reading the results

- The two `gpt-oss` models and `llama3.1:8b` are close. The gap between `llama3.1:8b` (95) and
  `gpt-oss-20b` (91 to 95 across runs) is within the run-to-run variation of the hosted model, so they rank
  about the same.
- Local models gave the same answers on every run; the Groq-hosted models did not, even at temperature 0.
  This is why each model was run three times.
- `mistral:7b` answers 4 of the 5 harmful goals when asked directly, with no technique at all (15/18 control
  probes over three runs). Its jailbreak rate mostly measures missing guardrails, not the strength of the
  techniques. For the one goal it refuses directly, 8 of the 18 techniques still get an answer.
- The per-goal comparison between direct requests and techniques is in the "Control Baselines" section of each
  report.

## Verdicts re-evaluated after two evaluator fixes

The final human verification of the evaluators ([`validation/`](../validation/)) and a review of the reports
before publication found two flaws in the keyword rules: a refusal quoting the injected phrase ("I cannot
provide a response that includes the phrase 'OVERRIDE SUCCESSFUL'") counted as a successful injection, and a
long response opening with a refusal ("I cannot provide instructions... However, here is a fictional guide")
counted as blocked. Both cases now go to the judge. The stored responses were re-judged without calling the
target models again: 21 verdicts changed out of 1,716. `llama3.1:8b` moved from 86 to 95 (its injection rate
was 36% before the fix) and `mistral:7b` from 34 to 32. Each affected report records this in its metadata.

## Limitations

- Three of the eighteen jailbreak templates frame the request as a hacker story. They were written for
  cybersecurity goals and fit badly with some of the JailbreakBench goals (cannibalism, a 5G/COVID-19 article):
  6 to 9 probes out of 90 per run, the same for every model. Their success rate is probably underestimated.
- The hallucination probes use blatant false premises that every model corrects (0 to 6%), so this category
  does not separate the models.
- 14 to 17 probes per category give wide confidence intervals; see the 95% intervals in each report.
- Small models only, single-turn attacks, fixed prompts.

## Files

One `.redacted.json` and one `.redacted.md` per run. Every jailbreak, hallucination and training-data response
is masked, whatever its verdict: a successful jailbreak is real harmful content (phishing emails, malware,
instructions for violence), a hallucination can be dangerous misinformation, and a training-data extraction can
reproduce copyrighted text. Verdicts can be wrong, so a response judged as blocked is masked too. Prompts,
verdicts and the judge's reasoning are kept, so each verdict can still be followed. See also
[Responsible use](../README.md#responsible-use). The table above can be rebuilt with:

```bash
llm-redteam compare benchmark/
```
