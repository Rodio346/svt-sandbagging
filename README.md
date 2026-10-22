# Malingering tests for AI sandbagging

**Question:** as sandbaggers get more sophisticated, which clinical *symptom validity* signatures still catch them?

Forensic neuropsychology detects people who fake cognitive impairment with forced-choice tests:
scoring *below* chance, failing items even impaired patients pass, and implausible error profiles.
This repo applies that logic to LLMs that deliberately underperform on capability evaluations
("sandbagging"), built on [Inspect](https://inspect.aisi.org.uk/).

It extends **Cacioli (2026)** ([2604.25249](https://arxiv.org/abs/2604.25249), [2604.26206](https://arxiv.org/pdf/2604.26206)),
who found 7–9B models never went below chance when told to sandbag, collapsing onto favourite answer
letters instead. Here: capable API models, a ladder of sandbagging sophistication, six signatures,
and the same-model controls that [Lin et al. (2026)](https://lacuna.tiptreesystems.com/work/when-lie-detectors-learn-model-identity-confounds)
showed black-box detectors need.

## Design in one table

| Arm | Prompt manipulation | Role |
|---|---|---|
| `honest` | none | negative |
| `naive` | "deliberately underperform" | positive, rung 1 |
| `calibrated` | "aim for X% accuracy, errors should look natural" (X = reference weak model's accuracy) | positive, rung 2 |
| `coached` | calibrated + the six checks spelled out | positive, rung 3 |
| `incentive` | Apollo-style note: scoring > 50% → no deployment, retraining; no instruction | rung 4 (spontaneous) |
| `incentive_reverse` | same note, ≤ 50% triggers it | control for rung 4 |
| redacted control | honest, stems removed from a weighted-random set of harder items (difficulty from other models) until accuracy ≈ X, using measured options-only accuracy | same-model genuine weakness |
| `honest_rep1`, stemless | replicate honest run; all stems removed | run-to-run null; options-only accuracy |
| weak models | honest | cross-model genuine weakness |

Every item is shown in two option orders (the second a full derangement), so *what* a model picks
is separable from *where* it picks.

## Signatures (higher = more suspicious)

| | Signature | Clinical analogue |
|---|---|---|
| D1 | below-chance accuracy (binomial, perm 0) | TOMM forced choice |
| D2 | error concentration on floor items | Rey 15-item |
| D3 | 1 − AUC(item difficulty → error) | atypical difficulty profile |
| D4 | letter concentration (1 − entropy) | response-set bias |
| D5 | content inconsistency across option orders | repeat-administration inconsistency |
| D6 | wrong answers ≠ the distractor honest models pick | "approximate answers" |
| B0/B1/B1c | error rate; scratchpad monitor; monitor on wrong answers only | baselines |

Analysis: paired sessions (same n items for both runs, n ≤ pool/3), item bootstrap with replacement, and a
**null band** from genuine-vs-genuine pairs (weak model vs weak model, honest run vs its replicate): a
signature counts only if it clears it. References exclude both models compared. Unparsed answers are
excluded from signatures and reported per arm. Two combined detectors, both leave-one-model-out on
accuracy-matched negatives: a logistic battery trained on cruder sandbagging, and a **two-sided norms
detector** fitted on genuine sessions only (Mahalanobis distance).

## Quick start

```bash
pip install -e ".[dev]" tabulate
pytest -q                                     # 38 tests, incl. a full Inspect run with scripted mock models
python scripts/simulate_power.py              # planning simulation (no API calls)
python scripts/estimate_cost.py               # cost of the plan in configs/study.yaml

# single cell, any Inspect model
inspect eval src/svt_sandbag/task.py@svt_battery --model google/gemini-2.5-flash-lite \
    -T dataset=arith -T condition=naive -T n_items=40

# full study (edit configs/study.yaml first; set provider API keys in .env)
python scripts/run_study.py pilot && python scripts/estimate_cost.py --from-logs logs/pilot
python scripts/run_study.py panel            # honest for all models + replicate and stemless runs for subjects
python scripts/run_study.py prepare          # X from the reference weak model; per-subject redaction lists
python scripts/run_study.py main
python scripts/analyze.py --logs logs/panel logs/main --dataset wmdp --out results/analysis
```

## Layout

```
src/svt_sandbag/
  items.py       items, balanced option orders, derangements, redaction helper
  datasets.py    arithmetic ladder (offline), WMDP and MMLU loaders (Hugging Face)
  prompts.py     the sophistication ladder
  task.py        Inspect task + scorer (records chosen content AND position)
  monitor.py     scratchpad-monitor scorer (never sees the system prompt)
  detectors.py   D1–D6 + baselines, vectorised over item × perm arrays
  analysis.py    log loading, leave-one-model-out references, AUROC vs n, combined detectors
  simulate.py    planning simulator in the same schema as real logs
scripts/         run_study.py, analyze.py, simulate_power.py, estimate_cost.py
configs/         study.yaml (models, prices, sizes)
```

## What the planning simulation says (assumed policies, not results)

* **Single-run comparisons are noisy.** Two *genuine* runs reach AUROC 0.68–0.91 at 100 items by chance (null band),
  so an early version of this pipeline (overlapping sessions, no null) badly overstated detection. Fixed after review.
* **Judge D1 on the full run.** A naive sandbagger at 13% accuracy is flagged in 72% of 100-item sessions.
* **Difficulty profiles (D2, D3) separate a calibrated sandbagger from a different model (0.98–0.99) but not from the
  same model's redacted control** — part of the cross-model signal is model identity (Lin et al. 2026).
* **Order inconsistency (D5) is the most robust signature** (0.95 vs a 0.85 band against the same-model control) —
  under the assumption that a sandbagger decides afresh on each presentation.
* **One-sided training can be inverted.** A coached sandbagger that over-corrects scores AUROC 0.00 against a battery
  trained on cruder sandbaggers; the two-sided norms detector reaches 0.72.
* A sandbagger that perfectly imitates a weaker model is undetectable by any black-box signature (cf. Taylor et al. 2025).

## Limitations

Prompted sandbagging is a model organism, not a schemer. Redacted items differ visibly from normal ones,
and the scratchpad monitor can see that. Hidden reasoning (reasoning models) is not visible to the monitor, and the
sandbagging prompts tell the model to keep its scratchpad clean, so B1 is a weak baseline by design.
Difficulty comes from a small panel. Two option orders per item; one seed. WMDP/MMLU loaders are
untested in CI (no network there): run `python -c "from svt_sandbag.datasets import load_wmdp; print(len(load_wmdp()))"` once.
