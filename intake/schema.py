"""요청 카드 필드 정의와 추출 결과 JSON Schema.

카드 한 장 = 필드별 {value, evidence_span, needs_confirmation}.
- value: 원문에서 읽은 값. 원문에 없으면 null (임의 기본값으로 채우지 않는다, FR-14).
- evidence_span: 값의 근거가 된 원문 구간(그대로 복사). 값이 null이면 null.
- needs_confirmation: 담당자 확인이 필요한지.

시간은 'HH:MM', 날짜는 'YYYY-MM-DD' 문자열로 받는다. 엔진 입력(자정 기준 분)으로의 변환은
카드가 확인된 뒤 한 번만 한다(to_minutes).
"""
from __future__ import annotations

import re

SERVICE_TYPES = ("hospital_escort", "household_support")

# 필드 이름 → (설명, 형식)
FIELDS: dict[str, tuple[str, str]] = {
    "beneficiary_id": ("대상자 ID", "id"),
    "service_type": ("서비스 종류", "service_type"),
    "service_date": ("서비스일", "date"),
    "earliest_start": ("시작 가능 시각(동행은 댁 출발 가능 시각)", "time"),
    "latest_start": ("늦어도 시작할 시각(동행은 늦어도 댁 출발 시각)", "time"),
    "service_minutes": ("가사지원 필요시간(분)", "minutes"),
    "hospital_name": ("병원 이름", "text"),
    "appointment_time": ("외래 예약 시각", "time"),
    "hospital_stay_minutes": ("병원 체류시간(분)", "minutes"),
    "mobility_note": ("이동 관련 원문 표현(그대로)", "text"),
    "planned_discharge_date": ("퇴원 예정일", "date"),
    "actual_discharge_date": ("실제 퇴원일", "date"),
}

FIELD_LABELS = {k: v[0] for k, v in FIELDS.items()}

# 배정 계산에 꼭 필요한 필드만 승인 차단 사유로 쓴다(FR-57).
# 위치는 대상자 ID로 등록된 집 좌표를 쓴다(합성 데이터). 필요 자격은 서비스 종류에서 정해진다.
BLOCKING_COMMON = ("beneficiary_id", "service_type", "service_date", "earliest_start", "latest_start")
BLOCKING_BY_SERVICE = {
    "household_support": BLOCKING_COMMON + ("service_minutes",),
    "hospital_escort": BLOCKING_COMMON + ("hospital_name", "appointment_time", "hospital_stay_minutes"),
}
ESCORT_ONLY = ("hospital_name", "appointment_time", "hospital_stay_minutes")
HOUSEHOLD_ONLY = ("service_minutes",)

_RE = {
    "id": re.compile(r"^B\d{4}$"),
    "date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    "time": re.compile(r"^([01]\d|2[0-3]):[0-5]\d$"),
}


def _field_schema(kind: str) -> dict:
    if kind == "minutes":
        value = {"type": ["integer", "null"]}
    elif kind == "service_type":
        value = {"enum": [*SERVICE_TYPES, None]}
    else:
        value = {"type": ["string", "null"]}
    return {
        "type": "object",
        "properties": {
            "value": value,
            "evidence_span": {"type": ["string", "null"]},
            "needs_confirmation": {"type": "boolean"},
        },
        "required": ["value", "evidence_span", "needs_confirmation"],
        "additionalProperties": False,
    }


# 추출기 출력 형식. 정해진 필드 밖의 내용(승인 지시, 우선순위 변경 등)은 받지 않는다.
CARD_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "fields": {
            "type": "object",
            "properties": {name: _field_schema(kind) for name, (_, kind) in FIELDS.items()},
            "required": list(FIELDS),
            "additionalProperties": False,
        }
    },
    "required": ["fields"],
    "additionalProperties": False,
}


def check_format(name: str, value) -> bool:
    """값 형식 검사. None은 형식 검사 대상이 아니다(누락은 따로 다룬다)."""
    if value is None:
        return True
    kind = FIELDS[name][1]
    if kind in _RE:
        if not isinstance(value, str) or not _RE[kind].match(value):
            return False
        if kind == "date":
            import datetime as _dt
            try:
                _dt.date.fromisoformat(value)
            except ValueError:
                return False
        return True
    if kind == "minutes":
        return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 720
    if kind == "service_type":
        return value in SERVICE_TYPES
    return isinstance(value, str) and value.strip() != ""


def to_minutes(hhmm: str) -> int:
    """'HH:MM' → 자정 기준 분."""
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def from_minutes(minutes: int) -> str:
    """자정 기준 분 → 'HH:MM'."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def empty_fields() -> dict:
    return {name: {"value": None, "evidence_span": None, "needs_confirmation": False} for name in FIELDS}
