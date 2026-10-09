# llm-redteam

Automated adversarial testing framework for Large Language Models.

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
