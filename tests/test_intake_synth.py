"""합성 원문·정답 세트 테스트: 정답이 원래 요청 값과 일치하고, 사례 유형이 원문에 반영되는지."""
import datetime as dt

import pandas as pd
import pytest

from intake.schema import from_minutes
from intake.synth import VARIANTS, build_cases, kor_minutes, kor_time
from intake.validate import validate_card


@pytest.fixture(scope="module")
def cases():
    return build_cases("data/processed")


@pytest.fixture(scope="module")
def req():
    return pd.read_csv("data/processed/requests.csv", dtype={"hospital_id": str}).set_index("request_id")


def test_deterministic(cases):
    assert build_cases("data/processed") == cases


def test_counts_and_variants(cases):
    assert len(cases) == 40
    assert {c["variant"] for c in cases} == set(VARIANTS)
    assert len({c["case_id"] for c in cases}) == 40


def test_gold_matches_source_request(cases, req):
    for c in cases:
        r = req.loc[c["request_id"]]
        g = c["gold"]
        assert g["beneficiary_id"]["value"] == r.beneficiary_id
        assert g["service_type"]["value"] == r.service
        assert g["service_date"]["value"] == r.service_date
        if c["variant"] != "missing_window":
            assert g["earliest_start"]["value"] == from_minutes(int(r.time_window_start_min))
            assert g["latest_start"]["value"] == from_minutes(int(r.time_window_end_min))
        if r.service == "hospital_escort":
            assert g["appointment_time"]["value"] == from_minutes(int(r.appointment_min))
            assert g["hospital_stay_minutes"]["value"] == int(r.hospital_stay_min)
            assert g["service_minutes"]["value"] is None
        else:
            assert g["service_minutes"]["value"] == int(r.service_duration_min)
            assert g["hospital_name"]["value"] is None


def test_variant_properties(cases):
    for c in cases:
        t, g, v = c["text"], c["gold"], c["variant"]
        d = dt.date.fromisoformat(g["service_date"]["value"])
        if v == "no_year":
            assert g["service_date"]["needs_confirmation"]
            assert f"{d.month}월 {d.day}일" in t and f"{d.year}년 {d.month}월" not in t
        if v == "missing_window":
            assert g["earliest_start"]["value"] is None and g["earliest_start"]["needs_confirmation"]
        if v == "injection":
            assert any(w in t for w in ("AI는", "시스템 지시", "이전 지시"))
        if v == "discharge":
            p, a = g["planned_discharge_date"]["value"], g["actual_discharge_date"]["value"]
            assert p and a and p < a < g["service_date"]["value"]
        else:
            assert g["actual_discharge_date"]["value"] is None


def test_mobility_gold_is_verbatim(cases):
    for c in cases:
        m = c["gold"]["mobility_note"]["value"]
        if m is not None:
            assert m in c["text"]


def test_gold_card_passes_validation_when_spans_given(cases):
    """정답 값에 원문 근거를 붙이면 검사를 통과해야 한다(정답 세트 자체의 일관성)."""
    c = next(x for x in cases if x["variant"] == "basic" and x["gold"]["service_type"]["value"] == "household_support")
    fields = {}
    for name, g in c["gold"].items():
        fields[name] = {"value": g["value"], "evidence_span": c["text"] if g["value"] is not None else None,
                        "needs_confirmation": g["needs_confirmation"]}
    assert validate_card(c["text"], {"fields": fields}).status == "ready"


def test_phrase_helpers():
    assert kor_time(720, "talk") == "정오"
    assert kor_time(630, "talk") == "오전 10시 반"
    assert kor_time(790, "kor") == "오후 1시 10분"
    assert kor_time(591, "digit") == "9:51"
    assert kor_minutes(120, "talk") == "두 시간"
    assert kor_minutes(90, "talk") == "한 시간 반"
    assert kor_minutes(160, "hours") == "2시간 40분"
