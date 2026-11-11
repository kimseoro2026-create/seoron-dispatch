"""정확도 평가 지표 테스트 — 특히 '위험한 ready 카드'를 잡는지."""
import copy

import pandas as pd
import pytest

from intake.evaluate import score_case, summarize, values_match
from intake.extract import ExtractResult
from intake.synth import build_cases
from intake.validate import validate_card


@pytest.fixture(scope="module")
def case():
    return next(c for c in build_cases("data/processed") if c["variant"] == "basic"
                and c["gold"]["service_type"]["value"] == "household_support")


def _raw_from_gold(case, override=None):
    raw = {"fields": {n: {"value": g["value"], "evidence_span": case["text"] if g["value"] is not None else None,
                          "needs_confirmation": g["needs_confirmation"]} for n, g in case["gold"].items()}}
    for name, value in (override or {}).items():
        raw["fields"][name]["value"] = value
    return raw


def _result(case, raw):
    return ExtractResult(check=validate_card(case["text"], raw), method="llm", source="live", model="m")


def test_values_match_text_containment():
    assert values_match("mobility_note", "이동: 보행기 사용.", "보행기 사용")
    assert values_match("hospital_name", "한림병원", "한림병원")
    assert not values_match("hospital_name", "", "한림병원")
    assert not values_match("service_minutes", 120, 90)
    assert values_match("service_minutes", None, None)
    assert not values_match("service_minutes", None, 120)


def test_perfect_card(case):
    res = _result(case, _raw_from_gold(case))
    s = summarize(pd.DataFrame(score_case(case, res)), {case["case_id"]: res})
    assert s["value_accuracy"] == 1.0 and s["wrong_value"] == 0 and s["unsafe_ready"] == 0


def test_unsafe_ready_detected(case):
    # 원문 전체를 근거로 둬서 검사를 통과하지만 값이 틀린 카드 → ready 인데 틀림 = 위험
    raw = _raw_from_gold(case, {"service_minutes": case["gold"]["service_minutes"]["value"] + 30})
    res = _result(case, raw)
    assert res.status == "ready"
    s = summarize(pd.DataFrame(score_case(case, res)), {case["case_id"]: res})
    assert s["unsafe_ready"] == 1 and s["wrong_value"] == 1


def test_missed_and_invented(case):
    raw = _raw_from_gold(case, {"latest_start": None})
    raw["fields"]["latest_start"]["evidence_span"] = None
    raw["fields"]["hospital_stay_minutes"] = {"value": 100, "evidence_span": case["text"], "needs_confirmation": False}
    res = _result(case, raw)
    s = summarize(pd.DataFrame(score_case(case, res)), {case["case_id"]: res})
    assert s["missed"] == 1 and s["invented"] == 1
    assert s["unsafe_ready"] == 0  # 누락이 있으면 검토 필요로 막힌다


def test_flag_recall_safety_net():
    """추출기가 '확인 필요'를 빠뜨려도, 근거에 연도가 없으면 검사가 표시를 붙여 재현율이 유지된다."""
    import datetime as dt
    c = next(x for x in build_cases("data/processed") if x["variant"] == "no_year")
    d = dt.date.fromisoformat(c["gold"]["service_date"]["value"])
    raw = _raw_from_gold(c)
    raw["fields"]["service_date"]["evidence_span"] = f"{d.month}월 {d.day}일"
    raw["fields"]["service_date"]["needs_confirmation"] = False
    res = _result(c, raw)
    s = summarize(pd.DataFrame(score_case(c, res)), {c["case_id"]: res})
    assert s["flags_expected"] == 1 and s["flag_recall"] == 1.0
    assert res.status == "needs_review"
