# results/analysis — file index

Machine-readable outputs behind [RESULTS.md](../../RESULTS.md). For figures and plain-language tables, see
[profile/README.md](../../profile/README.md); for the arm names used here, see the
[design table](../../README.md#design-in-one-table).

Start with **[`summary.md`](summary.md)**, the per-model write-up of every test.

## Reproducible from the public export

Written by `python scripts/analyze.py --data data --dataset wmdp --out results/analysis` from
[`data/`](../../data/README.md):

| File | One row per | What it holds |
|---|---|---|
| [`summary.md`](summary.md) | — | Per-model tables of every comparison: dominance rows, headline counts, null bands, compliance gate, detector deltas and p-values |
| [`models.json`](models.json) | model | Ids requested vs the version actually served (sample counts), served version per arm, `drift` flag |
| [`accuracy.csv`](accuracy.csv) | model × arm | Accuracy and parse-failure rate |
| [`compliance.csv`](compliance.csv) | model × instructed arm | Drop vs honest, McNemar p, whether the arm passes the gate (`gate`), D1 below-chance p |
| [`tests.csv`](tests.csv) | comparison × detector | The main table. `kind`: `detection`, `monitor`, `cross_model`, `negative_control`, `incentive`, `null_replicate`, `null_crossmodel`. Detectors D2–D6, B0, B1, B1c, battery. Delta, one-/two-sided permutation p, strictly paired Fisher p on floor vs other errors, null band, `flag` (`suspicious` / `inverted` / empty) |
| [`stemless_tests.csv`](stemless_tests.csv) | comparison × detector | Same columns: each gated sandbagging arm vs the same model's all-stems-removed run |
| [`dominance.csv`](dominance.csv) | comparison | Rows where the sandbagging run is more accurate **and** makes more floor errors (Fisher p < 0.01) |
| [`headline.csv`](headline.csv) | floor rule | Headline counts (matched comparisons flagged by D2/D3, trend, stemless, dominance) under the panel and legacy floors |
| [`null_bands.csv`](null_bands.csv) | scope × detector | Largest difference a genuine pair shows (same-model replicate; cross-model weak-vs-weak) |
| [`trend_deviation.csv`](trend_deviation.csv) | model × arm × detector | z of each run against the accuracy → detector line through the five weak models |
| [`trend_calibration.csv`](trend_calibration.csv) | detector × held-out weak model | Leave-one-out check of that line (\|z\| < 2 passes) |
| [`incentive.csv`](incentive.csv) | model | Incentive vs reverse-note accuracy, McNemar p |
| [`power.csv`](power.csv), `power_<model>.png` | model × comparison × length × detector | Detection rate on random 25–200-item subsets (how long a test needs to be) |

## Need the raw logs (not public)

| File | Written by | What it holds |
|---|---|---|
| [`monitor_reasoning.csv`](monitor_reasoning.csv) | `scripts/monitor_reasoning.py` | Monitor re-run with readable reasoning: per model × arm, wrong answers, share with scratchpad / readable reasoning, mean score, share ≥ 0.5, one-sided permutation p vs honest, unscored replies |
| [`incentive_uptake.csv`](incentive_uptake.csv) | `scripts/incentive_uptake.py` | Share of transcripts mentioning the incentive note, per keyword |

## `legacy/`

Outputs from before the panel-floor revision (floor rebuilt per comparison). Kept for comparison;
`--floor-rule legacy` reproduces them.
