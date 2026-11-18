"""병원동행 픽업창을 실제로 쓰는 집→병원 이동시간에 맞추는 규칙(planner.data.align_escort_pickup)."""
from planner.data import align_escort_pickup


def _escort(rid="R1", appt=600, buf=10, lo=530, hi=550, stay=120):
    return {"request_id": rid, "service_type": "hospital_escort", "home_node": "H1", "hospital_node": "P1",
            "appointment_min": appt, "arrival_buffer_min": buf, "earliest_start_min": lo, "latest_start_min": hi,
            "hospital_stay_min": stay, "service_minutes": stay + 80}


def test_window_follows_transit_time_and_reaches_appointment():
    r = _escort()
    n = align_escort_pickup([r], {("H1", "P1"): 52.4, ("P1", "H1"): 48.0})
    assert n == 1
    assert r["latest_start_min"] == 537 and r["earliest_start_min"] == 517      # floor(600 − 52.4 − 10), 폭 20분
    assert r["latest_start_min"] + 52.4 <= r["appointment_min"] - r["arrival_buffer_min"]
    assert r["appointment_min"] == 600 and r["hospital_stay_min"] == 120       # 예약·체류는 그대로
    assert r["service_minutes"] == 120 + 52 + 48
    assert r["pickup_rule"] == "travel_aligned"


def test_not_clipped_to_shift_start():
    """근무 시작(540) 전으로 당겨져도 자르지 않는다 — 예약을 늦추어 맡을 수 있는 것처럼 만들지 않음."""
    r = _escort(appt=570)
    align_escort_pickup([r], {("H1", "P1"): 60.0, ("P1", "H1"): 60.0})
    assert r["latest_start_min"] == 500 and r["earliest_start_min"] == 480


def test_skips_missing_legs_cards_and_household():
    a, b = _escort("R1"), _escort("R2")
    hh = {"request_id": "R3", "service_type": "household_support", "home_node": "H1",
          "earliest_start_min": 600, "latest_start_min": 720}
    n = align_escort_pickup([a, b, hh], {("H1", "P1"): 70.0}, skip_ids={"R2"})   # 돌아오는 구간 없음
    assert n == 0 and a["latest_start_min"] == 550 and b["latest_start_min"] == 550
    assert hh["earliest_start_min"] == 600
    n = align_escort_pickup([a, b], {("H1", "P1"): 70.0, ("P1", "H1"): 70.0}, skip_ids={"R2"})
    assert n == 1 and a["latest_start_min"] == 520 and b["latest_start_min"] == 550   # 카드(R2)는 담당자 값 그대로


def test_widen_windows_never_inverts_early_window():
    from simulator.experiment import widen_windows
    inst = {"workers": [{"shift_start_min": 540, "shift_end_min": 1080}],
            "requests": [dict(_escort(), earliest_start_min=480, latest_start_min=500)]}
    q = widen_windows(inst, escort_width=120)["requests"][0]
    assert q["earliest_start_min"] <= q["latest_start_min"] == 500


def test_load_day_aligns_only_when_asked():
    from pathlib import Path
    import pytest
    d = Path(__file__).resolve().parent.parent / "data" / "processed"
    if not (d / "requests.csv").exists():
        pytest.skip("생성 데이터 없음")
    from planner.data import load_day, service_dates
    day = service_dates(d)[0]
    plain = load_day(d, day, transit_share=0, align_pickup=False)
    al = load_day(d, day, travel=plain.travel, transit_share=0, align_pickup=True)
    pe = {r["request_id"]: r for r in plain.requests if r["service_type"] == "hospital_escort"}
    for r in al.requests:
        if r["service_type"] != "hospital_escort":
            continue
        t = plain.travel[(r["home_node"], r["hospital_node"])]
        assert r["latest_start_min"] + t <= r["appointment_min"] - r["arrival_buffer_min"] + 1e-9
        assert r["appointment_min"] == pe[r["request_id"]]["appointment_min"]
