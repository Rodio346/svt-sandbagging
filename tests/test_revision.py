"""Analysis revision: panel floor set, paired Fisher exact, stemless restriction, trend deviation and calibration."""

import numpy as np
import pandas as pd
import pytest
from scipy.stats import fisher_exact

from svt_sandbag.analysis import (
    Comparison,
    fixed_floor,
    floor_fisher,
    run_test,
    study_floor,
    trend_calibration,
    trend_deviation,
)
from svt_sandbag.detectors import Reference, pack_run
from svt_sandbag.simulate import simulate


@pytest.fixture(scope="module")
def sim():
    return simulate(n_items=200, seed=3)


def _honest(model, correct, parse_failed=None):
    """Two option orders per item; ``correct[i]`` = (perm0, perm1)."""
    rows = []
    for i, (c0, c1) in enumerate(correct):
        for p, c in enumerate((c0, c1)):
            pf = bool(parse_failed and (i, p) in parse_failed)
            rows.append({"model": model, "arm": "honest", "item_id": f"i{i}", "perm_id": p, "correct": c,
                         "parse_failed": pf, "chosen_choice": 0, "chosen_position": 0})
    return pd.DataFrame(rows)


def test_fixed_floor_needs_every_model_right_in_every_order():
    a = _honest("a", [(True, True), (True, True), (True, False), (True, True)], parse_failed={(3, 1)})
    b = _honest("b", [(True, True), (False, False), (True, True), (True, True)])
    df = pd.concat([a, b])
    assert fixed_floor(df, ["a", "b"]) == {"i0"}  # i1: b wrong; i2: a wrong once; i3: a unparsed once
    assert fixed_floor(df, ["b"]) == {"i0", "i2", "i3"}
    assert study_floor(df, "legacy") is None
    assert study_floor(df, "panel") == {"i0"}  # every panel model in the frame must get it right
    with pytest.raises(ValueError):
        study_floor(df.iloc[0:0], "panel")  # no panel runs yet: the caller must fall back to legacy
    with pytest.raises(ValueError):
        study_floor(df, "nonsense")


def test_fixed_floor_gives_one_d2_per_run_across_comparisons(sim):
    """Regression: with a fixed floor, a run's D2 must not depend on what it is compared with."""
    floor = study_floor(sim[sim["model"].isin(["weak-1", "weak-2"])], "panel")  # stand-in panel
    vals, floors = set(), set()
    for neg in ("weak:weak-1", "weak:weak-2", "weak:weak-3", "redacted", "honest"):
        rows = run_test(sim, "strong-A", Comparison("calibrated", neg, "x"), "t", n_perm=20, floor=floor)
        d2 = next(r for r in rows if r["detector"] == "D2_easy_error_conc")
        vals.add(round(d2["pos_value"], 12))
        floors.add(d2["floor_items"])
    assert len(vals) == 1 and len(floors) == 1


def test_floor_fisher_counts_and_p():
    ref = Reference(difficulty={f"i{k}": k / 10 for k in range(10)}, floor={"i0", "i1", "i2", "i3"})

    def run(wrong_items):
        return pd.DataFrame([{"item_id": f"i{k}", "perm_id": p, "correct": k not in wrong_items,
                              "parse_failed": False, "chosen_choice": 0, "chosen_position": 0}
                             for k in range(10) for p in range(2)])
    pos, neg = pack_run(run({0, 1, 2, 9}), ref), pack_run(run({7, 8, 9}), ref)
    f = floor_fisher(pos, neg)
    assert (f["floor_err_pos"], f["other_err_pos"], f["floor_err_neg"], f["other_err_neg"]) == (6, 2, 0, 6)
    assert f["floor_items"] == 4 and f["floor_err_rate_pos"] == pytest.approx(6 / 8)
    assert f["fisher_p"] == pytest.approx(fisher_exact([[6, 2], [0, 6]], alternative="greater")[1])


def test_floor_fisher_counts_only_presentations_paired_in_both_runs():
    """An item missing from one run, or unparsed in one run, must not enter either side of the table."""
    ref = Reference(difficulty={f"i{k}": k / 10 for k in range(6)}, floor={"i0", "i1", "i2"})
    ids = np.array([f"i{k}" for k in range(6)])

    def run(wrong, missing=(), unparsed=()):
        return pd.DataFrame([{"item_id": f"i{k}", "perm_id": p, "correct": k not in wrong,
                              "parse_failed": (k, p) in unparsed, "chosen_choice": 0, "chosen_position": 0}
                             for k in range(6) for p in range(2) if k not in missing])
    pos = pack_run(run({0, 1, 4}), ref, ids)
    neg = pack_run(run({2, 5}, missing={0}, unparsed={(1, 0)}), ref, ids)
    f = floor_fisher(pos, neg)
    # pos item 0 (neg lacks it) is dropped; pos item 1 counts only in order 1 (neg unparsed in order 0)
    assert (f["floor_err_pos"], f["other_err_pos"], f["floor_err_neg"], f["other_err_neg"]) == (1, 2, 2, 2)
    assert f["paired_presentations"] == 9 and f["floor_items"] == 2
    with pytest.raises(ValueError):
        floor_fisher(pos, pack_run(run(set()), ref, ids[:5]))


def test_run_test_restricts_to_given_items(sim):
    ids = sorted(sim["item_id"].unique())[:50] + ["not-an-item"]
    rows = run_test(sim, "strong-A", Comparison("calibrated", "redacted", "x"), "stemless", n_perm=20, items=ids)
    assert {r["n_items"] for r in rows} == {50}
    assert all("fisher_p" in r for r in rows)


def test_trend_run_on_the_line_has_zero_z():
    acc = np.array([0.50, 0.56, 0.66, 0.73, 0.80])
    noise = np.array([0.02, -0.03, 0.01, 0.03, -0.03])
    line = lambda x: 1.2 - 1.2 * x
    stats = pd.DataFrame({"model": [f"w{i}" for i in range(5)] + ["s", "s"], "arm": ["honest"] * 5 + ["a", "b"],
                          "accuracy": [*acc, 0.6, 0.6], "D2": [*(line(acc) + noise), line(0.6), line(0.6) + 0.5],
                          "floor_items": 70})
    weak = [f"w{i}" for i in range(5)]
    t = trend_deviation(None, detectors=("D2",), weak=weak, stats=stats).set_index("arm")
    on, off = t.loc["a"], t.loc["b"]
    slope, intercept = np.polyfit(acc, line(acc) + noise, 1)
    assert on["predicted"] == pytest.approx(intercept + slope * 0.6)
    assert abs(on["z"]) < 0.5 and on["p_t3"] > 0.5  # on the true line: no deviation
    assert off["z"] > 5 and off["p_t3"] < 0.01 and off["z_min_loo"] > 3
    assert not on["extrapolated"] and set(t.index) == {"a", "b"}  # weak runs are the fit, not rows


def test_trend_calibration_flags_only_the_off_line_weak_model():
    acc = np.array([0.50, 0.56, 0.66, 0.73, 0.80])
    y = 1.2 - 1.2 * acc + np.array([0.02, -0.03, 0.01, 0.03, -0.03])
    weak = [f"w{i}" for i in range(5)]
    stats = pd.DataFrame({"model": weak, "arm": "honest", "accuracy": acc, "D2": y, "floor_items": 70})
    good = trend_calibration(None, detectors=("D2",), weak=weak, stats=stats)
    assert len(good) == 5 and (good["n_fit"] == 4).all() and good["calibrated"].all()
    bad = trend_calibration(None, detectors=("D2",), weak=weak, stats=stats.assign(D2=y + [0, 0, 0.4, 0, 0]))
    assert bad.set_index("held_out").loc["w2", "z"] > 2 and not bad.set_index("held_out").loc["w2", "calibrated"]
