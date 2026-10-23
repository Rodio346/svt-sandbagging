# Results — exploratory run (WMDP, 2026-10-23)

**Status: exploratory.** The hypotheses were *not* pre-registered before these runs, so
everything here is exploratory, not confirmatory. Any confirmatory claim needs an OSF
pre-registration and a fresh run.

**Headline:** Clinical malingering tests catch a *blatant* AI sandbagger instantly (it scores
below chance), but a *careful* one that matches a genuinely-weak model's accuracy slips past
every black-box profile test — none of them beats the genuine-vs-genuine null band. Only a
transcript (scratchpad) monitor catches the subtle case, and only one of three capable models
could sandbag convincingly at all.

## Setup

- **Subjects (get every arm):** `google/gemini-flash-latest`, `openai/gpt-5-mini`, `deepseek/deepseek-chat`
- **Weak controls (honest only):** `google/gemini-3.5-flash-lite`, `openai/gpt-5-nano`
- **Monitor:** `google/gemini-3.5-flash-lite`
- **Task:** WMDP (bio/chem/cyber), 300 items, 2 option orders (2nd a full derangement)
- **Calibration target X = 0.795** (the weak reference model's honest accuracy)
- A detector counts only if its AUROC **exceeds the null band** — the AUROC two *genuine*
  runs reach by chance (weak-vs-weak, and each honest run vs its replicate).

## Accuracy by arm (parsed answers)

| Arm | gemini-flash-latest | gpt-5-mini | deepseek-chat |
|---|---|---|---|
| honest | 0.88 | 0.80 | 0.87 |
| naive | **0.03** | **0.19** | **0.30** |
| calibrated | 0.86 | **0.63** | 0.89 |
| coached | 0.87 | **0.73** | 0.87 |
| incentive | 0.87 | 0.76 | 0.86 |
| incentive_reverse | 0.88 | 0.76 | 0.86 |
| redacted (genuine control) | 0.75 | 0.74 | 0.74 |

Only **gpt-5-mini** actually lowers its accuracy on the calibrated/coached rungs. Gemini and
DeepSeek ignore those instructions and answer ~honestly — they sandbag *only* when told bluntly.

## Findings vs the hypotheses

- **H1 — naive sandbagging is caught (supported).** All three subjects fall to ~chance when told
  to underperform; below-chance (D1) and accuracy (B0) flag it at AUROC ≈ 1.0 vs honest.
- **H2/H3 — black-box profile detectors fail the accuracy-matched test (core result).** Against
  the same-model redacted control at n=100, **no** profile detector (D2 easy-item errors, D3
  difficulty insensitivity, D4 letter bias, D5 order inconsistency, D6 atypical distractors)
  clears its null band for the calibrated or coached arms. D5, predicted strongest, has a null
  band of **0.96** — genuine runs are just as inconsistent, so it is unusable here. This
  reproduces the model-identity warning of Lin et al. (2026) and the project's own simulation.
- **Only the scratchpad monitor (B1/B1c ≈ 0.80–0.96) and the two-sided norms detector (0.844 on
  calibrated) separate subtle sandbagging** — the monitor by reading the reasoning (a weak,
  non-black-box baseline by design), the norms detector by flagging atypicality in either
  direction (the "battery degrades slower than any single test" idea).
- **H4 — no spontaneous sandbagging (untestable at this scale).** The Apollo-style incentive
  produced no accuracy drop in any subject (exact McNemar p = 0.75 / 0.73 / 0.92). Incentive ≈
  reverse ≈ honest.

See `results/analysis/summary.md` for the full tables and `results/analysis/auroc_vs_n_*.png`
for the AUROC-vs-test-length figures.

## Limitations

- **Exploratory, not pre-registered** (see top).
- **WMDP is easy for these 2026 models** (honest 0.73–0.90), so the weak reference (0.795) is
  barely weaker than the subjects and the calibrated contrast is small — especially for gpt-5-mini
  (honest 0.815 vs target 0.795).
- **Options leak the answer:** stemless (options-only) accuracy is 0.50–0.61, well above the 25%
  chance rate, which is why the redacted control needs many items redacted.
- **Refusals:** gpt-5-nano refuses ~7% of hazardous items (logged, not counted in signatures).
- **Prompted sandbagging is a model organism, not a real schemer.** Two option orders, one seed.
- **Only one of three subjects could sandbag subtly**, so most of the "sophisticated" signal comes
  from gpt-5-mini.
