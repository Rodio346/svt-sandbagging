"""Run the study in phases (each phase is resumable: Inspect's eval_set skips finished tasks).

    python scripts/run_study.py pilot      # arithmetic, 40 items, 2 subjects, all arms + stemless
    python scripts/run_study.py panel      # honest arm for every model; subjects also get a replicate
                                           # honest run (null) and a stemless run (options-only accuracy)
    python scripts/run_study.py prepare    # X = reference weak model's accuracy; redaction lists
    python scripts/run_study.py main       # sandbagging arms + redacted control, with monitor
    python scripts/run_study.py spotcheck  # optional frontier model

Models, prices and sizes live in configs/study.yaml. Logs go to logs/<phase>/ and the
analysis reads logs/panel + logs/main filtered to the main dataset.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml
from inspect_ai import eval_set

from svt_sandbag.analysis import load_logs, redaction_ids
from svt_sandbag.task import svt_battery

ROOT = Path(__file__).parents[1]

# Load .env with override=True so the project's key wins over a stale system
# environment variable. python-dotenv (and Inspect's auto-load) do NOT override
# an existing env var, so an old GOOGLE_API_KEY/OPENAI_API_KEY exported system-wide
# would silently shadow the one in .env and send requests on the wrong account.
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass
INSTRUCTED = ("naive", "calibrated", "coached")


def cfg() -> dict:
    return yaml.safe_load((ROOT / "configs" / "study.yaml").read_text())


def slug(model: str) -> str:
    return model.replace("/", "_").replace(":", "_")


def run(tasks, models, log_dir: Path, max_connections: int = 10, max_tasks: int = 1, **kw):
    """Run a set of tasks with a HARD concurrency cap.

    ``eval_set`` does not accept ``max_connections`` (it is silently swallowed by **kwargs),
    so without this the run falls back to Inspect's adaptive concurrency (start=20, max=100).
    That bursts past providers' per-minute limits; a few transient 429s then send Inspect's
    retry backoff to ~25-minute waits and every connection freezes at once. We set the cap on
    each task's GenerateConfig (where it actually takes effect) and run arms one at a time.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    for t in tasks:
        t.config.max_connections = max_connections
    ok, _ = eval_set(tasks, log_dir=str(log_dir), model=models, retry_attempts=3,
                     max_tasks=max_tasks, **kw)
    if not ok:
        raise SystemExit(f"some tasks did not finish; re-run the same command to resume ({log_dir})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["pilot", "panel", "prepare", "main", "spotcheck"])
    ap.add_argument("--max-connections", type=int, default=10)
    a = ap.parse_args()
    c = cfg()
    subjects = [m["id"] for m in c["subjects"]]
    weak = [m["id"] for m in c["weak_controls"]]
    ref_model = c["calibration_reference"]
    mon = c["monitor"]["id"]
    common = {"n_items": c["n_items"], "n_perms": c["n_perms"], "dataset": c["dataset"]}
    res = ROOT / "results"
    res.mkdir(exist_ok=True)
    mc = {"max_connections": a.max_connections}

    if a.phase == "pilot":
        p = c["pilot"]
        tasks = [svt_battery(dataset=p["dataset"], n_items=p["n_items"], condition=cond)
                 for cond in ("honest", *INSTRUCTED, "incentive", "incentive_reverse")]
        tasks.append(svt_battery(dataset=p["dataset"], n_items=p["n_items"], redact_fraction=1.0))
        run(tasks, subjects[: p["models"]], ROOT / "logs" / "pilot", **mc)
        print("Next: python scripts/estimate_cost.py --from-logs logs/pilot ; check parse rate, compliance, "
              "and that stemless accuracy on arith is near 25%.")

    elif a.phase == "panel":
        # separate sub-folders: eval_set refuses a log dir holding another set's logs
        run([svt_battery(condition="honest", monitor_model=mon, **common)], subjects,
            ROOT / "logs" / "panel" / "subjects", **mc)
        run([svt_battery(condition="honest", **common)], weak, ROOT / "logs" / "panel" / "weak", **mc)
        stemless = svt_battery(condition="honest", redact_file=str(_all_ids_file(c)),
                               **{**common, "n_items": c["stemless_items"]})  # every stem removed
        run([svt_battery(condition="honest", replicate=1, monitor_model=mon, **common), stemless],
            subjects, ROOT / "logs" / "panel" / "extra", **mc)

    elif a.phase == "prepare":
        df = load_logs(ROOT / "logs" / "panel")
        main_df = df[df["dataset"] == c["dataset"]]
        parsed = main_df[~main_df["parse_failed"].astype(bool)]
        acc = parsed[parsed["arm"] == "honest"].groupby("model")["correct"].mean()
        stemless = parsed[parsed["arm"] == "stemless"].groupby("model")["correct"].mean()
        if ref_model not in acc.index:
            raise SystemExit(f"calibration_reference {ref_model} has no honest run in logs/panel")
        target = float(acc[ref_model])
        redact = {}
        for m in subjects:
            ids = redaction_ids(main_df, m, target, float(stemless.get(m, 0.25)), seed=c.get("seed", 0))
            (res / f"redact_{slug(m)}.json").write_text(json.dumps(ids))
            redact[m] = len(ids)
        plan = {"target_accuracy": round(target, 3), "calibration_reference": ref_model,
                "n_redacted": redact, "stemless_accuracy": stemless.round(3).to_dict(),
                "honest_accuracy": acc.round(3).to_dict()}
        (res / "plan.json").write_text(json.dumps(plan, indent=2))
        print(json.dumps(plan, indent=2))
        print("Check: stemless accuracy well above 25% means the redacted control needs more items redacted; "
              "redaction_ids already accounts for it. Verify the realised redacted accuracy after `main`.")

    elif a.phase == "main":
        plan = json.loads((res / "plan.json").read_text())
        x = plan["target_accuracy"]
        for m in subjects:
            tasks = [svt_battery(condition=cond, target_accuracy=x, monitor_model=mon, **common)
                     for cond in (*INSTRUCTED, "incentive", "incentive_reverse")]
            tasks.append(svt_battery(condition="honest", redact_file=str(res / f"redact_{slug(m)}.json"),
                                     monitor_model=mon, **common))
            run(tasks, [m], ROOT / "logs" / "main" / slug(m), **mc)

    elif a.phase == "spotcheck":
        f = c["frontier_spotcheck"]
        plan = json.loads((res / "plan.json").read_text())
        tasks = [svt_battery(condition=cond, target_accuracy=plan["target_accuracy"], dataset=c["dataset"],
                             n_items=f["n_items"], n_perms=c["n_perms"]) for cond in f["arms"]]
        run(tasks, [f["id"]], ROOT / "logs" / "spotcheck", **mc)


def _all_ids_file(c: dict) -> Path:
    """Write the ids of the stemless subset (every stem removed) and return the path."""
    from svt_sandbag.datasets import load_pool
    from svt_sandbag.items import sample_items

    items = sample_items(load_pool(c["dataset"], seed=c.get("seed", 0)), c["stemless_items"], seed=c.get("seed", 0))
    path = ROOT / "results" / "stemless_ids.json"
    path.write_text(json.dumps([it.item_id for it in items]))
    return path


if __name__ == "__main__":
    main()
