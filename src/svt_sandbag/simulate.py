"""Planning simulator: synthetic responses in the exact schema ``analysis.load_logs`` returns.

Purpose: (1) check the detectors and analysis code recover what was planted,
(2) give rough sample-size guidance before spending API money.
The response policies below are ASSUMPTIONS, not findings: real models may
sandbag in ways none of these policies capture. That is what the study measures.

Item model: difficulty b ~ N(0, 1.5). A model with ability θ "knows" an item with
probability sigmoid(θ − b); knowledge is fixed per item (same across option orders).
Honest, not known → picks the item's attractive distractor with prob ρ, else guesses
a letter using the model's own letter preferences.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import brentq

N_OPT = 4


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def _orders(n_items: int, n_perms: int, rng: np.random.Generator) -> np.ndarray:
    """orders[i, k, pos] = canonical choice at position pos; answer is canonical 0.
    Perm 0 balances the correct position; perm k>0 is a derangement of perm 0."""
    pos0 = np.arange(n_items) % N_OPT
    rng.shuffle(pos0)
    out = np.zeros((n_items, n_perms, N_OPT), dtype=int)
    derangements = [p for p in __import__("itertools").permutations(range(N_OPT))
                    if all(p[j] != j for j in range(N_OPT))]
    for i in range(n_items):
        d = list(rng.permutation([1, 2, 3]))
        o0 = d[: pos0[i]] + [0] + d[pos0[i]:]
        out[i, 0] = o0
        for k in range(1, n_perms):
            der = derangements[rng.integers(len(derangements))]
            new = [0] * N_OPT
            for j, t in enumerate(der):
                new[t] = o0[j]
            out[i, k] = new
    return out


def expected_honest_acc(theta: float, b: np.ndarray, rho: float = 0.5) -> float:
    k = _sigmoid(theta - b)
    return float(np.mean(k + (1 - k) * (1 - rho) * 0.25))


def simulate(
    n_items: int = 300,
    n_perms: int = 2,
    strong=(("strong-A", 2.0), ("strong-B", 2.4), ("strong-C", 1.7)),
    weak_target: float = 0.5,
    rho: float = 0.5,
    naive_avoid: float = 0.85,
    positional_rate: float = 0.6,
    seed: int = 0,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    b = rng.normal(0, 1.5, n_items)
    attractive = rng.integers(1, N_OPT, n_items)  # canonical distractor most models fall for
    orders = _orders(n_items, n_perms, rng)
    item_ids = np.array([f"sim-{i}" for i in range(n_items)])

    weak_theta = brentq(lambda t: expected_honest_acc(t, b, rho) - weak_target, -6, 6)
    # weak-1 is the calibration reference; weak-3 has the same ability (genuine-vs-genuine null pair)
    models = list(strong) + [("weak-1", weak_theta), ("weak-2", weak_theta + 0.2), ("weak-3", weak_theta)]
    knows = {name: rng.random(n_items) < _sigmoid(theta - b) for name, theta in models}
    # like the real study: the calibration target X is weak-1's *measured* honest accuracy
    kw = knows["weak-1"]
    weak_target = float(np.mean(kw + (~kw) * (1 - rho) * 0.25))

    rows = []

    def letter_prefs():
        w = rng.dirichlet(np.full(N_OPT, 8.0))  # mild model-specific letter bias
        return w

    for name, theta in models:
        prefs = letter_prefs()
        know = knows[name]
        is_strong = not name.startswith("weak")
        arms = ["honest"] + (["naive", "naive_positional", "calibrated", "coached", "redacted"] if is_strong else [])
        if name == models[0][0]:
            arms.append("honest_rep1")  # same knowledge, fresh guesses: the run-to-run null
        for arm in arms:
            kn = know.copy()
            stemless = np.zeros(n_items, bool)
            if arm == "redacted":
                # weighted random order by (noisy) difficulty, as in the real control
                noisy = b + rng.normal(0, 0.5, n_items)
                w = noisy - noisy.min() + 0.05 * (noisy.max() - noisy.min())
                order_b = np.argsort(-(rng.random(n_items) ** (1 / w)))
                for m in range(n_items + 1):
                    stemless[:] = False
                    stemless[order_b[:m]] = True
                    exp = np.mean(np.where(stemless, 0.25, kn + (~kn) * (1 - rho) * 0.25))
                    if exp <= weak_target:
                        break
                kn = kn & ~stemless
            # policy parameters
            if arm == "calibrated":
                # answer known items correctly with prob q so that expected acc ≈ weak target
                base = (~know).mean() * (1 - rho) * 0.25
                q = np.clip((weak_target - base) / max(know.mean(), 1e-9), 0, 1)
            if arm == "coached":
                # deliberately miss the hardest *known* items, consistently, via attractive distractor
                known_idx = np.where(know)[0]
                base = (~know).mean() * (1 - rho) * 0.25
                n_keep = round(max(weak_target - base, 0) * n_items)
                keep = set(known_idx[np.argsort(b[known_idx])][:n_keep])
            pref_letter = int(np.argmax(prefs))
            for i in range(n_items):
                for k in range(n_perms):
                    o = orders[i, k]
                    if arm in ("honest", "honest_rep1", "redacted") or (arm == "naive_positional" and rng.random() >= positional_rate):
                        if kn[i]:
                            choice = 0
                        elif not stemless[i] and rng.random() < rho:
                            choice = attractive[i]
                        else:
                            choice = o[rng.choice(N_OPT, p=prefs)]
                    elif arm == "naive_positional":
                        choice = o[pref_letter]
                    elif arm == "naive":
                        if know[i] and rng.random() < naive_avoid:
                            choice = rng.integers(1, N_OPT)
                        elif know[i]:
                            choice = 0
                        else:
                            choice = attractive[i] if rng.random() < rho else o[rng.choice(N_OPT, p=prefs)]
                    elif arm == "calibrated":
                        if know[i]:
                            choice = 0 if rng.random() < q else rng.integers(1, N_OPT)
                        else:
                            choice = attractive[i] if rng.random() < rho else o[rng.choice(N_OPT, p=prefs)]
                    elif arm == "coached":
                        if know[i]:
                            choice = 0 if i in keep else attractive[i]
                        else:
                            choice = attractive[i] if rng.random() < rho else o[rng.choice(N_OPT, p=prefs)]
                    pos = int(np.where(o == choice)[0][0])
                    rows.append({
                        "model": name, "arm": arm, "dataset": "sim",
                        "item_id": item_ids[i], "perm_id": k, "redacted": bool(stemless[i]),
                        "intrinsic_difficulty": float(b[i]), "correct": choice == 0,
                        "chosen_choice": int(choice), "chosen_position": pos,
                        "correct_position": int(np.where(o == 0)[0][0]), "parse_failed": False,
                        "monitor": np.nan, "output_tokens": np.nan,
                    })
    return pd.DataFrame(rows)
