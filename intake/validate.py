"""추출 결과 사후 검사 — 어떤 추출기(LLM·규칙)를 쓰든 같은 규칙을 적용한다.

규칙은 결정적 코드다. 추출기가 무엇을 내놓든:
1. JSON Schema에 맞지 않으면 카드 전체를 '형식 오류'로 두고 값을 하나도 쓰지 않는다.
2. 근거 구간이 없거나 원문에 없으면 값을 버린다(null + 확인 필요).
3. 형식이 틀린 값은 버린다.
4. 연도가 근거에 없는 날짜는 확정하지 않는다(확인 필요).
5. 이동 관련 표현은 원문 그대로만 받는다. '보행기 사용'을 '자립보행 가능'으로 바꾸면 버린다.
6. 배정에 꼭 필요한 필드가 비었거나 확인 필요면 카드는 '검토 필요'.

검사는 값을 '고치지' 않는다. 버리거나 확인 필요로 표시할 뿐이다(실패를 성공으로 바꾸지 않는다).
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

import jsonschema

from .schema import (
    BLOCKING_BY_SERVICE, BLOCKING_COMMON, CARD_JSON_SCHEMA, ESCORT_ONLY, FIELDS,
    HOUSEHOLD_ONLY, check_format, empty_fields, to_minutes,
)

ISSUE_TEXT = {
    "SCHEMA_ERROR": "추출 결과 형식이 맞지 않아 사용하지 않았습니다",
    "NO_EVIDENCE": "근거 구간이 없어 값을 비웠습니다",
    "EVIDENCE_NOT_IN_TEXT": "근거 구간이 원문에 없어 값을 비웠습니다",
    "BAD_FORMAT": "값 형식이 맞지 않아 비웠습니다",
    "NO_YEAR": "원문에 연도가 없어 날짜를 확정하지 않았습니다",
    "NOT_VERBATIM": "원문 표현과 달라 비웠습니다(원문 그대로만 받습니다)",
    "MOBILITY_JUDGMENT": "원문에 없는 이동능력 판정이라 비웠습니다",
    "MISSING_REQUIRED": "배정에 꼭 필요한 값이 원문에 없습니다",
    "TIME_ORDER": "시각 순서가 맞지 않습니다",
    "WRONG_SERVICE_FIELD": "서비스 종류와 맞지 않는 항목입니다",
    "EXTRACTOR_FLAG": "추출기가 확인 필요로 표시했습니다",
}

# 원문에 없으면 쓸 수 없는 '판정' 표현 (PRD 10.4, W03 검수)
_JUDGMENT_WORDS = ("자립보행", "독립보행", "혼자 걸", "보행 가능", "이동 가능")


@dataclass
class CardCheck:
    fields: dict
    status: str                      # ready | needs_review | schema_error
    issues: list[dict] = field(default_factory=list)   # {field, code, message}
    schema_errors: list[str] = field(default_factory=list)

    def blocking_fields(self) -> tuple[str, ...]:
        st = self.fields.get("service_type", {}).get("value")
        return BLOCKING_BY_SERVICE.get(st, BLOCKING_COMMON)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _issue(issues: list, name: str | None, code: str) -> None:
    issues.append({"field": name, "code": code, "message": ISSUE_TEXT[code]})


def _drop(f: dict) -> None:
    f["value"] = None
    f["evidence_span"] = None
    f["needs_confirmation"] = True


def validate_card(text: str, raw: dict | None) -> CardCheck:
    """원문과 추출기 출력(raw)을 받아 검사한 카드를 돌려준다. raw는 바꾸지 않는다."""
    issues: list[dict] = []
    try:
        jsonschema.validate(raw, CARD_JSON_SCHEMA)
    except jsonschema.ValidationError as e:
        fields = empty_fields()
        for name in BLOCKING_COMMON:
            fields[name]["needs_confirmation"] = True
        _issue(issues, None, "SCHEMA_ERROR")
        return CardCheck(fields=fields, status="schema_error", issues=issues,
                         schema_errors=[e.message[:200]])

    fields = copy.deepcopy(raw["fields"])
    norm_text = _norm(text)

    for name, f in fields.items():
        if f["needs_confirmation"] and f["value"] is not None:
            _issue(issues, name, "EXTRACTOR_FLAG")
        if f["value"] is None:
            f["evidence_span"] = None
            continue
        span = f["evidence_span"]
        if not span or not span.strip():
            _issue(issues, name, "NO_EVIDENCE")
            _drop(f)
            continue
        if _norm(span) not in norm_text:
            _issue(issues, name, "EVIDENCE_NOT_IN_TEXT")
            _drop(f)
            continue
        if not check_format(name, f["value"]):
            _issue(issues, name, "BAD_FORMAT")
            _drop(f)
            continue
        kind = FIELDS[name][1]
        if kind == "date" and not re.search(r"(19|20)\d{2}", span):
            _issue(issues, name, "NO_YEAR")
            f["needs_confirmation"] = True
        if name == "mobility_note":
            v = f["value"]
            if any(w in v for w in _JUDGMENT_WORDS) and v not in text and _norm(v) not in norm_text:
                _issue(issues, name, "MOBILITY_JUDGMENT")
                _drop(f)
            elif _norm(v) not in norm_text:
                _issue(issues, name, "NOT_VERBATIM")
                _drop(f)

    service = fields["service_type"]["value"]

    # 서비스 종류와 맞지 않는 항목: 값은 남기되 확인 필요(어느 쪽이 틀렸는지 코드가 정하지 않는다)
    wrong = ESCORT_ONLY if service == "household_support" else HOUSEHOLD_ONLY if service == "hospital_escort" else ()
    for name in wrong:
        if fields[name]["value"] is not None:
            _issue(issues, name, "WRONG_SERVICE_FIELD")
            fields[name]["needs_confirmation"] = True
            fields["service_type"]["needs_confirmation"] = True

    # 시각 순서
    e, l = fields["earliest_start"]["value"], fields["latest_start"]["value"]
    if e and l and to_minutes(e) > to_minutes(l):
        _issue(issues, "latest_start", "TIME_ORDER")
        fields["earliest_start"]["needs_confirmation"] = True
        fields["latest_start"]["needs_confirmation"] = True
    a = fields["appointment_time"]["value"]
    if service == "hospital_escort" and l and a and to_minutes(l) >= to_minutes(a):
        _issue(issues, "appointment_time", "TIME_ORDER")
        fields["latest_start"]["needs_confirmation"] = True
        fields["appointment_time"]["needs_confirmation"] = True

    blocking = BLOCKING_BY_SERVICE.get(service, BLOCKING_COMMON)
    for name in blocking:
        if fields[name]["value"] is None:
            if not any(i["field"] == name for i in issues):
                _issue(issues, name, "MISSING_REQUIRED")
            fields[name]["needs_confirmation"] = True

    needs = any(fields[n]["value"] is None or fields[n]["needs_confirmation"] for n in blocking)
    return CardCheck(fields=fields, status="needs_review" if needs else "ready", issues=issues)
