"""Live progress bars for running evaluations (and Ollama model downloads).

    python scripts/progress.py            # one snapshot
    python scripts/progress.py --watch    # refresh every 10 s until Ctrl+C

Read-only: it only opens the .eval log files Inspect is writing (a zip with one entry per
finished answer) and, if Ollama is installed, the local model folder. It never touches a run.
An evaluation counts as running while its log has no final header and was written to in the
last --stale minutes; finished ones stay listed for --recent minutes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from inspect_ai.log import read_eval_log, read_eval_log_sample_summaries
from tqdm import tqdm

ROOT = Path(__file__).parents[1]
# approximate download sizes (bytes) of the local models this study uses
OLLAMA_SIZES = {"llama3.1:8b": 4.92e9, "llama3.2:3b": 2.02e9}


def _created(created: str | None) -> float:
    try:
        return datetime.fromisoformat(str(created)).timestamp()
    except ValueError:
        return time.time()


def scan(log_root: Path, stale_min: float, recent_min: float, with_acc: bool) -> list[dict]:
    rows, now = [], time.time()
    for path in log_root.rglob("*.eval"):
        age_min = (now - path.stat().st_mtime) / 60
        if age_min > max(stale_min, recent_min):
            continue
        try:
            # entry names are cheap to list; their content is zstd-compressed, so anything
            # read goes through Inspect's own reader
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
            finished = "header.json" in names
            if (finished and age_min > recent_min) or (not finished and age_min > stale_min):
                continue
            head = read_eval_log(str(path), header_only=True)
            spec = head.eval
            done = sum(n.startswith("samples/") for n in names)
            acc = None
            if with_acc:
                scores = [(s.scores or {}).get("svt_scorer") for s in read_eval_log_sample_summaries(str(path))]
                scores = [sc for sc in scores if sc is not None]
                acc = sum(sc.value == "C" for sc in scores) / len(scores) if scores else None
        except Exception:  # noqa: BLE001, S112 - a log being written right now; the next refresh reads it
            continue
        md = spec.metadata or {}
        total = int(md.get("n_items", 0)) * int(md.get("n_perms", 1)) or done
        started = _created(spec.created)
        rows.append({"label": f"{spec.model.split('/')[-1]} · {md.get('arm', '?')}",
                     "done": done, "total": total, "started": started,
                     # a finished run's duration ends at its last write, not now
                     "elapsed": (path.stat().st_mtime if finished else time.time()) - started,
                     "status": head.status if finished else "running",
                     "finished": finished, "acc": acc, "phase": path.relative_to(log_root).parts[0]})
    return sorted(rows, key=lambda r: (r["finished"], r["phase"], r["label"]))


def ollama_lines() -> list[str]:
    home = Path(os.environ.get("OLLAMA_MODELS", Path.home() / ".ollama" / "models"))
    if not home.exists():
        return []
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as r:
            have = {m["name"] for m in json.loads(r.read())["models"]}
    except OSError:
        have = set()
    lines = [f"  ✓ {m} ready" for m in sorted(have)]
    # a download in progress is a pre-allocated "<blob>-partial" file plus small "<blob>-partial-N"
    # JSON records, one per chunk, each with its Size and Completed bytes
    chunks: dict[str, list[dict]] = {}
    for f in (home / "blobs").glob("*-partial-*"):
        try:
            chunks.setdefault(f.name.rsplit("-partial-", 1)[0], []).append(json.loads(f.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    for blob, parts in chunks.items():
        size, done = sum(p["Size"] for p in parts), sum(p["Completed"] for p in parts)
        name = min(OLLAMA_SIZES, key=lambda m: abs(OLLAMA_SIZES[m] - size))  # identify by size
        first = _SEEN.setdefault(blob, (time.time(), done))  # watch mode: rate since first seen
        elapsed = time.time() - first[0]
        rate = (done - first[1]) / elapsed if elapsed > 0 and done > first[1] else None
        lines.append("  " + tqdm.format_meter(done, size, elapsed, ncols=90, unit="B", unit_scale=True,
                                              rate=rate, initial=first[1], prefix=f"downloading {name}"))
    return lines


_SEEN: dict[str, tuple[float, int]] = {}


def render(log_root: Path, a: argparse.Namespace) -> str:
    out = [f"svt-sandbagging progress — {datetime.now().strftime('%H:%M:%S')}  (logs: {log_root})", ""]
    rows = scan(log_root, a.stale, a.recent, not a.no_acc)
    running = [r for r in rows if not r["finished"]]
    out.append(f"Running evaluations: {len(running)}")
    for r in rows:
        acc = f" acc={r['acc']:.2f}" if r["acc"] is not None else ""
        tag = {"success": "done ", "running": ""}.get(r["status"], f"{r['status'].upper()} ")
        bar = tqdm.format_meter(r["done"], r["total"], r["elapsed"], ncols=100, unit="ans",
                                prefix=f"{tag}{r['phase']}: {r['label']}")
        out.append(f"  {bar}{acc}")
    if not rows:
        out.append("  (nothing running or recently finished)")
    oll = ollama_lines()
    if oll:
        out += ["", "Ollama models:", *oll]
    out += ["", "Ctrl+C to stop." if a.watch else ""]
    return "\n".join(out)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default=str(ROOT / "logs"))
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--every", type=float, default=10, help="refresh interval in seconds")
    ap.add_argument("--stale", type=float, default=30, help="minutes without writes before a run counts as stopped")
    ap.add_argument("--recent", type=float, default=120, help="minutes to keep finished runs listed")
    ap.add_argument("--no-acc", action="store_true", help="skip running accuracy (faster on huge logs)")
    a = ap.parse_args()
    root = Path(a.logs)
    if not a.watch:
        print(render(root, a))
        return
    try:
        while True:
            text = render(root, a)
            sys.stdout.write("\033[2J\033[H" + text + "\n")
            sys.stdout.flush()
            time.sleep(a.every)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
