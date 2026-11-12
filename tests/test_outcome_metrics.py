"""요청별 결과와 우선순위·권역·전체 충족 지표 테스트 (PRD 4.1, CLAUDE.md 7절)."""
import json
from pathlib import Path

import pytest

from simulator.experiment import outcome_rows, run_day, run_experiment

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "verify_set.json"


@pytest.fixture
def inst():
    d = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # B01 이 두 건(R01, R02)을 가진 사람이 되도록 바꾼다
    for r in d["requests"]:
        if r["request_id"] == "R02":
            r["beneficiary_id"] = "B01"
    return d


def test_outcome_rows_one_per_request(inst):
    rows = outcome_rows(inst, assigned={"R01": "W01"},
                        unassigned=[{"request_id": "R02", "reason": "NO_CAPACITY", "certainty": "diagnostic"}])
    assert [r["request_id"] for r in rows] == [r["request_id"] for r in inst["requests"]]
    by = {r["request_id"]: r for r in rows}
    assert by["R01"]["assigned"] and by["R01"]["worker_id"] == "W01" and by["R01"]["reason"] == ""
    assert not by["R02"]["assigned"] and by["R02"]["reason"] == "NO_CAPACITY"
    assert by["R02"]["certainty"] == "diagnostic"
    # 사유가 없는 미배정은 지어내지 않고 UNKNOWN 으로 둔다
    assert by["R03"]["reason"] == "UNKNOWN" and by["R03"]["certainty"] == "unknown"
    assert by["R01"]["start_hour"] == inst["requests"][0]["earliest_start_min"] // 60


def test_area_attached_when_given(inst):
    rows = outcome_rows(inst, assigned={}, unassigned=[], area_of={"B01": "가상동1"})
    by = {r["request_id"]: r for r in rows}
    assert by["R01"]["area"] == "가상동1" and by["R02"]["area"] == "가상동1"
    assert by["R03"]["area"] == ""     # 모르는 권역은 빈 값(추정하지 않음)


@pytest.mark.parametrize("algo", ["request_order", "nearest", "optimize"])
def test_day_metrics_consistent(inst, algo):
    res = run_day(inst, algo, time_limit_sec=2)
    rows = res.outcomes
    assert len(rows) == res.requests
    assert sum(r["assigned"] for r in rows) == res.assigned
    high = [r for r in rows if r["priority"] >= 2]
    assert res.requests_high == len(high)
    assert res.assigned_high == sum(r["assigned"] for r in high)
    # 전체 충족 인원: 그날 요청이 모두 배정된 사람 수. 고유 인원보다 클 수 없다.
    people = {}
    for r in rows:
        people.setdefault(r["beneficiary_id"], []).append(r["assigned"])
    assert res.fully_met_beneficiaries == sum(all(v) for v in people.values())
    assert res.fully_met_beneficiaries <= res.unique_beneficiaries
    row = res.as_row()
    for k in ("requests_high", "assigned_high", "fully_met_beneficiaries"):
        assert k in row
    assert "outcomes" not in row


def test_partial_person_not_fully_met(inst):
    # B01 의 두 건 중 한 건만 배정되면 고유 인원에는 들어가지만 전체 충족은 아니다
    rows = outcome_rows(inst, assigned={"R01": "W01"}, unassigned=[])
    from simulator.experiment import fully_met
    assert fully_met(rows) == 0
    rows = outcome_rows(inst, assigned={"R01": "W01", "R02": "W02"}, unassigned=[])
    assert fully_met(rows) == 1


def test_run_experiment_collects_outcomes(tmp_path):
    data = Path(__file__).resolve().parent.parent / "data" / "processed"
    if not (data / "requests.csv").exists():
        pytest.skip("생성 데이터 없음")
    from planner.data import service_dates
    sink: list = []
    df = run_experiment(data, algorithms=("nearest",), load_factors=[0.6], seeds=[42],
                        dates=service_dates(data)[:1], outcome_sink=sink)
    assert len(sink) == int(df["requests"].sum())
    assert {"service_date", "seed", "target_rho", "algorithm", "area"} <= set(sink[0])
    assert all(r["area"] for r in sink)   # 생성 데이터는 모든 대상자에 행정동이 있다


def _one(req_window, brk_start=720, brk_min=60, skills=("hospital_escort", "household_support")):
    from simulator.experiment import idle_check
    w = {"worker_id": "W1", "skills": list(skills), "base_node": "A", "shift_start_min": 540,
         "shift_end_min": 1080, "break_start_min": brk_start, "break_minutes": brk_min, "max_work_minutes": 480}
    r = {"request_id": "R", "beneficiary_id": "B", "service_type": "household_support", "home_node": "H",
         "earliest_start_min": req_window[0], "latest_start_min": req_window[1], "service_minutes": 120,
         "required_skills": ["household_support"], "priority": 1}
    travel = {("A", "H"): 10.0, ("H", "A"): 10.0, ("H", "H"): 0.0}
    return idle_check(r, [w], travel)


def test_idle_check_categories():
    from simulator import experiment as ex
    assert _one((600, 600)) == ex.IDLE_CAPACITY            # 10~12시: 빈 일정이면 가능
    assert _one((660, 690)) == ex.IDLE_BREAK               # 11시대 시작 2시간 → 12시 휴게와 겹침
    assert _one((1020, 1020)) == ex.IDLE_WINDOW            # 17시 시작 2시간 → 근무 종료 넘김(휴게 무관)
    assert _one((600, 600), skills=("hospital_escort",)) == ex.IDLE_NO_SKILL


def test_idle_check_only_on_unassigned(inst):
    res = run_day(inst, "nearest")
    for o in res.outcomes:
        assert (o["idle_check"] == "") == o["assigned"]
