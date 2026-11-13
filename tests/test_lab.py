"""효과 검증 실험실(W09)·데이터 연결 상태(W11)·다음 주 수요(FR-39)."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from simulator.lab import LabConfig, apply_assumptions, run_lab, seed_summary

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "processed"
HAS_DATA = (DATA / "requests.csv").exists()


def _inst():
    return {"workers": [], "travel": {("A", "B"): 10.0, ("B", "A"): 20.0},
            "requests": [{"request_id": f"R{i}", "service_type": "hospital_escort" if i % 2 else "household_support",
                          "hospital_stay_min": 100, "service_minutes": 60, "cancelled_same_day": False,
                          "cancel_notice_min_before": None, "earliest_start_min": 600} for i in range(200)]}


# ── 가정 바꾸기(FR-36) ────────────────────────────────────────────────────
def test_travel_and_stay_factors():
    out = apply_assumptions(_inst(), LabConfig(travel_factor=1.3, stay_factor=0.8), seed=42)
    assert out["travel"][("A", "B")] == 13.0 and out["travel"][("B", "A")] == 26.0
    esc = [r for r in out["requests"] if r["service_type"] == "hospital_escort"]
    assert all(r["hospital_stay_min"] == 80 for r in esc)
    assert all(r["service_minutes"] == 60 for r in out["requests"] if r["service_type"] == "household_support")


def test_cancel_rate_is_seeded_and_close_to_rate():
    a = apply_assumptions(_inst(), LabConfig(cancel_rate=0.2), seed=42)
    b = apply_assumptions(_inst(), LabConfig(cancel_rate=0.2), seed=42)
    c = apply_assumptions(_inst(), LabConfig(cancel_rate=0.2), seed=43)
    ids = lambda x: [r["request_id"] for r in x["requests"] if r["cancelled_same_day"]]
    assert ids(a) == ids(b) and ids(a) != ids(c)
    assert 25 <= len(ids(a)) <= 55
    assert all(r["cancel_notice_min_before"] in (30, 60, 120, 240) for r in a["requests"] if r["cancelled_same_day"])


def test_default_config_changes_nothing():
    inst = _inst()
    assert apply_assumptions(inst, LabConfig(), seed=1) == inst


# ── 시드별 요약(FR-37) ────────────────────────────────────────────────────
def test_seed_summary_mean_range_failures():
    df = pd.DataFrame([
        {"target_rho": 0.9, "algorithm": "nearest", "seed": 42, "service_date": "d1", "unique_beneficiaries": 30,
         "on_time_rate": 0.6, "travel_minutes": 300, "violations": 0, "status": "ok", "assigned": 30, "requests": 50},
        {"target_rho": 0.9, "algorithm": "nearest", "seed": 43, "service_date": "d1", "unique_beneficiaries": 34,
         "on_time_rate": 0.7, "travel_minutes": 320, "violations": 0, "status": "ok", "assigned": 34, "requests": 50},
        {"target_rho": 0.9, "algorithm": "optimize", "seed": 42, "service_date": "d1", "unique_beneficiaries": 0,
         "on_time_rate": 0.0, "travel_minutes": 0, "violations": 0, "status": "timeout", "assigned": 0, "requests": 50},
    ])
    per_seed, summ = seed_summary(df)
    assert len(per_seed) == 3
    row = summ[(summ["방식"] == "근거리")].iloc[0]
    assert (row["고유 인원 평균"], row["고유 인원 최소"], row["고유 인원 최대"], row["실패 횟수"]) == (32.0, 30, 34, 0)
    assert summ[summ["방식"] == "AI 최적화"].iloc[0]["실패 횟수"] == 1


@pytest.mark.skipif(not HAS_DATA, reason="생성 데이터 없음")
def test_run_lab_saves_results_assumptions_and_record(tmp_path):
    from store import Store
    s = Store(tmp_path / "s.db")
    cfg = LabConfig(n_days=1, seeds=[42], rhos=[0.9], algorithms=["request_order", "nearest"], travel_factor=1.3)
    out = run_lab(cfg, DATA, out_dir=tmp_path, store=s, actor="테스트")
    assert out["raw"].shape[0] == 2 and set(out["raw"]["violations"]) == {0}
    meta = json.loads(Path(out["assumptions_path"]).read_text(encoding="utf-8"))
    assert meta["config"]["travel_factor"] == 1.3 and meta["input_hash"] and meta["engine_version"]
    rec = s.experiments()[0]
    assert rec["result_path"] == str(out["raw_path"]) and rec["config"]["seeds"] == [42]


# ── 데이터 연결 상태(W11) ─────────────────────────────────────────────────
def test_adapter_states_never_show_secret_values(monkeypatch):
    from adapters.status import adapter_states
    monkeypatch.setenv("KAKAO_REST_API_KEY", "secret-value-123")
    monkeypatch.delenv("KOREACONNECT_API_USER_KEY_ID", raising=False)
    rows = adapter_states(ROOT)
    blob = json.dumps(rows, ensure_ascii=False)
    assert "secret-value-123" not in blob
    names = {r["name"] for r in rows}
    assert {"병원 위치", "고령인구", "날씨", "대중교통", "차량 이동시간", "주소→좌표", "AI(요청 카드)"} <= names
    allowed = {"mock", "configured", "verified", "degraded", "disabled"}
    assert all(r["state"] in allowed for r in rows)
    kakao = next(r for r in rows if r["name"] == "주소→좌표")
    assert kakao["key"] == "설정됨"


@pytest.mark.skipif(not HAS_DATA, reason="생성 데이터 없음")
def test_adapter_states_reflect_data_evidence():
    from adapters.status import adapter_states
    rows = {r["name"]: r for r in adapter_states(ROOT)}
    h = pd.read_csv(DATA / "hospitals.csv")
    expect = "mock" if h["hospital_id"].astype(str).str.startswith("MOCK").any() else "verified"
    assert rows["병원 위치"]["state"] == expect
    assert rows["날씨"]["state"] == "disabled"           # 아직 쓰지 않는 자원은 '연결됨'으로 보이지 않는다


# ── 다음 주 수요(FR-39) ───────────────────────────────────────────────────
@pytest.mark.skipif(not HAS_DATA, reason="생성 데이터 없음")
def test_next_week_demand_separates_received_and_referrals(tmp_path):
    from simulator.demand import next_week_demand
    from store import Store
    s = Store(tmp_path / "s.db")
    s.save_card_review("k", card_id="E99", text_hash="h", extracted={}, final={"planned_discharge_date": "2026-11-05",
                       "beneficiary_id": "B0001", "service_type": "hospital_escort"}, action="보류",
                       reason="요청 시간 미정", reviewer="A", request=None)
    out = next_week_demand(s, DATA, "2026-11-02")
    assert out["days"] and all(d["service_hours"] > 0 for d in out["days"])
    assert out["days"][0]["service_date"] >= "2026-11-02" and len(out["days"]) <= 7
    assert out["referrals"] == [{"card_id": "E99", "beneficiary_id": "B0001", "planned_discharge_date": "2026-11-05",
                                 "service_type": "hospital_escort"}]
