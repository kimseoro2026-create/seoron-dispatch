"""규칙 기반 추출기 — LLM 없이 정규식으로 요청 카드를 뽑는다.

용도: (1) 키가 없을 때의 모의(mock) 추출기, (2) LLM 정확도와 비교할 기준선.
화면·평가에서는 항상 '규칙 추출(모의)'로 표시한다. 실제 AI 추론 결과로 표시하지 않는다.
출력 형식은 LLM과 같고, 같은 사후 검사(validate_card)를 거친다.
"""
from __future__ import annotations

import re

from .schema import empty_fields, from_minutes

_TIME = re.compile(
    r"정오|(?:(오전|오후|낮)\s*)?(\d{1,2})(?::(\d{2})|시(?!간)(?:\s*(\d{1,2})분|\s*(반))?)"
)
_DUR = re.compile(
    r"(\d+)\s*시간(?:\s*(\d+)\s*분|\s*(반))?|(\d+)\s*분|(한|두|세|네)\s*시간(\s*반)?"
)
_DATE_Y = re.compile(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일|(\d{4})\.(\d{1,2})\.(\d{1,2})")
_DATE_NOY = re.compile(r"(\d{1,2})월\s*(\d{1,2})일")
_HEADER_DATE = re.compile(r"접수\s*(\d{4})-(\d{2})-(\d{2})")
_KOR = {"한": 1, "두": 2, "세": 3, "네": 4}
_MOBILITY_WORDS = ("보행기", "보행보조기", "휠체어", "지팡이")


def _time_min(m: re.Match) -> int:
    if m.group(0) == "정오":
        return 720
    ampm, h = m.group(1), int(m.group(2))
    mm = int(m.group(3) or m.group(4) or 0) + (30 if m.group(5) else 0)
    if ampm in ("오후", "낮") and h < 12:
        h += 12
    return h * 60 + mm


def _dur_min(m: re.Match) -> int:
    if m.group(1):
        return int(m.group(1)) * 60 + int(m.group(2) or 0) + (30 if m.group(3) else 0)
    if m.group(4):
        return int(m.group(4))
    return _KOR[m.group(5)] * 60 + (30 if m.group(6) else 0)


def _date(line: str, header_year: int | None, header_month: int | None):
    """(값, 근거) — 연도가 없으면 접수일로 연도를 제안한다(검사에서 '확인 필요'가 붙는다)."""
    m = _DATE_Y.search(line)
    if m:
        y, mo, d = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(4), m.group(5), m.group(6))
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}", m.group(0)
    m = _DATE_NOY.search(line)
    if m and header_year:
        mo, d = int(m.group(1)), int(m.group(2))
        y = header_year + (1 if header_month and mo < header_month else 0)
        return f"{y:04d}-{mo:02d}-{d:02d}", m.group(0)
    return None, None


def _set(f: dict, name: str, value, span: str | None) -> None:
    if value is not None and span:
        f[name] = {"value": value, "evidence_span": span, "needs_confirmation": False}


def extract_rules(text: str) -> dict:
    f = empty_fields()
    lines = text.splitlines()
    hm = _HEADER_DATE.search(text)
    hy, hmo = (int(hm.group(1)), int(hm.group(2))) if hm else (None, None)
    body = [ln for ln in lines if not ln.startswith("[상담 메모]")]

    m = re.search(r"B\d{4}", text)
    if m:
        _set(f, "beneficiary_id", m.group(0), m.group(0))
    if "병원동행" in text:
        _set(f, "service_type", "hospital_escort", "병원동행")
    elif "가사지원" in text:
        _set(f, "service_type", "household_support", "가사지원")

    for ln in body:
        if "퇴원" in ln:
            parts = re.split(r"(?<=예정)", ln, maxsplit=1)
            for part in parts:
                v, s = _date(part, hy, hmo)
                if v and "예정" in part and f["planned_discharge_date"]["value"] is None:
                    _set(f, "planned_discharge_date", v, s)
                elif v and "실제" in part:
                    _set(f, "actual_discharge_date", v, s)
            continue
        if ("희망일" in ln or "예약" in ln) and f["service_date"]["value"] is None:
            v, s = _date(ln, hy, hmo)
            _set(f, "service_date", v, s)
        times = list(_TIME.finditer(ln))
        if "예약" in ln and times:
            t = times[0]
            _set(f, "appointment_time", from_minutes(_time_min(t)), t.group(0))
            name = ln[t.end():].split("외래")[0].split("예약")[0].strip()
            if name:
                _set(f, "hospital_name", name, name)
        if ("출발" in ln or "사이" in ln) and len(times) >= 2 and "예약" not in ln:
            _set(f, "earliest_start", from_minutes(_time_min(times[0])), times[0].group(0))
            _set(f, "latest_start", from_minutes(_time_min(times[1])), times[1].group(0))
        d = _DUR.search(ln)
        if d and "머무" in ln:
            _set(f, "hospital_stay_minutes", _dur_min(d), d.group(0))
        elif d and "정도" in ln and f["service_type"]["value"] == "household_support":
            _set(f, "service_minutes", _dur_min(d), d.group(0))
        if any(w in ln for w in _MOBILITY_WORDS):
            phrase = ln.split(":", 1)[-1].strip().rstrip(".")
            _set(f, "mobility_note", phrase, phrase)
    return {"fields": f}
