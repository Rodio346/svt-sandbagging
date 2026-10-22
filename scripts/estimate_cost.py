"""Estimate API cost of the plan in configs/study.yaml.

    python scripts/estimate_cost.py                      # from assumed token averages
    python scripts/estimate_cost.py --from-logs logs/    # re-estimate with pilot token usage
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

# Windows consoles default to cp1252, which cannot encode the characters below.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def cost(calls: int, tin: float, tout: float, price: list[float]) -> float:
    return calls * (tin * price[0] + tout * price[1]) / 1e6


def pilot_tokens(log_dir: str) -> tuple[float, float]:
    from inspect_ai.log import list_eval_logs, read_eval_log

    tin = tout = n = 0
    for info in list_eval_logs(log_dir):
        log = read_eval_log(info)
        for s in log.samples or []:
            u = (s.model_usage or {}).get(log.eval.model)  # subject only, not the monitor
            if u is None:
                continue
            tin += u.input_tokens
            tout += u.output_tokens  # already includes reasoning tokens in Inspect
            n += 1
    if not n:
        raise SystemExit("no token usage found in logs")
    return tin / n, tout / n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(Path(__file__).parents[1] / "configs" / "study.yaml"))
    ap.add_argument("--from-logs")
    a = ap.parse_args()
    c = yaml.safe_load(Path(a.config).read_text())
    t = c["tokens"]
    if a.from_logs:
        t["input"], t["output_subject"] = pilot_tokens(a.from_logs)
        print(f"pilot averages: {t['input']:.0f} in / {t['output_subject']:.0f} out per call")

    per = c["n_items"] * c["n_perms"]
    lines = []
    subj_calls = 0
    for m in c["subjects"]:
        calls = per * len(c["arms"]) + c.get("stemless_items", 0) * c["n_perms"]
        subj_calls += calls
        lines.append((m["id"], calls, cost(calls, t["input"], t["output_subject"], m["price"])))
    for m in c["weak_controls"]:
        lines.append((m["id"], per, cost(per, t["input"], t["output_weak"], m["price"])))
    f = c.get("frontier_spotcheck")
    if f:
        calls = f["n_items"] * c["n_perms"] * len(f["arms"])
        subj_calls += calls
        lines.append((f["id"] + " (spot-check)", calls, cost(calls, t["input"], t["output_subject"], f["price"])))
    mon = c["monitor"]
    lines.append((mon["id"] + " (monitor)", subj_calls,
                  cost(subj_calls, t["monitor_input"], t["monitor_output"], mon["price"])))
    p = c["pilot"]
    pilot_calls = p["n_items"] * c["n_perms"] * 7 * p["models"]  # 6 conditions + stemless
    pilot_cost = cost(pilot_calls, t["input"], t["output_subject"], c["subjects"][0]["price"])
    lines.append(("pilot (arith)", pilot_calls, pilot_cost))

    total = sum(x[2] for x in lines)
    print(f"{'item':48s} {'calls':>7s} {'USD':>8s}")
    for name, calls, usd in lines:
        print(f"{name:48s} {calls:7d} {usd:8.2f}")
    buf = 0.3 * total
    print(f"{'subtotal':48s} {'':7s} {total:8.2f}")
    print(f"{'30% buffer (re-runs, longer reasoning)':48s} {'':7s} {buf:8.2f}")
    gbp = (total + buf) * c["usd_to_gbp"]
    print(f"{'TOTAL':48s} {'':7s} {total + buf:8.2f}  ≈ £{gbp:.2f}")


if __name__ == "__main__":
    main()
