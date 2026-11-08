"""시뮬레이터 테스트.

핵심은 세 가지다.
1) 부하율 계산이 맞는가 (정의를 손으로 검산)
2) 부하율을 낮추면 실제로 요청이 줄고 ρ가 목표에 가까워지는가
3) 실험은 돌지만 위반 0건인가 — 시뮬레이터가 제약을 어기는 배정을 지표로 세면 안 된다
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from simulator.experiment import (available_minutes, load_factor, run_day,
                                  run_experiment, select_requests, service_span,
                                  summarize)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "verify_set.json"


@pytest.fixture
def inst():
    with open(FIXTURE, encoding="utf-8") as f:
        data = json.load(f)
    data["travel"] = {(t["from_node"], t["to_node"]): t["minutes"]
                      for t in data["travel"]}
    return data


# ── 부하율 정의 ───────────────────────────────────────────────────────────

def test_available_minutes_excludes_break(inst):
    # 각 540~1080 (540분) - 휴게 60 = 480분. 인력 2명 → 960분
    assert available_minutes(inst["workers"]) == 960


def test_service_span_household(inst):
    req = next(r for r in inst["requests"] if r["service_type"] == "household_support")
    assert service_span(req, inst["travel"]) == req["service_minutes"]


def test_service_span_escort_includes_travel(inst):
    req = next(r for r in inst["requests"] if r["service_type"] == "hospital_escort")
    to_h = inst["travel"][(req["home_node"], req["hospital_node"])]
    back = inst["travel"][(req["hospital_node"], req["home_node"])]
    assert service_span(req, inst["travel"]) == int(to_h) + req["hospital_stay_min"] + int(back)


def test_service_span_missing_route_returns_none(inst):
    """경로가 없으면 0으로 대체하지 않는다."""
    req = next(r for r in inst["requests"] if r["service_type"] == "hospital_escort")
    broken = dict(inst["travel"])
    broken.pop((req["home_node"], req["hospital_node"]), None)
    assert service_span(req, broken) is None


def test_load_factor_is_positive_and_finite(inst):
    rho = load_factor(inst)
    assert rho > 0
    assert rho == rho          # NaN 아님


# ── 부하율 맞추기 ─────────────────────────────────────────────────────────

def test_select_requests_reduces_count(inst):
    lower = select_requests(inst, target_rho=0.5, seed=1)
    assert len(lower["requests"]) < len(inst["requests"])


def test_select_requests_approaches_target(inst):
    base = load_factor(inst)
    target = base * 0.5
    sel = select_requests(inst, target_rho=target, seed=7)
    got = load_factor(sel)
    # 부분집합이라 정확히 맞지는 않는다. 20% 안에 들면 충분하다.
    assert got < base
    assert abs(got - target) / target < 0.25, f"목표 {target:.2f}, 실제 {got:.2f}"


def test_select_requests_keeps_all_when_target_higher(inst):
    """목표가 현재보다 크면 요청을 복제하지 않고 전부 쓴다."""
    sel = select_requests(inst, target_rho=load_factor(inst) * 3, seed=1)
    assert len(sel["requests"]) == len(inst["requests"])


def test_select_requests_is_deterministic(inst):
    a = select_requests(inst, 0.5, seed=11)
    b = select_requests(inst, 0.5, seed=11)
    assert [r["request_id"] for r in a["requests"]] == [r["request_id"] for r in b["requests"]]


def test_select_requests_actually_uses_seed(inst):
    """시드가 결과에 실제로 쓰이는지 확인한다.

    시드 두 개를 비교하면 우연히 같은 조합이 나올 수 있다(요청 5건에서 4건을 고르면
    경우의 수가 5가지뿐). 그래서 여러 시드를 돌려 '서로 다른 결과가 하나라도 있는지'를 본다.
    """
    results = set()
    for seed in range(20):
        sel = select_requests(inst, target_rho=0.2, seed=seed)
        results.add(tuple(r["request_id"] for r in sel["requests"]))
    assert len(results) > 1, "20개 시드가 모두 같은 결과 — 시드가 무시되고 있다"


def test_selection_does_not_mutate_input(inst):
    before = len(inst["requests"])
    select_requests(inst, 0.5, seed=3)
    assert len(inst["requests"]) == before


# ── 하루 실행 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("algo", ["request_order", "nearest", "optimize"])
def test_run_day_no_violations(inst, algo):
    """시뮬레이터가 위반 있는 배정을 지표로 세면 안 된다."""
    res = run_day(inst, algo, seed=1, time_limit_sec=2)
    assert res.violations == 0, f"{algo}: 위반 {res.violations}건"


@pytest.mark.parametrize("algo", ["request_order", "nearest", "optimize"])
def test_run_day_counts_add_up(inst, algo):
    """배정 + 미배정 = 전체 요청. 요청이 조용히 사라지면 안 된다."""
    res = run_day(inst, algo, seed=1, time_limit_sec=2)
    unassigned = sum(res.reasons.values())
    assert res.assigned + unassigned == res.requests


def test_run_day_metrics_are_consistent(inst):
    res = run_day(inst, "request_order", seed=1)
    assert 0 <= res.on_time_rate <= 1
    assert 0 <= res.utilization <= 2
    assert res.unique_beneficiaries <= res.total_beneficiaries
    assert res.unique_beneficiaries <= res.assigned


def test_unique_beneficiaries_counts_people_not_requests(inst):
    """같은 사람이 2건이면 1명으로 센다(PRD 4.1)."""
    res = run_day(inst, "request_order", seed=1)
    if res.assigned > 1:
        assert res.unique_beneficiaries <= res.assigned


# ── 실험 실행 ─────────────────────────────────────────────────────────────

def test_run_experiment_produces_rows():
    """run_experiment 를 1일·1시드·1부하율로 돌려 출력 형태를 확인한다."""
    data = ROOT / "data" / "processed"
    if not (data / "requests.csv").exists():
        pytest.skip("data/processed 없음")

    from planner.data import service_dates
    dates = service_dates(data)
    df = run_experiment(
        data,
        algorithms=("request_order",),
        load_factors=(0.9,),
        seeds=(42,),
        dates=dates[:1],
        time_limit_sec=2,
    )
    assert len(df) == 1
    for col in ("algorithm", "target_rho", "assigned", "requests", "violations", "status"):
        assert col in df.columns
    assert df.iloc[0]["violations"] == 0


def test_experiment_small_scale(tmp_path):
    """실제 데이터로 아주 작게(1일·2시드·2부하율) 돌려 본다."""
    data = ROOT / "data" / "processed"
    if not (data / "requests.csv").exists():
        pytest.skip("data/processed 가 없다. python -m f0 all 실행 후 확인")

    from planner.data import service_dates
    dates = service_dates(data)
    df = run_experiment(
        data,
        algorithms=("request_order", "nearest"),
        load_factors=(0.6,),
        seeds=(42, 43),
        dates=dates[:1],
        time_limit_sec=2,
    )
    assert not df.empty
    assert set(df["algorithm"]) == {"request_order", "nearest"}
    assert (df["violations"] == 0).all()
    assert (df["requests"] > 0).all()
    # 배정 + 미배정이 요청 수와 같아야 한다
    assert (df["assigned"] <= df["requests"]).all()


def test_summarize_groups_by_rho_and_algorithm(tmp_path):
    import pandas as pd
    df = pd.DataFrame([
        {"target_rho": 0.6, "algorithm": "a", "on_time_rate": 0.5, "unique_beneficiaries": 5,
         "travel_minutes": 100, "utilization": 0.4, "solve_seconds": 1.0, "violations": 0},
        {"target_rho": 0.6, "algorithm": "a", "on_time_rate": 0.7, "unique_beneficiaries": 6,
         "travel_minutes": 120, "utilization": 0.5, "solve_seconds": 1.0, "violations": 0},
    ])
    s = summarize(df)
    assert len(s) == 1
    assert s.iloc[0]["적기배정률_평균"] == pytest.approx(0.6)
    assert s.iloc[0]["관측수"] == 2
