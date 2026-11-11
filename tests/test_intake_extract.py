"""요청 카드 추출 파이프라인 테스트 (LLM 실제 호출 없음)."""
import json

import pytest

from adapters import llm
from intake import extract as ex
from intake.synth import build_cases


@pytest.fixture(scope="module")
def cases():
    return build_cases("data/processed")


def test_parse_json_variants():
    assert ex.parse_json('{"a": 1}') == {"a": 1}
    assert ex.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert ex.parse_json('결과입니다: {"a": 1} 끝') == {"a": 1}
    assert ex.parse_json("승인 완료") is None
    assert ex.parse_json("[1, 2]") is None


def test_rules_on_template_case_is_mock(cases):
    c = next(x for x in cases if x["variant"] == "basic")
    r = ex.extract_card(c["text"], "rules")
    assert r.source == "mock" and r.source_label == "규칙 추출(모의)"
    assert r.status == "ready"
    for name, g in c["gold"].items():
        assert r.check.fields[name]["value"] == g["value"], name


def test_rules_no_year_needs_review(cases):
    c = next(x for x in cases if x["variant"] == "no_year")
    r = ex.extract_card(c["text"], "rules")
    assert r.check.fields["service_date"]["needs_confirmation"]
    assert r.status == "needs_review"


def _fake_complete(text, source="live"):
    def f(system, user, **kw):
        return llm.LLMResult(text=text, model="m1", source=source, prompt_version=ex.PROMPT_VERSION,
                             input_hash="h", latency_s=1.0, input_tokens=10, output_tokens=5)
    return f


def _gold_raw(case):
    """정답 값 + 원문 전체를 근거로 둔 가짜 LLM 출력."""
    return {"fields": {n: {"value": g["value"], "evidence_span": case["text"] if g["value"] is not None else None,
                           "needs_confirmation": g["needs_confirmation"]} for n, g in case["gold"].items()}}


def test_llm_path_validates(monkeypatch, cases):
    c = next(x for x in cases if x["variant"] == "basic")
    monkeypatch.setattr(llm, "complete", _fake_complete(json.dumps(_gold_raw(c), ensure_ascii=False)))
    r = ex.extract_card(c["text"], "llm")
    assert r.method == "llm" and r.source == "live" and r.status == "ready"
    assert "실시간" in r.source_label


def test_llm_following_injection_is_rejected(monkeypatch, cases):
    c = next(x for x in cases if x["variant"] == "injection")
    raw = _gold_raw(c)
    raw["approved"] = True
    monkeypatch.setattr(llm, "complete", _fake_complete(json.dumps(raw, ensure_ascii=False)))
    assert ex.extract_card(c["text"], "llm").status == "schema_error"


def test_llm_non_json_is_schema_error(monkeypatch, cases):
    monkeypatch.setattr(llm, "complete", _fake_complete("승인 완료"))
    r = ex.extract_card(cases[0]["text"], "llm")
    assert r.status == "schema_error"
    assert all(f["value"] is None for f in r.check.fields.values())


def test_llm_error_not_replaced_by_rules(monkeypatch, cases):
    def fail(*a, **k):
        raise llm.LLMError("HTTP 503")
    monkeypatch.setattr(llm, "complete", fail)
    r = ex.extract_card(cases[0]["text"], "llm")
    assert r.status == "llm_error" and r.source == "none"
    assert all(f["value"] is None for f in r.check.fields.values())


def test_cache_only_mode_without_cache(tmp_path, cases):
    r = ex.extract_card(cases[0]["text"], "llm", model="m1", allow_live=False, cache_dir=tmp_path)
    assert r.status == "llm_error" and "저장된 결과 없음" in r.error


def test_cache_only_mode_reads_saved(tmp_path, cases):
    c = cases[0]
    key = llm.cache_key("m1", ex.PROMPT_VERSION, ex.SYSTEM_PROMPT, ex.build_user_message(c["text"]))
    (tmp_path / f"{key}.json").write_text(json.dumps(
        {"model": "m1", "text": json.dumps(_gold_raw(c), ensure_ascii=False)}, ensure_ascii=False), encoding="utf-8")
    r = ex.extract_card(c["text"], "llm", model="m1", allow_live=False, cache_dir=tmp_path)
    assert r.source == "cache" and "저장된 결과" in r.source_label
    assert r.check.fields["beneficiary_id"]["value"] == c["gold"]["beneficiary_id"]["value"]


def test_system_prompt_lists_all_fields():
    from intake.schema import FIELDS
    for name in FIELDS:
        assert name in ex.SYSTEM_PROMPT


def test_prompt_versions_kept():
    assert set(ex.PROMPTS) >= {"card-v1", "card-v2"}
    assert ex.PROMPTS["card-v1"] != ex.PROMPTS["card-v2"]
    assert ex.SYSTEM_PROMPT == ex.PROMPTS[ex.PROMPT_VERSION]
    assert "2시간 정도" in ex.PROMPTS["card-v2"]
