"""인력별 이동수단(travel_mode) — docs/travel_modes_plan.md 5단계.

인력마다 travel_mode(car / transit)를 둔다. transit 인력은 '인력이 다음 집으로 가는 이동'만
instance["worker_travel"]["transit"] 표로 계산하고, 병원동행의 집↔병원 구간은 모든 인력이 같은
instance["travel"] 값을 쓴다(동행 수단은 요청의 것이다).

검사:
  1) 검증기: transit 인력의 이동이 길어지면 자동차 기준으로 맞던 배정도 위반으로 잡는다.
  2) 검증기: 수단 표가 없는데 transit 인력이 있으면 자동차 시간으로 대신하지 않고 위반으로 남긴다.
  3) 기준선·최적화: transit 인력이 있는 입력에서 낸 배정이 검증기를 통과한다(같은 규칙).
  4) 수단 표에 값이 없으면 자동차 시간으로 메우지 않는다(ROUTE_UNKNOWN).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from planner.baseline import nearest, request_order
from planner.optimize import optimize
from verifier import DATA_INCONSISTENT, verify

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "verify_set.json"
SLOW = 40     # 대중교통 인력의 이동을 자동차보다 이만큼 길게 둔 시험값


@pytest.fixture
def inst():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def _hospitals(inst) -> set[str]:
    return {r["hospital_node"] for r in inst["requests"] if r.get("hospital_node")}


def _transit_table(inst, extra: int) -> list[dict]:
    """인력 출발지·어르신 집 사이 구간만(병원 구간 제외) 자동차 + extra 분."""
    hosp = _hospitals(inst)
    return [dict(t, minutes=float(t["minutes"]) + extra) for t in inst["travel"]
            if t["from_node"] not in hosp and t["to_node"] not in hosp]


def _with_transit(inst, wids, extra=SLOW):
    out = copy.deepcopy(inst)
    for w in out["workers"]:
        if w["worker_id"] in wids:
            w["travel_mode"] = "transit"
    out["worker_travel"] = {"transit": _transit_table(inst, extra)}
    return out


def _wids(inst):
    return [w["worker_id"] for w in inst["workers"]]


# ── 검증기 ────────────────────────────────────────────────────────────────

def test_same_table_keeps_fixture_feasible(inst):
    t = _with_transit(inst, _wids(inst), extra=0)
    assert verify(t, inst["assignments"]).ok


def test_verifier_uses_worker_mode_for_legs(inst):
    assert verify(inst, inst["assignments"]).ok
    t = _with_transit(inst, _wids(inst))
    rep = verify(t, inst["assignments"])
    assert not rep.ok, "대중교통 인력의 긴 이동을 자동차 시간으로 계산했다"


def test_car_worker_ignores_transit_table(inst):
    t = _with_transit(inst, set())            # 표는 있지만 모두 자동차 인력
    assert verify(t, inst["assignments"]).ok


def test_missing_mode_table_is_violation_not_car(inst):
    t = copy.deepcopy(inst)
    wid = inst["assignments"][0]["worker_id"]
    for w in t["workers"]:
        if w["worker_id"] == wid:
            w["travel_mode"] = "transit"
    rep = verify(t, inst["assignments"])
    assert DATA_INCONSISTENT in rep.codes()


def test_escort_legs_stay_shared(inst):
    """수단 표에 병원 구간이 있어도 동행의 집↔병원은 instance['travel'] 값을 쓴다."""
    t = _with_transit(inst, _wids(inst), extra=0)
    hosp = _hospitals(inst)
    t["worker_travel"]["transit"] += [dict(r, minutes=float(r["minutes"]) + 500) for r in inst["travel"]
                                      if r["from_node"] in hosp or r["to_node"] in hosp]
    assert verify(t, inst["assignments"]).ok


# ── 엔진: 같은 규칙으로 계산해 검증기를 통과한다 ──────────────────────────

@pytest.mark.parametrize("algo", [request_order, nearest])
def test_baselines_pass_verifier_with_transit_workers(inst, algo):
    for wids in (_wids(inst)[:1], _wids(inst)):
        t = _with_transit(inst, set(wids))
        res = algo(t)
        rep = verify(t, res.assignments)
        assert rep.ok, rep.summary()


def test_baseline_arrival_includes_transit_leg(inst):
    t = _with_transit(inst, _wids(inst))
    legs = {(r["from_node"], r["to_node"]): r["minutes"] for r in t["worker_travel"]["transit"]}
    workers = {w["worker_id"]: w for w in t["workers"]}
    reqs = {r["request_id"]: r for r in t["requests"]}
    res = request_order(t)
    assert res.assignments
    first = {}
    for a in sorted(res.assignments, key=lambda a: a["arrive_min"]):
        first.setdefault(a["worker_id"], a)
    for wid, a in first.items():
        w = workers[wid]
        leg = legs[(w["base_node"], reqs[a["request_id"]]["home_node"])]
        assert a["arrive_min"] >= int(w["shift_start_min"]) + leg


def test_optimize_passes_verifier_with_transit_workers(inst):
    t = _with_transit(inst, set(_wids(inst)[:1]))
    res = optimize(t, time_limit_sec=2)
    assert verify(t, res.assignments).ok
    car = optimize(inst, time_limit_sec=2)
    assert len(res.assignments) <= len(car.assignments)


def test_verifier_flags_leg_missing_from_mode_table(inst):
    t = _with_transit(inst, _wids(inst), extra=0)
    t["worker_travel"]["transit"] = t["worker_travel"]["transit"][:1]
    assert "ROUTE_UNKNOWN" in verify(t, inst["assignments"]).codes()


def test_missing_transit_leg_is_route_unknown_not_car(inst):
    t = _with_transit(inst, _wids(inst), extra=0)
    target = inst["requests"][1]["home_node"]
    t["worker_travel"]["transit"] = [r for r in t["worker_travel"]["transit"] if r["to_node"] != target]
    for algo in (request_order, nearest):
        res = algo(t)
        assert inst["requests"][1]["request_id"] not in {a["request_id"] for a in res.assignments}
        assert verify(t, res.assignments).ok
    res = optimize(t, time_limit_sec=2)
    assert inst["requests"][1]["request_id"] not in {a["request_id"] for a in res.assignments}


# ── 확정 일정·취소 재생도 같은 규칙 ────────────────────────────────────────

def test_locked_plan_respects_transit_worker(inst):
    from planner.locked import plan_with_locks
    t = _with_transit(inst, _wids(inst))
    base = request_order(t)
    locked = base.assignments[:1]
    for strategy in ("request_order", "nearest"):
        res = plan_with_locks(t, locked, strategy)
        assert verify(t, res.assignments).ok


def test_replay_with_transit_workers_stays_valid(inst):
    from planner.reschedule import replay
    t = _with_transit(inst, set(_wids(inst)[:1]))
    for algo in ("request_order", "nearest"):
        res = replay(t, algorithm=algo, refill_strategy=algo)
        assert res.ok, res.summary()


def test_load_factor_counts_transit_pickup(inst):
    from simulator.experiment import load_factor
    t = _with_transit(inst, _wids(inst))
    assert load_factor(t) > load_factor(inst)
