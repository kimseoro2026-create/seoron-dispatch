"""확인된 요청 카드 → 배정 요청 변환 테스트.

지어내지 않는다: 모르는 대상자·병원은 막고, 비슷한 병원 이름으로 추측하지 않는다.
"""
import pandas as pd
import pytest

from intake.to_request import card_to_request, merge_card_requests


@pytest.fixture
def ref(tmp_path):
    pd.DataFrame({"beneficiary_id": ["B0001", "B0002"], "lat": [37.2, 37.21], "lon": [127.0, 127.01]}) \
        .to_csv(tmp_path / "beneficiaries.csv", index=False)
    pd.DataFrame({"hospital_id": ["H1", "H2"], "name": ["가상종합병원", "가상의원"],
                  "lat": [37.3, 37.31], "lon": [127.1, 127.11]}).to_csv(tmp_path / "hospitals.csv", index=False)
    return tmp_path


def _household(**kw):
    f = {"beneficiary_id": "B0001", "service_type": "household_support", "service_date": "2026-11-20",
         "earliest_start": "10:00", "latest_start": "12:00", "service_minutes": 120}
    f.update(kw)
    return f


def _escort(**kw):
    f = {"beneficiary_id": "B0002", "service_type": "hospital_escort", "service_date": "2026-11-20",
         "earliest_start": "09:30", "latest_start": "09:50", "hospital_name": "가상종합병원",
         "appointment_time": "10:30", "hospital_stay_minutes": 140}
    f.update(kw)
    return f


def test_household_card_becomes_request(ref):
    r, probs = card_to_request(_household(), ref, card_id="E01", confirmed_at="2026-11-19T11:42")
    assert probs == []
    assert r["request_id"] == "카드-E01"
    assert r["home_node"] == "BENEFICIARY:B0001"
    assert (r["earliest_start_min"], r["latest_start_min"], r["service_minutes"]) == (600, 720, 120)
    assert r["required_skills"] == ["household_support"]
    assert r["priority"] == 1 and r["requested_at"] == "2026-11-19T11:42"
    assert r["source"] == "card"


def test_escort_card_uses_exact_hospital_and_config_buffer(ref):
    r, probs = card_to_request(_escort(), ref, card_id="E02", confirmed_at="2026-11-19T12:00")
    assert probs == []
    assert r["hospital_node"] == "HOSP:H1"
    assert r["appointment_min"] == 630 and r["hospital_stay_min"] == 140
    assert r["arrival_buffer_min"] == 10      # config/region.yaml card_intake


def test_unknown_hospital_is_blocked_not_guessed(ref):
    r, probs = card_to_request(_escort(hospital_name="가상종합"), ref, card_id="E03", confirmed_at="x")
    assert r is None
    assert any("병원" in p for p in probs)


def test_unknown_beneficiary_is_blocked(ref):
    r, probs = card_to_request(_household(beneficiary_id="B9999"), ref, card_id="E04", confirmed_at="x")
    assert r is None
    assert any("대상자" in p for p in probs)


def test_missing_required_field_is_blocked(ref):
    r, probs = card_to_request(_household(service_minutes=None), ref, card_id="E05", confirmed_at="x")
    assert r is None and probs


def test_window_order_is_checked(ref):
    r, probs = card_to_request(_household(earliest_start="13:00", latest_start="12:00"), ref,
                               card_id="E06", confirmed_at="x")
    assert r is None and any("시각" in p for p in probs)


@pytest.mark.parametrize("discharged, prio", [("2026-11-10", 2), ("2026-11-06", 2), ("2026-11-01", 1), (None, 1)])
def test_priority_from_confirmed_discharge_date(ref, discharged, prio):
    r, _ = card_to_request(_household(actual_discharge_date=discharged), ref, card_id="E07", confirmed_at="x")
    assert r["priority"] == prio
    if prio == 2:
        assert "퇴원" in r["priority_reason"]


def test_merge_replaces_same_beneficiary_service_and_adds_new():
    base = [{"request_id": "R1", "beneficiary_id": "B0001", "service_type": "household_support"},
            {"request_id": "R2", "beneficiary_id": "B0003", "service_type": "household_support"}]
    cards = [{"request_id": "카드-E01", "beneficiary_id": "B0001", "service_type": "household_support"},
             {"request_id": "카드-N01", "beneficiary_id": "B0002", "service_type": "hospital_escort"}]
    merged, replaced = merge_card_requests(base, cards)
    ids = [r["request_id"] for r in merged]
    assert ids == ["R2", "카드-E01", "카드-N01"]
    assert replaced == {"카드-E01": "R1"}
