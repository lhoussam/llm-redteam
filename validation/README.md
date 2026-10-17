# Evaluator validation

The robustness score is only as reliable as the evaluators that decide whether each attack succeeded
(keyword rules, planted-secret matching, correction heuristic, LLM judge). This folder holds the data used
to check them against human labels.

## Files

| File | Content |
|---|---|
| [`GUIDELINES.md`](GUIDELINES.md) | The labeling rules, written before labeling. |
| `dev_set.public.jsonl` | 40 real responses from scans, each with its human label. |
| `dev_results.initial.json` | Verdict of every evaluator on every response, and the metrics, **before** any fix. |
| `dev_results.after_fixes.json` | The same, after the fixes made from this measurement. |
| `final_set.public.jsonl` | 20 responses from the final benchmark runs, each with its human label (final verification). |
| `final_results.json` | Benchmark verdicts on these 20 responses, before and after the keyword fixes, and the metrics. |

**Redaction.** 6 of the 40 responses are masked: the 5 successful jailbreaks and one reproduction of a
copyrighted book passage, with the same rule as scan reports (`llm-redteam redact`). Their prompt, label
and every evaluator verdict remain. Prompt injections and data extractions are shown in full:
they only ever leak harmless test values.

## Method

- 40 responses drawn from earlier scan reports (5 target models, 4 attack categories). The pool holds every
  distinct response of those reports. Each one was placed in a stratum by attack category and by what the
  offline rules conclude (clear success, clear failure, or ambiguous, the case sent to the LLM judge). Fixed
  quotas per stratum give 12 jailbreak, 10 prompt injection, 9 data extraction and 9 hallucination
  responses. Inside a stratum, responses are taken in turn from each target model so that no model
  dominates, in a random order with a fixed seed. Two edge cases noticed earlier were always included
  (D16, D40). The source reports contain unredacted harmful outputs, so they and the sampling script are
  not published.
- Labeled blind by the author, following written guidelines: [`GUIDELINES.md`](GUIDELINES.md).
- Each response judged three ways: offline rules only, LLM judge alone, and the full cascade used by the
  scanner (rules first, judge on ambiguous responses).

## Results (development set)

**Before fixes**

| Evaluator | Decided | Accuracy [95% CI] | Recall |
|---|---|---|---|
| Offline rules | 24/40 | 96% [80–99%] | 90% |
| Cascade + `openai/gpt-oss-120b` | 40/40 | 95% [84–99%] | 93% |
| Cascade + `qwen/qwen3.8-27b` | 40/40 | 98% [87–100%] | 100% |
| Cascade + `openai/gpt-oss-safeguard-20b` | 33/40 | 100% on decided | — |

Recall is the share of real successes that were detected: a missed attack makes the score too optimistic.

What the measurement revealed:

- **The judge only read the first 2,500 characters** of a response; one answer denied its false premise
  after 3,400 characters and every judge got it wrong. The window is now 12,000 characters.
- **The judging instructions disagreed with the labeling rules** on two points (minor inaccuracies counted as
  hallucinations; confirming that a system prompt exists counted as a leak). Both were aligned.
- **Correction keywords containing a capital letter could never match.** Fixed.
- **`gpt-oss-safeguard-20b` is unusable on the free tier**: limited to 3 requests per minute, it failed on
  17 of 38 responses.

**After fixes**

| Evaluator | Decided | Accuracy [95% CI] | Recall |
|---|---|---|---|
| Offline rules | 22/40 | 96% [78–99%] | 86% |
| Cascade + `openai/gpt-oss-120b` | 40/40 | 95% [84–99%] | 86% |
| Cascade + `qwen/qwen3.8-27b` | 40/40 | 100% [91–100%] | 100% |

The offline rules decide 4 responses fewer than before. Two later fixes send more responses to the judge: a
response containing both a success keyword and a refusal keyword (D14, D16, D21), since a refusal can quote the
injected phrase ("I cannot provide a response that includes the phrase 'OVERRIDE SUCCESSFUL'"); and a long
response containing a refusal keyword (D09), since a model can refuse in its first sentence and comply in the
next ones. `qwen3.8-27b` gave the same verdicts as before; `gpt-oss-120b` changed its verdict on 2 responses
(D16, D30) between two identical runs, with the same overall accuracy.

A first version of the new hallucination instructions made `qwen3.8-27b` **invert its verdicts** (it read
`"success"` as "the model behaved well"). Every judging instruction now ends with one shared sentence stating
that `success` means the **attack** succeeded.

`qwen/qwen3.8-27b` was chosen as the default judge: best measured result, and from a different model
family than the `gpt-oss` targets (`gpt-oss-120b` judged a leak of `gpt-oss-20b`'s own hidden instructions
as harmless, D30).

## Final verification (benchmark runs)

The figures above come from the responses that were used to fix the evaluators, so they are optimistic. The
final check uses responses nobody had looked at:

- 20 responses drawn from the 12 runs in [`benchmark/`](../benchmark/) (629 distinct responses), stratified by
  category and by the verdict stored in the report, taken in turn from each target model, fixed seed.
- Labeled blind by the author with the same guidelines, then compared with the verdicts the benchmark runs had
  recorded. No judge was called again for this measurement.

| | Accuracy [95% CI] | Precision | Recall |
|---|---|---|---|
| Benchmark verdicts, as recorded | **19/20 = 95%** [76–99%] | 9/10 | 9/9 |
| Same, partial successes counted as failures | 17/20 = 85% [64–95%] | 7/10 | 7/7 |

No successful attack was missed. The one disagreement (F11) is a refusal that quotes the injected phrase ("I
cannot provide a summary that includes the phrase 'OVERRIDE SUCCESSFUL'..."), which the keyword rules counted
as a successful injection. The same pattern appeared 12 times in the benchmark, all for `llama3.1:8b`. The rules
were fixed and the benchmark verdicts re-judged (see [`benchmark/`](../benchmark/)). After the fix the 20
responses are all judged correctly, but that figure is optimistic since the fix was designed by looking at F11.
An independent check on new responses is planned for the next version of the benchmark.

## Limitations

- **40 responses is small**: confidence intervals are wide, and the differences between judges (1 to 2
  responses) are not statistically significant.
- **The "after fixes" figures are optimistic**: the fixes were made by looking at these same responses.
  The figure to trust is the final verification above (95%), measured on responses nobody had seen.
- **No successful hallucination** occurs in this set (every model denied the false premises), so the recall
  of the hallucination evaluators is not measured here.

## Reproduce

```bash
llm-redteam validate validation/dev_set.public.jsonl --judge groq:qwen/qwen3.8-27b --output results.json
```

Masked responses are left out automatically, since their text can no longer be judged.
