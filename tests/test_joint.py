"""공동 동행 실험(EXP, PRD 12장): 기본 꺼짐, 조건이 맞는 같은 병원 동행 두 건만 한 인력이 함께 모신다.

AT-17: 꺼져 있으면 1:1 전담 그대로. 공동 인력시간은 한 번만, 어르신별 기다림은 따로. 1:1 성과에 합산하지 않는다.
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from simulator.joint import (JointConfig, build_joint_instance, expand_assignments, find_pairs,
                             run_joint_experiment)
from verifier import verify

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "processed"

NODES = ["BASE_W01", "HA", "HB", "HC", "HOSP:X", "HOSP:Y"]
DIST = {frozenset(("HA", "HB")): 5, frozenset(("HA", "HOSP:X")): 20, frozenset(("HB", "HOSP:X")): 18}


def _t(a, b):
    return DIST.get(frozenset((a, b)), 15)


def _esc(rid, ben, home, hosp, appt, stay=60, buf=10, lo=None, hi=None):
    lo = appt - 60 if lo is None else lo
    hi = appt - buf - _t(home, hosp) if hi is None else hi
    return {"request_id": rid, "beneficiary_id": ben, "service_type": "hospital_escort", "service_date": "2026-11-02",
            "home_node": home, "hospital_node": hosp, "appointment_min": appt, "arrival_buffer_min": buf,
            "hospital_stay_min": stay, "earliest_start_min": lo, "latest_start_min": hi, "service_minutes": None,
            "required_skills": ["hospital_escort"], "priority": 1, "requested_at": "t"}


def _inst(reqs):
    return {"workers": [{"worker_id": "W01", "skills": ["hospital_escort", "household_support"], "base_node": "BASE_W01",
                         "shift_start_min": 540, "shift_end_min": 1080, "break_start_min": 780, "break_minutes": 60,
                         "max_work_minutes": 480}],
            "requests": reqs,
            "travel": {(a, b): float(_t(a, b)) for a, b in itertools.permutations(NODES, 2)}}


BENS = {"BA": {"mobility": "independent", "joint_escort_ok": True},
        "BB": {"mobility": "independent", "joint_escort_ok": True},
        "BC": {"mobility": "walker", "joint_escort_ok": False}}
ON = JointConfig(enabled=True, mode="simultaneous", max_appointment_gap_min=30, max_detour_min=20, max_wait_min=60)


def test_off_means_one_to_one():                                   # AT-17
    inst = _inst([_esc("RA", "BA", "HA", "HOSP:X", 660), _esc("RB", "BB", "HB", "HOSP:X", 670)])
    assert build_joint_instance(inst, BENS, JointConfig(enabled=False))[0] is inst


def test_pairs_need_consent_mobility_same_hospital_and_close_times():
    reqs = [_esc("RA", "BA", "HA", "HOSP:X", 660), _esc("RB", "BB", "HB", "HOSP:X", 670),
            _esc("RC", "BC", "HC", "HOSP:X", 665)]
    pairs, excluded = find_pairs(_inst(reqs), BENS, ON)
    assert [(p["a"], p["b"]) for p in pairs] in ([("RA", "RB")], [("RB", "RA")])
    assert excluded["동의·이동조건"] >= 1
    far = [_esc("RA", "BA", "HA", "HOSP:X", 660), _esc("RB", "BB", "HB", "HOSP:X", 760)]
    assert find_pairs(_inst(far), BENS, ON)[0] == []
    other = [_esc("RA", "BA", "HA", "HOSP:X", 660), _esc("RB", "BB", "HB", "HOSP:Y", 665)]
    assert find_pairs(_inst(other), BENS, ON)[0] == []


def test_combined_node_respects_both_appointments_and_counts_worker_time_once():
    inst = _inst([_esc("RA", "BA", "HA", "HOSP:X", 660), _esc("RB", "BB", "HB", "HOSP:X", 670)])
    joint, pairs = build_joint_instance(inst, BENS, ON)
    nodes = [r for r in joint["requests"] if r["service_type"] == "joint_escort"]
    assert len(nodes) == 1 and len(joint["requests"]) == 1
    n = nodes[0]
    assert n["earliest_start_min"] == n["latest_start_min"]          # 두 분 예약에 맞춘 한 시각
    p = pairs[0]
    first, second = p["order"]
    arr = n["earliest_start_min"] + _t(inst["requests"][0]["home_node"] if first == "RA" else "HB",
                                       "HB" if first == "RA" else "HA") + 0
    assert p["hospital_arrive"] <= min(650, 660)                      # 예약 − 여유(둘 중 이른 쪽)
    # 1:1 두 번보다 인력 점유가 짧아야 의미가 있다(같은 인력시간을 한 번만 셈)
    solo = sum(_t(h, "HOSP:X") * 2 + 60 for h in ("HA", "HB"))
    assert n["service_minutes"] < solo
    assert p["wait"]["RA"] >= 0 and p["wait"]["RB"] >= 0


def test_engine_result_is_valid_and_expands_to_both_people():
    from planner.baseline import nearest
    inst = _inst([_esc("RA", "BA", "HA", "HOSP:X", 660), _esc("RB", "BB", "HB", "HOSP:X", 670)])
    joint, pairs = build_joint_instance(inst, BENS, ON)
    res = nearest(joint)
    assert res.assignments and not verify(joint, res.assignments).violations
    served = expand_assignments(res.assignments, pairs)
    assert set(served) == {"RA", "RB"}


def test_ride_share_mode_waits_longer_than_simultaneous():
    inst = _inst([_esc("RA", "BA", "HA", "HOSP:X", 660), _esc("RB", "BB", "HB", "HOSP:X", 670)])
    _, p_sim = build_joint_instance(inst, BENS, ON)
    _, p_ride = build_joint_instance(inst, BENS, JointConfig(enabled=True, mode="ride_share", max_appointment_gap_min=30,
                                                             max_detour_min=20, max_wait_min=200))
    assert sum(p_ride[0]["wait"].values()) > sum(p_sim[0]["wait"].values())


@pytest.mark.skipif(not (DATA / "requests.csv").exists(), reason="생성 데이터 없음")
def test_real_experiment_rows_are_separate_and_valid(tmp_path):
    from store import Store
    s = Store(tmp_path / "s.db")
    out = run_joint_experiment(DATA, JointConfig(enabled=True), n_days=3, algorithm="nearest", out_dir=tmp_path,
                               store=s, actor="테스트")
    df = out["raw"]
    assert set(df["mode"]) == {"1:1", "공동 동행"} and len(df) == 6
    assert set(df["violations"]) == {0}
    exp = s.experiments()[0]
    assert exp["config"]["experiment"] == "joint_escort"            # 기본 실험과 다른 실행 ID·종류(FR-38)
