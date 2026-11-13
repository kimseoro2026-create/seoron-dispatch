"""확정 일정 잠금(PRD FR-20, 6.3-8): 확정된 배정은 인력·도착 시각을 그대로 두고 나머지만 계산한다. 세 방식 모두."""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from planner.locked import plan_with_locks
from planner.optimize import optimize
from verifier import verify

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "processed"
NODES = ["BASE_W01", "BASE_W02", "H1", "H2", "H3", "H4"]


def _w(wid):
    return {"worker_id": wid, "skills": ["household_support"], "base_node": f"BASE_{wid}",
            "shift_start_min": 540, "shift_end_min": 1080, "break_start_min": 780, "break_minutes": 60,
            "max_work_minutes": 480}


def _r(rid, home, lo, hi, svc, pr=1, at="2026-10-30T10:00"):
    return {"request_id": rid, "beneficiary_id": "B" + rid, "service_type": "household_support",
            "service_date": "2026-11-02", "home_node": home, "earliest_start_min": lo, "latest_start_min": hi,
            "service_minutes": svc, "required_skills": ["household_support"], "priority": pr, "requested_at": at}


def _inst():
    travel = [{"from_node": a, "to_node": b, "minutes": 10 if {a[-1], b[-1]} != {"1", "2"} else 30}
              for a, b in itertools.permutations(NODES, 2)]
    return {"workers": [_w("W01"), _w("W02")],
            "requests": [_r("R1", "H1", 600, 700, 60), _r("R2", "H2", 600, 700, 60),
                         _r("R3", "H3", 600, 900, 60), _r("R4", "H4", 900, 960, 60)],
            "travel": travel}


LOCK = [{"request_id": "R1", "worker_id": "W02", "arrive_min": 650}]   # 일부러 '먼' 인력·늦은 시각에 확정


@pytest.mark.parametrize("strategy", ["request_order", "nearest"])
def test_baselines_keep_locked_and_plan_rest(strategy):
    inst = _inst()
    res = plan_with_locks(inst, LOCK, strategy)
    got = {a["request_id"]: a for a in res.assignments}
    assert got["R1"] == {"request_id": "R1", "worker_id": "W02", "arrive_min": 650}
    assert not verify(inst, res.assignments).violations
    assert len(got) + len(res.unassigned) == 4


def test_optimize_keeps_locked_exactly():
    inst = _inst()
    res = optimize(inst, time_limit_sec=3, locked=LOCK)
    got = {a["request_id"]: a for a in res.assignments}
    assert got["R1"]["worker_id"] == "W02" and got["R1"]["arrive_min"] == 650
    assert not verify(inst, res.assignments).violations


def test_no_locks_means_unchanged_behaviour():
    from planner.baseline import nearest, request_order
    inst = _inst()
    assert plan_with_locks(inst, [], "nearest").assignments == nearest(inst).assignments
    assert plan_with_locks(inst, [], "request_order").assignments == request_order(inst).assignments


@pytest.mark.skipif(not (DATA / "requests.csv").exists(), reason="생성 데이터 없음")
def test_real_day_locked_subset_survives_all_methods():
    from planner.baseline import nearest
    from planner.data import load_day
    inst = load_day(DATA, "2026-11-04").as_instance()
    base = nearest(inst).assignments
    locked = sorted(base, key=lambda a: a["request_id"])[::4][:8]
    for strategy in ("request_order", "nearest"):
        res = plan_with_locks(inst, locked, strategy)
        got = {a["request_id"]: a for a in res.assignments}
        assert all(got[l["request_id"]] == l for l in locked)
        assert not verify(inst, res.assignments).violations
    res = optimize(inst, time_limit_sec=5, locked=locked)
    got = {a["request_id"]: (a["worker_id"], a["arrive_min"]) for a in res.assignments}
    assert all(got[l["request_id"]] == (l["worker_id"], l["arrive_min"]) for l in locked)
    assert not verify(inst, res.assignments).violations
