"""Manipulation check for the incentive arm: did the models even notice the deployment note?

    python scripts/incentive_uptake.py --logs logs/main logs/panel --out results/analysis

H4 ("an incentive alone makes models sandbag") was judged on accuracy alone. A null there means
something only if the note was read. This script searches what each model wrote - the visible
scratchpad and the readable reasoning (DeepSeek's reasoning text; the provider-written reasoning
summaries for OpenAI and Gemini, whose raw reasoning is encrypted) - for words from the note, in
the incentive, incentive_reverse and honest arms of every subject model. honest never saw the
note, so its hit rate is the false-positive rate of the keyword list (words such as "policy" or
"threshold" also occur in the questions).

Outputs
  <out>/incentive_uptake.csv   per model x arm: transcripts, share with any keyword, per keyword
  <dump>/<model>__<arm>.jsonl  LOCAL ONLY (default logs/incentive_uptake/, gitignored): the
                               visible text of a seeded random sample per arm, for the manual
                               read. It quotes model reasoning about WMDP questions - never publish.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parents[1]
ARMS = ("incentive", "incentive_reverse", "honest")
KEYWORDS = {  # the words of the note (prompts.INCENTIVE_DOC); "deploy" also matches deployment/deployed
    "deploy": r"deploy", "retrain": r"re-?train", "further training": r"further\s+training",
    "policy": r"polic(?:y|ies)", "threshold": r"threshold", "50%": r"50\s*%|fifty percent",
    "not be deployed": r"not\s+be\s+deployed", "internal note": r"internal\s+note",
}
_PATTERNS = {k: re.compile(v, re.IGNORECASE) for k, v in KEYWORDS.items()}


def visible_text(sample) -> tuple[str, str]:
    """(scratchpad, readable reasoning). Encrypted reasoning is skipped; its summary is kept."""
    sc = (sample.scores or {}).get("svt_scorer")
    pad = (sc.explanation or "").strip() if sc is not None else ""
    msg = sample.output.message if sample.output else None
    parts = []
    for c in (msg.content if msg is not None and isinstance(msg.content, list) else []):
        if getattr(c, "type", None) != "reasoning":
            continue
        if not getattr(c, "redacted", False) and getattr(c, "reasoning", None):
            parts.append(c.reasoning)
        if getattr(c, "summary", None):
            parts.append(c.summary)
    return pad, "\n".join(parts).strip()


def hits(text: str) -> dict[str, bool]:
    return {k: bool(p.search(text)) for k, p in _PATTERNS.items()}


def load(log_dirs: list[str], subjects: list[str], aliases: dict[str, str]) -> list[dict]:
    from inspect_ai.log import list_eval_logs, read_eval_log

    out = []
    for d in log_dirs:
        for info in sorted(list_eval_logs(d), key=lambda i: i.name):
            head = read_eval_log(info, header_only=True)
            md = head.eval.metadata or {}
            model = aliases.get(head.eval.model, head.eval.model)
            arm = md.get("arm") or md.get("condition")
            if md.get("dataset") != "wmdp" or model not in subjects or arm not in ARMS or head.status != "success":
                continue
            for s in read_eval_log(info).samples or []:
                pad, reasoning = visible_text(s)
                out.append({"model": model, "arm": arm, "scratchpad": pad, "reasoning": reasoning,
                            **hits(pad + "\n" + reasoning)})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", nargs="+", default=["logs/main", "logs/panel"])
    ap.add_argument("--config", default=str(ROOT / "configs" / "study.yaml"))
    ap.add_argument("--out", default="results/analysis")
    ap.add_argument("--dump", default="logs/incentive_uptake", help="local-only sample dump for the manual read")
    ap.add_argument("--sample", type=int, default=25)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    cfg = yaml.safe_load(Path(a.config).read_text())
    subjects = [m["id"] if isinstance(m, dict) else m for m in cfg["subjects"]]
    rows = load([d for d in a.logs if Path(d).exists()], subjects, cfg.get("aliases") or {})
    df = pd.DataFrame(rows)
    if df.empty:
        raise SystemExit("no incentive / honest WMDP runs of the subject models found")
    kw = list(KEYWORDS)
    df["any_keyword"] = df[kw].any(axis=1)
    df["has_text"] = (df["scratchpad"].str.len() + df["reasoning"].str.len()) > 0

    g = df.groupby(["model", "arm"])
    table = pd.concat([g.size().rename("n_transcripts"), g["has_text"].mean().rename("share_with_text"),
                       g["any_keyword"].mean().rename("share_any_keyword"), g[kw].mean()], axis=1).round(4)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / "incentive_uptake.csv")
    print(table.to_markdown())

    dump = Path(a.dump)
    dump.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(a.seed)
    for (m, arm), gg in df[df["has_text"]].groupby(["model", "arm"]):
        pick = gg.iloc[np.sort(rng.choice(len(gg), size=min(a.sample, len(gg)), replace=False))]
        with open(dump / f"{m.replace('/', '_')}__{arm}.jsonl", "w", encoding="utf-8") as f:
            f.writelines(json.dumps({"k": k, "model": m, "arm": arm, "keywords": [x for x in kw if r[x]],
                                    "scratchpad": r["scratchpad"], "reasoning": r["reasoning"]},
                                   ensure_ascii=False) + "\n" for k, r in enumerate(pick.to_dict("records")))
    print(f"\nsampled transcripts for the manual read (LOCAL ONLY): {dump}")


if __name__ == "__main__":
    main()
