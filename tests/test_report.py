"""부족 자원·비용 리포트 계산 테스트."""
import json
from pathlib import Path

import pandas as pd
import pytest

from simulator import report as rp

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "verify_set.json"


@pytest.fixture
def inst():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_paid_minutes():
    w = {"shift_start_min": 540, "shift_end_min": 1080, "break_minutes": 60}
    assert rp.paid_minutes(w) == 480
    assert rp.paid_minutes(w, "shift") == 540
    with pytest.raises(ValueError):
        rp.paid_minutes(w, "모름")


def test_added_cost(inst):
    add2 = rp.BY_KEY["add2"]
    added = rp.added_workers(inst, add2)
    assert len(added) == 2
    exp = sum(rp.paid_minutes(w) for w in added) / 60 * 13000
    assert rp.added_labor_cost_per_day(inst, add2, 13000) == pytest.approx(exp)
    # 휴게 분산은 유급시간이 같아 추가 인건비 0, 시급이 없으면 계산 불가
    assert rp.added_labor_cost_per_day(inst, rp.BY_KEY["stagger"], 13000) == 0
    assert rp.added_labor_cost_per_day(inst, add2, None) is None


def test_scenarios_keep_requests(inst):
    for s in rp.SCENARIOS:
        out = s.apply(inst)
        assert out["requests"] == inst["requests"]
        assert len(out["workers"]) == len(inst["workers"]) + s.add


def _days():
    rows = []
    for sc, uniq in (("base", 10), ("add1", 12), ("stagger", 10)):
        for day in range(2):
            rows.append(dict(scenario=sc, algorithm="optimize", assigned=uniq + 1, unique_beneficiaries=uniq,
                             fully_met_beneficiaries=uniq - 1, assigned_high=3, requests_high=4,
                             assigned_escort=2, requests_escort=5, requests=20, travel_minutes=100,
                             utilization=0.5, violations=0))
    return pd.DataFrame(rows)


def test_scenario_table_cost_per_person():
    t = rp.scenario_table(_days(), {"base": 0.0, "add1": 104000.0, "stagger": 0.0})
    assert list(t.index) == ["base", "add1", "stagger"]       # SCENARIOS 순서
    assert t.loc["add1", "d_unique"] == 2
    assert t.loc["add1", "cost_per_added_person"] == pytest.approx(52000)
    # 늘어난 인원이 0이면 1명당 비용은 N/A(나누지 않음)
    assert t.loc["stagger", "cost_per_added_person"] is None or pd.isna(t.loc["stagger", "cost_per_added_person"])
    assert t.loc["base", "high_unmet_rate"] == pytest.approx(0.25)


def test_scenario_table_unknown_cost():
    t = rp.scenario_table(_days(), {"base": None, "add1": None, "stagger": None})
    assert t["cost_per_added_person"].isna().all()


def test_unmet_breakdown():
    o = pd.DataFrame([
        dict(scenario="base", algorithm="optimize", service_date="d1", seed=1, service_type="hospital_escort",
             assigned=False, reason="TIME_WINDOW_CONFLICT", certainty="verified", start_hour=9),
        dict(scenario="base", algorithm="optimize", service_date="d1", seed=1, service_type="household_support",
             assigned=True, reason="", certainty="", start_hour=10),
        dict(scenario="base", algorithm="optimize", service_date="d2", seed=1, service_type="hospital_escort",
             assigned=True, reason="", certainty="", start_hour=9),
        dict(scenario="add1", algorithm="optimize", service_date="d1", seed=1, service_type="hospital_escort",
             assigned=False, reason="NO_CAPACITY", certainty="diagnostic", start_hour=9),
    ])
    t = rp.unmet_breakdown(o, "service_type").set_index("service_type")
    assert t.loc["hospital_escort", "unmet"] == 1 and t.loc["hospital_escort", "unmet_rate"] == 0.5
    assert t.loc["hospital_escort", "unmet_per_day"] == 0.5     # 2일
    r = rp.unmet_breakdown(o, "reason")
    assert r.iloc[0]["reason"] == "TIME_WINDOW_CONFLICT" and r.iloc[0]["share"] == 1.0


def test_cost_config_reads_assumptions():
    root = Path(__file__).resolve().parent.parent
    c = rp.load_cost_config(root)
    assert c["paid_time"] == "shift_minus_break"
    assert "monthly_salary_krw" in c and "hourly_krw" in c


# ── 행정동별 미충족률 × 가까운 인력까지 거리 (PRD 6장: 가까운 사람만 배정되는지) ──
def _access_dir(tmp_path, rows):
    pd.DataFrame({"beneficiary_id": ["B1", "B2", "B3", "B4"], "dong": ["가동", "가동", "나동", "다동"]}).to_csv(
        tmp_path / "beneficiaries.csv", index=False)
    pd.DataFrame({"worker_id": ["W1", "W2"]}).to_csv(tmp_path / "workers.csv", index=False)
    pd.DataFrame(rows, columns=["from_node", "to_node", "minutes", "source"]).to_csv(
        tmp_path / "travel_matrix.csv", index=False)
    return tmp_path


def test_nearest_worker_minutes_per_dong(tmp_path):
    d = _access_dir(tmp_path, [
        ("WORKER:W1", "BENEFICIARY:B1", 4, "osrm"), ("WORKER:W2", "BENEFICIARY:B1", 9, "osrm"),
        ("WORKER:W1", "BENEFICIARY:B2", 8, "osrm"), ("WORKER:W2", "BENEFICIARY:B2", 6, "osrm"),
        ("WORKER:W1", "BENEFICIARY:B3", 12, "osrm"), ("WORKER:W2", "BENEFICIARY:B3", 15, "osrm"),
        ("BENEFICIARY:B4", "WORKER:W1", 3, "osrm")])        # 방향이 반대인 구간만 있음 → 경로 없음
    a = rp.nearest_worker_minutes(d)
    assert a["source"] == "osrm"
    t = a["by_area"].set_index("area")
    assert t.loc["가동", "near_worker_min"] == 5.0          # B1 4분, B2 6분 → 중앙값 5
    assert t.loc["나동", "near_worker_min"] == 12.0
    assert pd.isna(t.loc["다동", "near_worker_min"])          # 0분으로 채우지 않음
    assert t.loc["다동", "no_route"] == 1


def test_rank_correlation():
    assert rp.rank_corr([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert rp.rank_corr([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert rp.rank_corr([1, 2], [1, 2]) is None              # 3곳 미만이면 계산하지 않음
    assert rp.rank_corr([1, 2, 3], [5, 5, 5]) is None         # 한쪽이 모두 같으면 계산 불가
    assert rp.rank_corr([1, 2, None, 4], [1, 2, 3, 4]) == pytest.approx(1.0)   # 빈 값은 빼고


def test_area_distance_table(tmp_path):
    d = _access_dir(tmp_path, [
        ("WORKER:W1", "BENEFICIARY:B1", 4, "osrm"), ("WORKER:W1", "BENEFICIARY:B2", 6, "osrm"),
        ("WORKER:W1", "BENEFICIARY:B3", 12, "osrm"), ("WORKER:W1", "BENEFICIARY:B4", 20, "osrm")])
    o = pd.DataFrame({
        "scenario": "base", "service_date": "2026-12-01", "seed": 42,
        "algorithm": ["nearest"] * 3 + ["optimize"] * 3,
        "area": ["가동", "나동", "다동"] * 2,
        "assigned": [True, False, False, True, True, False]})
    r = rp.area_distance(o, d)
    t = r["table"].set_index("area")
    assert t.loc["다동", "near_worker_min"] == 20.0
    assert t.loc["다동", "unmet_rate_nearest"] == 1.0 and t.loc["가동", "unmet_rate_optimize"] == 0.0
    assert r["corr"]["nearest"] == pytest.approx(0.866, abs=1e-3)
    assert r["corr"]["optimize"] == pytest.approx(0.866, abs=1e-3)
    assert r["n_areas"] == 3 and r["source"] == "osrm"
