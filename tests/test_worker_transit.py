"""인력 대중교통 보정 모형(adapters/worker_transit.py) — 호출 없이 검사한다."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from adapters import worker_transit as wt


def _obs(n=200, a=1.8, b=12.0, detour=1.3, kmh=4.0, seed=0):
    rng = np.random.default_rng(seed)
    car = rng.uniform(2, 30, n)
    hav = car * 0.5
    odsay = a * car + b + rng.normal(0, 1, n)
    fk = hav * detour
    walk = fk / kmh * 60
    rows = []
    for i in range(n):
        f, t = f"N{i}", f"M{i}"
        rows.append({"from_id": f, "to_id": t, "pair_status": "ok", "car_min": car[i], "hav_km": hav[i],
                     "foot_km": fk[i], "odsay_min": odsay[i], "walk_min": walk[i],
                     "truth_min": min(odsay[i], walk[i]), "test": wt._is_test(f, t)})
    return pd.DataFrame(rows)


def test_fit_recovers_coefficients_and_reports_holdout_error():
    m = wt.fit(_obs(), 4.0, "x.csv")
    assert m["a"] == pytest.approx(1.8, abs=0.05)
    assert m["b"] == pytest.approx(12.0, abs=1.0)
    assert m["detour"] == pytest.approx(1.3, abs=0.01)
    assert m["source"] == "estimate"
    assert m["n_test"] > 0 and m["test_mae_min"] < 2
    assert m["n_train"] + m["n_test"] == 200


def test_predict_takes_shorter_of_transit_and_walk():
    m = {"a": 2.0, "b": 10.0, "detour": 1.3, "walk_kmh": 4.0}
    # 아주 가까우면 걷기(0.2km → 3.9분)가 대중교통(2×1+10=12분)보다 짧다
    assert wt.predict(1.0, 0.2, m) == pytest.approx(0.2 * 1.3 / 4 * 60)
    assert wt.predict(10.0, 5.0, m) == pytest.approx(30.0)
    arr = wt.predict(np.array([1.0, np.nan]), np.array([0.2, 1.0]), m)
    assert np.isnan(arr[1]), "자동차 시간이 없는 구간을 숫자로 만들었다"


def test_margin_quantile_reduces_underestimation():
    o = _obs(n=400, seed=3)
    o["odsay_min"] += np.random.default_rng(5).normal(0, 6, len(o))
    o["truth_min"] = np.minimum(o["odsay_min"], o["walk_min"])
    m50 = wt.fit(o, 4.0, "x.csv", quantile=0.5)
    m80 = wt.fit(o, 4.0, "x.csv", quantile=0.8)
    assert m80["margin"] > m50["margin"]
    assert m80["test_under_share"] < m50["test_under_share"]


def test_fit_refuses_too_few_samples():
    with pytest.raises(ValueError):
        wt.fit(_obs(n=10), 4.0, "x.csv")


def test_holdout_split_is_fixed():
    assert wt._is_test("A", "B") == wt._is_test("A", "B")
    share = np.mean([wt._is_test(f"N{i}", f"M{i}") for i in range(2000)])
    assert 0.25 < share < 0.35


def test_observations_keep_failures_out_of_truth():
    raw = pd.DataFrame([
        {"from_id": "A", "to_id": "B", "pair_status": "ok", "duration_seconds": 1200,
         "from_lat": 37.2, "from_lon": 127.0, "to_lat": 37.21, "to_lon": 127.01},
        {"from_id": "A", "to_id": "C", "pair_status": "rate_limited", "duration_seconds": None,
         "from_lat": 37.2, "from_lon": 127.0, "to_lat": 37.22, "to_lon": 127.02},
        {"from_id": "A", "to_id": "D", "pair_status": "too_close", "duration_seconds": None,
         "from_lat": 37.2, "from_lon": 127.0, "to_lat": 37.201, "to_lon": 127.001},
    ])
    obs = wt.observations(raw, {("A", "B"): 8.0, ("A", "C"): 9.0, ("A", "D"): 1.0}, 4.0, foot_url=None)
    by = obs.set_index("to_id")
    assert by.loc["B", "truth_min"] == pytest.approx(20.0)
    assert pd.isna(by.loc["C", "truth_min"]), "실패한 조회를 관측값으로 썼다"
    assert pd.isna(by.loc["D", "truth_min"]), "도보 거리를 모르는데 값을 만들었다"


# ── 확정 후보안 재조회 ───────────────────────────────────────────────────────

def _plan():
    import copy
    import json
    from pathlib import Path

    from planner.baseline import request_order
    inst = json.loads((Path(__file__).resolve().parent.parent / "fixtures" / "verify_set.json").read_text(encoding="utf-8"))
    hosp = {r["hospital_node"] for r in inst["requests"] if r.get("hospital_node")}
    t = copy.deepcopy(inst)
    for w in t["workers"]:
        w["travel_mode"] = "transit"
    t["worker_travel"] = {"transit": {(r["from_node"], r["to_node"]): float(r["minutes"]) for r in inst["travel"]
                                      if r["from_node"] not in hosp and r["to_node"] not in hosp}}
    return t, request_order(t).assignments


def _raw(legs, minutes, status="ok"):
    return pd.DataFrame([{"from_id": l["from_id"], "to_id": l["to_id"], "pair_status": status,
                          "duration_seconds": m * 60 if m is not None else None} for l, m in zip(legs, minutes)])


def test_plan_legs_follow_assignment_order():
    t, a = _plan()
    legs = wt.plan_legs(t, a)
    assert legs and all(l["model_min"] is not None for l in legs)
    assert len(legs) <= len(a)


def test_recheck_same_values_stays_ok():
    t, a = _plan()
    legs = wt.plan_legs(t, a)
    acts = wt.actual_minutes(_raw(legs, [l["model_min"] for l in legs]), legs, 4.0, {})
    res = wt.recheck(t, a, acts)
    assert res["ok"] and res["n_checked"] == len(legs) and res["mean_diff_min"] == 0


def test_recheck_flags_late_leg():
    t, a = _plan()
    legs = wt.plan_legs(t, a)
    mins = [l["model_min"] for l in legs]
    mins[-1] += 300                       # 마지막 연결 구간이 실제로는 아주 오래 걸림
    res = wt.recheck(t, a, wt.actual_minutes(_raw(legs, mins), legs, 4.0, {}))
    assert not res["ok"]
    assert legs[-1]["request_id"] in res["late_requests"]


def test_failed_query_is_not_treated_as_checked():
    t, a = _plan()
    legs = wt.plan_legs(t, a)
    acts = wt.actual_minutes(_raw(legs, [None] * len(legs), status="rate_limited"), legs, 4.0, {})
    assert all(x["actual_min"] is None and x["how"].startswith("확인 실패") for x in acts)
    res = wt.recheck(t, a, acts)
    assert res["n_checked"] == 0 and res["n_unchecked"] == len(legs)


def test_repair_retimes_without_new_legs_and_passes():
    t, a = _plan()
    legs = wt.plan_legs(t, a)
    mins = [l["model_min"] for l in legs]
    mins[-1] += 5                         # 조금 늦어지는 구간
    chk = wt.recheck(t, a, wt.actual_minutes(_raw(legs, mins), legs, 4.0, {}))
    fix = wt.repair(chk["instance"], a, chk["late_requests"] or [legs[-1]["request_id"]])
    assert fix["ok"]
    assert {(l["from_id"], l["to_id"]) for l in wt.plan_legs(chk["instance"], fix["assignments"])} <= \
           {(l["from_id"], l["to_id"]) for l in legs}, "다시 맞추면서 새 연결 구간이 생겼다"
    assert len(fix["assignments"]) + len(fix["displaced"]) == len(a)
