"""Export the raw Inspect logs as a public, text-free dataset (data/runs.csv + data/presentations.csv).

    python scripts/sanitize_logs.py                      # logs/{pilot,panel,main,main_x50,difficulty_panel} -> data/
    python scripts/sanitize_logs.py --check-only         # re-run the leak checks on existing data/

The raw logs hold the full benchmark questions and answer options, every model's reasoning,
monitor explanations and request metadata. None of that may be published. This script works
by ALLOWLIST: it writes only the structured fields named in RUN_FIELDS / ROW_FIELDS below, so
anything not listed - prompts, options, completions, scratchpads, reasoning, explanations,
headers, run ids, paths, hostnames, package lists - is dropped by construction.

For protected datasets (default: wmdp, mmlu) it also removes the answer key, because "item X,
chosen option 2, correct" would republish it even without any question text:
  * item ids are replaced by order-preserving pseudonyms (``<source>-item0042``), so runs still
    pair item by item and every analysis sorts items exactly as before;
  * option indices are relabelled with a secret per-item permutation (keyed by a salt kept in a
    local, gitignored file). Every detector only tests options for EQUALITY (same answer across
    option orders, same distractor as other models), which a per-item relabelling preserves, so
    the analysis reproduces exactly while the published labels say nothing about which original
    option was correct.

Before anything is written, the output is checked against the raw logs at run time: no question
stem or option text (read from the logs, never stored in this file), no original item id, no API
key value (read from .env / the environment, never printed), no key-like strings, no free-text
cell. Any failure aborts without writing.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import random
import re
import secrets
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parents[1]
DEFAULT_LOGS = ("logs/pilot", "logs/panel", "logs/main", "logs/main_x50", "logs/difficulty_panel")
PROTECTED = ("wmdp", "mmlu")

RUN_FIELDS = ("run_id", "phase", "requested_model", "dataset", "condition", "arm", "target_accuracy",
              "threshold", "n_items", "n_perms", "seed", "replicate", "arm_suffix", "n_redacted",
              "max_tokens", "reasoning_effort", "temperature", "status", "n_samples", "run_date",
              "inspect_version")
ROW_FIELDS = ("run_id", "item_id", "source", "perm_id", "redacted", "intrinsic_difficulty", "correct",
              "parse_failed", "chosen_position", "correct_position", "chosen_choice", "monitor",
              "input_tokens", "output_tokens", "reasoning_tokens", "resolved_model", "stop_reason",
              "scratchpad_chars", "reasoning_summary_chars")

# every string cell must match its column's pattern; anything else is treated as a leak
STRING_PATTERNS = {
    "run_id": r"r\d{3}", "phase": r"[a-z0-9_]+", "requested_model": r"[a-z0-9_.:/-]+",
    "dataset": r"[a-z]+", "condition": r"[a-z_]+", "arm": r"[a-z0-9_]+", "arm_suffix": r"[a-z0-9]+",
    "reasoning_effort": r"[a-z]+", "status": r"[a-z]+", "run_date": r"\d{4}-\d{2}-\d{2}",
    "inspect_version": r"[0-9a-z.+-]+", "item_id": r"[A-Za-z0-9-]+", "source": r"[a-z-]+",
    "resolved_model": r"[a-z0-9_.:/-]+", "stop_reason": r"[a-z_]+",
}
KEY_PATTERN = re.compile(r"AIza[0-9A-Za-z_-]{30,}|sk-[0-9A-Za-z_-]{20,}|hf_[0-9A-Za-z]{30,}")
ORIGINAL_ID = re.compile(r"\b(?:wmdp-(?:bio|chem|cyber)|mmlu)-\d+\b")
OPTION_LINE = re.compile(r"^[A-D]\) (.+)$", re.MULTILINE)
NUMERIC_RUN_FIELDS = ("target_accuracy", "threshold", "n_items", "n_perms", "seed", "replicate", "n_redacted",
                      "max_tokens", "temperature", "n_samples")
MIN_LEN = 12  # option texts shorter than this ("Yes", "None") are too generic to search for


# ---------------------------------------------------------------- pseudonyms

def load_salt(path: Path) -> bytes:
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        print(f"created new salt at {path} (gitignored; keep it private to re-sanitize consistently)")
    return bytes.fromhex(path.read_text().strip())


def item_pseudonyms(ids_by_source: dict[str, set[str]]) -> dict[str, str]:
    """Order-preserving: rank over the sorted union of original ids, so pseudonyms sort exactly
    like the originals (every analysis that sorts by item id sees the same order)."""
    ordered = sorted(i for ids in ids_by_source.values() for i in ids)
    source = {i: s for s, ids in ids_by_source.items() for i in ids}
    return {orig: f"{source[orig]}-item{rank:04d}" for rank, orig in enumerate(ordered)}


def option_relabel(salt: bytes, original_id: str) -> list[int]:
    """Secret per-item bijection of the four option indices."""
    digest = hmac.new(salt, original_id.encode(), hashlib.sha256).digest()
    perm = list(range(4))
    random.Random(int.from_bytes(digest[:8], "big")).shuffle(perm)
    return perm


# ---------------------------------------------------------------- extraction

def _num(x):
    return np.nan if x is None else x


def _reasoning_chars(sample) -> int:
    msg = sample.output.message if sample.output else None
    content = msg.content if msg is not None and isinstance(msg.content, list) else []
    return sum(len(getattr(c, "summary", None) or "") for c in content if getattr(c, "type", None) == "reasoning")


def extract(log_dirs: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, set[str]], set[str], set[str]]:
    """Read the logs; return (runs, rows) with ORIGINAL ids still in place, the protected item ids
    per source, and the stems/options seen (used only for the leak check, never written)."""
    from inspect_ai.log import list_eval_logs, read_eval_log

    runs, rows, protected_ids, stems, options = [], [], {}, set(), set()
    for d in log_dirs:
        for info in sorted(list_eval_logs(d), key=lambda i: i.name):
            log = read_eval_log(info)
            md, cfg = log.eval.metadata or {}, log.plan.config if log.plan else None
            run_id = f"r{len(runs) + 1:03d}"
            dataset = md.get("dataset")
            runs.append({
                "run_id": run_id, "phase": Path(d).name, "requested_model": log.eval.model,
                "dataset": dataset, "condition": md.get("condition"),
                "arm": md.get("arm") or md.get("condition"),
                "target_accuracy": md.get("target_accuracy") if md.get("condition") in ("calibrated", "coached") else None,
                "threshold": md.get("threshold"), "n_items": md.get("n_items"), "n_perms": md.get("n_perms"),
                "seed": md.get("seed"), "replicate": md.get("replicate"), "arm_suffix": md.get("arm_suffix"),
                "n_redacted": md.get("n_redacted"), "max_tokens": getattr(cfg, "max_tokens", None),
                "reasoning_effort": getattr(cfg, "reasoning_effort", None),
                "temperature": getattr(cfg, "temperature", None), "status": log.status,
                "n_samples": len(log.samples or []), "run_date": str(log.eval.created)[:10],
                "inspect_version": (log.eval.packages or {}).get("inspect_ai"),
            })
            for s in log.samples or []:
                sc = (s.scores or {}).get("svt_scorer")
                if sc is None:
                    continue
                mon = (s.scores or {}).get("scratchpad_monitor")
                usage = (s.model_usage or {}).get(log.eval.model)
                item, source = s.metadata["item_id"], s.metadata.get("source")
                if dataset in PROTECTED:
                    protected_ids.setdefault(source, set()).add(item)
                    user = next((m.text for m in s.input if m.role == "user"), "") if isinstance(s.input, list) else ""
                    stem = user.split("\nA) ")[0].strip()
                    if not s.metadata.get("redacted") and len(stem) >= MIN_LEN:
                        stems.add(stem)
                    options.update(o.strip() for o in OPTION_LINE.findall(user) if len(o.strip()) >= MIN_LEN)
                rows.append({
                    "run_id": run_id, "item_id": item, "source": source, "perm_id": s.metadata["perm_id"],
                    "redacted": bool(s.metadata.get("redacted", False)),
                    "intrinsic_difficulty": _num(s.metadata.get("intrinsic_difficulty")),
                    "correct": sc.value == "C", "parse_failed": bool(sc.metadata.get("parse_failed", False)),
                    "chosen_position": _num(sc.metadata.get("chosen_position")),
                    "correct_position": _num(sc.metadata.get("correct_position")),
                    "chosen_choice": _num(sc.metadata.get("chosen_choice")),
                    "monitor": mon.value if mon is not None else np.nan,
                    "input_tokens": usage.input_tokens if usage else np.nan,
                    "output_tokens": usage.output_tokens if usage else np.nan,
                    "reasoning_tokens": _num(getattr(usage, "reasoning_tokens", None)) if usage else np.nan,
                    "resolved_model": s.output.model if s.output else None,
                    "stop_reason": s.output.stop_reason if s.output else None,
                    "scratchpad_chars": len((sc.explanation or "").strip()),
                    "reasoning_summary_chars": _reasoning_chars(s),
                })
    runs = pd.DataFrame(runs)
    for col in NUMERIC_RUN_FIELDS:  # all-missing columns would otherwise be stored as text
        runs[col] = pd.to_numeric(runs[col], errors="coerce")
    return runs, pd.DataFrame(rows), protected_ids, stems, options


def anonymise(rows: pd.DataFrame, runs: pd.DataFrame, protected_ids: dict[str, set[str]],
              salt: bytes) -> pd.DataFrame:
    """Pseudonymise protected item ids and relabel their option indices (see module docstring)."""
    rows = rows.copy()
    pseud = item_pseudonyms(protected_ids)
    protected = rows["run_id"].map(runs.set_index("run_id")["dataset"]).isin(PROTECTED)
    perms = {orig: option_relabel(salt, orig) for orig in pseud}
    choice = rows["chosen_choice"].to_numpy(dtype=float).copy()
    for i in np.flatnonzero(protected.to_numpy() & ~np.isnan(choice)):
        choice[i] = perms[rows.at[i, "item_id"]][int(choice[i])]
    rows["chosen_choice"] = choice
    rows.loc[protected, "item_id"] = rows.loc[protected, "item_id"].map(pseud)
    return rows


# ---------------------------------------------------------------- leak checks

def secret_values() -> set[str]:
    vals = set()
    env_file = ROOT / ".env"
    if env_file.exists():
        from dotenv import dotenv_values

        vals |= {v for v in dotenv_values(env_file).values() if v and len(v) > 15}
    vals |= {v for k, v in os.environ.items() if k.endswith(("_API_KEY", "_TOKEN", "_SECRET")) and len(v) > 15}
    return vals


def leak_check(texts: dict[str, str], frames: dict[str, pd.DataFrame], stems: set[str], options: set[str],
               salt: bytes | None = None) -> list[str]:
    """Return a list of problems (empty = safe). Never includes the offending text itself."""
    problems = []
    keys = secret_values() | ({salt.hex()} if salt else set())
    for name, text in texts.items():
        n_stem = sum(s in text for s in stems)
        n_opt = sum(o in text for o in options)
        n_key = sum(k in text for k in keys)
        if n_stem:
            problems.append(f"{name}: contains {n_stem} question stem(s)")
        if n_opt:
            problems.append(f"{name}: contains {n_opt} answer option text(s)")
        if n_key:
            problems.append(f"{name}: contains {n_key} secret value(s)")
        if KEY_PATTERN.search(text):
            problems.append(f"{name}: contains a key-like string")
        if ORIGINAL_ID.search(text):
            problems.append(f"{name}: contains an original (non-pseudonymised) item id")
    for name, df in frames.items():
        expected = RUN_FIELDS if name == "runs.csv" else ROW_FIELDS
        if tuple(df.columns) != expected:
            problems.append(f"{name}: columns differ from the allowlist")
        for col in df.columns:
            # check every non-numeric column (object, pandas string, category ...): a free-text
            # column must never slip through just because of how pandas typed it
            if pd.api.types.is_numeric_dtype(df[col]) or pd.api.types.is_bool_dtype(df[col]):
                continue
            vals = df[col].dropna().astype(str)
            pat = STRING_PATTERNS.get(col)
            if pat is None:
                problems.append(f"{name}.{col}: text column not in the allowlist")
            elif not vals.str.fullmatch(pat).all() or (vals.str.len() > 80).any():
                problems.append(f"{name}.{col}: {int((~vals.str.fullmatch(pat)).sum())} cell(s) fail the allowed pattern")
    return problems


# ---------------------------------------------------------------- main

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", nargs="+", default=list(DEFAULT_LOGS))
    ap.add_argument("--out", default=str(ROOT / "data"))
    ap.add_argument("--salt-file", default=str(ROOT / ".sanitize_salt"))
    ap.add_argument("--check-only", action="store_true", help="re-check existing output against the logs")
    a = ap.parse_args()
    out = Path(a.out)
    logs = [d for d in a.logs if Path(d).exists()]

    print(f"reading {len(logs)} log folders ...")
    runs, rows, protected_ids, stems, options = extract(logs)
    print(f"  {len(runs)} runs, {len(rows)} answers; leak check will search for {len(stems)} question stems "
          f"and {len(options)} option texts read from the logs")

    if a.check_only:
        runs_out = pd.read_csv(out / "runs.csv")
        rows_out = pd.read_csv(out / "presentations.csv")
        salt = None
    else:
        salt = load_salt(Path(a.salt_file))
        runs_out = runs[list(RUN_FIELDS)]
        rows_out = anonymise(rows, runs, protected_ids, salt)[list(ROW_FIELDS)]

    texts = {"runs.csv": runs_out.to_csv(index=False), "presentations.csv": rows_out.to_csv(index=False)}
    problems = leak_check(texts, {"runs.csv": runs_out, "presentations.csv": rows_out}, stems, options, salt)
    if problems:
        print("LEAK CHECK FAILED - nothing written:")
        for p in problems:
            print("  -", p)
        raise SystemExit(1)
    print("leak check passed: no question/option text, original item ids, secrets, key-like strings or free text")
    if a.check_only:
        return
    out.mkdir(parents=True, exist_ok=True)
    for name, text in texts.items():
        (out / name).write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {out / 'runs.csv'} and {out / 'presentations.csv'}")


if __name__ == "__main__":
    main()
