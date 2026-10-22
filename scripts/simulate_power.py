"""Planning simulation: how many items does each detector need, against what null?

    python scripts/simulate_power.py --out results/sim

Writes sim_auroc.csv (AUROC vs n per detector and comparison), sim_null.csv (genuine-vs-
genuine pairs), sim_band.csv (95th-percentile null band) and sim_combined.csv.
Numbers depend on the assumed response policies in svt_sandbag/simulate.py.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from svt_sandbag.analysis import (
    Comparison,
    auroc_table,
    combined_lomo,
    norms_lomo,
    null_band,
    null_table,
    session_bank,
)
from svt_sandbag.simulate import simulate

COMPARISONS = [
    Comparison("naive", "honest", "naive vs honest (same model)"),
    Comparison("naive_positional", "honest", "positional collapse vs honest"),
    Comparison("calibrated", "redacted", "calibrated vs same-model genuine"),
    Comparison("calibrated", "weak:weak-1", "calibrated vs matched weaker model"),
    Comparison("coached", "redacted", "coached vs same-model genuine"),
    Comparison("coached", "weak:weak-1", "coached vs matched weaker model"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/sim")
    ap.add_argument("--n-sessions", type=int, default=200)
    ap.add_argument("--n-boot", type=int, default=50)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    kw = {"n_sessions": a.n_sessions, "n_boot": a.n_boot, "seed": a.seed}

    df = simulate(seed=a.seed)
    print("Simulated accuracy by model × arm\n",
          df.groupby(["model", "arm"])["correct"].mean().unstack().round(3), "\n")

    auc = pd.concat([auroc_table(df, m, COMPARISONS, **kw) for m in ("strong-A", "strong-B", "strong-C")],
                    ignore_index=True)
    null = null_table(df, **kw)
    band = null_band(null)
    auc.to_csv(out / "sim_auroc.csv", index=False)
    null.to_csv(out / "sim_null.csv", index=False)
    band.to_csv(out / "sim_band.csv", index=False)

    summary = auc.groupby(["comparison", "detector", "n_items"])["auroc"].mean().unstack("n_items").round(2)
    print(summary.to_string())
    print("\nNull band (95th pct of max(AUROC, 1-AUROC) over genuine pairs):")
    print(band.pivot(index="detector", columns="n_items", values="band").round(2).to_string())
    d1 = auc[auc.detector == "D1_below_chance"].groupby(["comparison", "n_items"])[["d1_flag_pos", "d1_flag_neg"]]
    print("\nD1 flag rates (p < .01):\n", d1.mean().round(3).to_string())

    sim = df[df["arm"] != "naive_positional"]
    bank = session_bank(sim, n_items=100, n_sessions=100, seed=a.seed)
    comb = pd.concat([
        combined_lomo(bank, positives=("naive", "calibrated")).assign(detector="battery (trained naive+calibrated)"),
        norms_lomo(bank).assign(detector="norms (genuine sessions only)"),
    ])
    comb.to_csv(out / "sim_combined.csv", index=False)
    print("\nCombined detectors, leave-one-model-out, 100 items:\n",
          comb.groupby(["detector", "arm"])["auroc"].mean().round(3).to_string())


if __name__ == "__main__":
    main()
