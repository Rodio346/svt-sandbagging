"""Run the study in phases (each phase is resumable: Inspect's eval_set skips finished tasks).

    python scripts/run_study.py pilot      # arithmetic, 40 items, 2 subjects, all arms + stemless
    python scripts/run_study.py panel      # honest arm for every model; subjects also get a replicate
                                           # honest run (null) and a stemless run (options-only accuracy)
    python scripts/run_study.py prepare    # X = reference weak model's accuracy; redaction lists
    python scripts/run_study.py main       # sandbagging arms + redacted control, with monitor
    python scripts/run_study.py spotcheck  # optional frontier model
    python scripts/run_study.py stemless_main  # stemless on the MAIN items (arm stemless_main), so the
                                               # same-model stemless comparison is paired on all 300
    python scripts/run_study.py difficulty     # honest runs of the independent difficulty panel
                                               # (config difficulty_panel): defines the fixed floor set

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
MAIN_ARMS = (*INSTRUCTED, "incentive", "incentive_reverse", "redacted")


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
    if not models:
        print(f"  (no models for {log_dir.name}; skipped by --models filter)")
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    for t in tasks:
        t.config.max_connections = max_connections
    # log_dir_allow_dirty: let a filtered re-run (e.g. --models) add/replace its own
    # models' logs while leaving other models' logs in the same dir untouched.
    ok, _ = eval_set(tasks, log_dir=str(log_dir), model=models, retry_attempts=3,
                     max_tasks=max_tasks, log_dir_allow_dirty=True, **kw)
    if not ok:
        raise SystemExit(f"some tasks did not finish; re-run the same command to resume ({log_dir})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["pilot", "panel", "prepare", "main", "spotcheck", "stemless_main",
                                      "difficulty"])
    ap.add_argument("--max-connections", type=int, default=10)
    ap.add_argument("--models", default=None,
                    help="comma-separated model ids to restrict this run to (e.g. re-running only "
                         "the arms of models whose parse-fail rate exceeded the gate). Others are skipped.")
    ap.add_argument("--target", type=float, default=None,
                    help="main only: override the plan's target accuracy X for calibrated/coached")
    ap.add_argument("--arms", default=None,
                    help="main only: comma-separated arms to run (default: " + ",".join(MAIN_ARMS) + ")")
    ap.add_argument("--suffix", default=None,
                    help="main only: tag appended to arm names (e.g. x50 -> calibrated_x50) and to the "
                         "log folder (logs/main_x50), so a variant never mixes with the original arms")
    a = ap.parse_args()
    c = cfg()
    only = {m.strip() for m in a.models.split(",")} if a.models else None
    subjects = [m["id"] for m in c["subjects"] if only is None or m["id"] in only]
    weak_cfg = [m for m in c["weak_controls"] if only is None or m["id"] in only]
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
        for m in weak_cfg:  # one run per weak model: generation settings can differ per model
            run([svt_battery(condition="honest", **common, **_generation(m))], [m["id"]],
                ROOT / "logs" / "panel" / "weak", **mc)
        stemless = svt_battery(condition="honest", redact_file=str(_all_ids_file(c)),
                               **{**common, "n_items": c["stemless_items"]})  # every stem removed
        run([svt_battery(condition="honest", replicate=1, monitor_model=mon, **common), stemless],
            subjects, ROOT / "logs" / "panel" / "extra", **mc)

    elif a.phase == "prepare":
        df = load_logs(ROOT / "logs" / "panel", aliases=c.get("aliases"))
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
        x = a.target if a.target is not None else plan["target_accuracy"]
        arms = [s.strip() for s in a.arms.split(",")] if a.arms else list(MAIN_ARMS)
        unknown = set(arms) - set(MAIN_ARMS)
        if unknown:
            raise SystemExit(f"unknown arms {sorted(unknown)}; choose from {MAIN_ARMS}")
        log_root = ROOT / "logs" / (f"main_{a.suffix}" if a.suffix else "main")
        print(f"main: target X={x}, arms={arms}, suffix={a.suffix}, logs -> {log_root}")
        for m in subjects:
            tasks = [svt_battery(condition=cond, target_accuracy=x, monitor_model=mon, arm_suffix=a.suffix,
                                 **common) for cond in arms if cond != "redacted"]
            if "redacted" in arms:
                tasks.append(svt_battery(condition="honest", redact_file=str(_redact_file(res, m, c)),
                                         monitor_model=mon, arm_suffix=a.suffix, **common))
            run(tasks, [m], log_root / slug(m), **mc)

    elif a.phase == "stemless_main":
        ids = _ids_file(c, c["n_items"], "main_ids.json")
        run([svt_battery(condition="honest", redact_file=str(ids), arm_suffix="main", **common)], subjects,
            ROOT / "logs" / "panel" / "stemless_main", **mc)

    elif a.phase == "difficulty":
        # independent of every compared model: these runs only define the floor (very easy) item set
        for m in c.get("difficulty_panel", []):
            if only is None or m["id"] in only:
                run([svt_battery(condition="honest", **common, **_generation(m))], [m["id"]],
                    ROOT / "logs" / "difficulty_panel", **mc)

    elif a.phase == "spotcheck":
        f = c["frontier_spotcheck"]
        plan = json.loads((res / "plan.json").read_text())
        tasks = [svt_battery(condition=cond, target_accuracy=plan["target_accuracy"], dataset=c["dataset"],
                             n_items=f["n_items"], n_perms=c["n_perms"]) for cond in f["arms"]]
        run(tasks, [f["id"]], ROOT / "logs" / "spotcheck", **mc)


def _generation(model_cfg: dict) -> dict:
    """Per-model generation overrides from the config (e.g. reasoning_effort: null for local
    models that reject a reasoning setting). Absent keys keep the task defaults."""
    return {k: model_cfg[k] for k in ("reasoning_effort", "max_tokens") if k in model_cfg}


def _redact_file(res: Path, model: str, c: dict) -> Path:
    """Redaction list for ``model``. Earlier runs saved it under the alias the model was
    requested as (e.g. redact_google_gemini-flash-latest.json), so check those names too."""
    names = [model] + [k for k, v in (c.get("aliases") or {}).items() if v == model]
    for name in names:
        path = res / f"redact_{slug(name)}.json"
        if path.exists():
            return path
    raise SystemExit(f"no redaction list for {model}; run `prepare` first")


def _all_ids_file(c: dict) -> Path:
    """Write the ids of the stemless subset (every stem removed) and return the path."""
    return _ids_file(c, c["stemless_items"], "stemless_ids.json")


def _ids_file(c: dict, n: int, name: str) -> Path:
    """Write the ids of the first ``n`` sampled items (the same draw svt_battery makes) to results/<name>.
    The file holds benchmark item ids, so it stays local (results/ is gitignored except analysis/)."""
    from svt_sandbag.datasets import load_pool
    from svt_sandbag.items import sample_items

    items = sample_items(load_pool(c["dataset"], seed=c.get("seed", 0)), n, seed=c.get("seed", 0))
    path = ROOT / "results" / name
    path.write_text(json.dumps([it.item_id for it in items]))
    return path


if __name__ == "__main__":
    main()
