"""요청 카드 사후 검사 규칙 테스트 (PRD 10장, W03 검수)."""
from intake.schema import empty_fields
from intake.validate import validate_card

TEXT_HH = (
    "[상담 메모] 접수 2026-10-28 16:29\n"
    "대상자 B0075 어르신 가사지원 요청.\n"
    "희망일: 2026년 11월 2일\n"
    "오전 10시부터 낮 12시 사이에 방문 시작. 2시간 정도 청소.\n"
    "이동: 보행기 사용."
)


def _f(value, span, nc=False):
    return {"value": value, "evidence_span": span, "needs_confirmation": nc}


def good_hh():
    f = empty_fields()
    f["beneficiary_id"] = _f("B0075", "B0075")
    f["service_type"] = _f("household_support", "가사지원")
    f["service_date"] = _f("2026-11-02", "2026년 11월 2일")
    f["earliest_start"] = _f("10:00", "오전 10시부터")
    f["latest_start"] = _f("12:00", "낮 12시 사이에")
    f["service_minutes"] = _f(120, "2시간 정도")
    f["mobility_note"] = _f("보행기 사용", "보행기 사용")
    return {"fields": f}


def codes(check, field=None):
    return {i["code"] for i in check.issues if field is None or i["field"] == field}


def test_good_card_is_ready():
    c = validate_card(TEXT_HH, good_hh())
    assert c.status == "ready", c.issues
    assert c.fields["service_minutes"]["value"] == 120


def test_schema_error_uses_no_values():
    raw = good_hh()
    raw["approve"] = True  # 원문 지시문을 따라 만든 필드
    c = validate_card(TEXT_HH, raw)
    assert c.status == "schema_error"
    assert all(f["value"] is None for f in c.fields.values())


def test_extra_field_rejected():
    raw = good_hh()
    raw["fields"]["priority"] = _f(3, "B0075")
    assert validate_card(TEXT_HH, raw).status == "schema_error"


def test_none_input_is_schema_error():
    assert validate_card(TEXT_HH, None).status == "schema_error"


def test_evidence_not_in_text_drops_value():
    raw = good_hh()
    raw["fields"]["service_minutes"] = _f(180, "3시간")
    c = validate_card(TEXT_HH, raw)
    assert c.fields["service_minutes"]["value"] is None
    assert "EVIDENCE_NOT_IN_TEXT" in codes(c, "service_minutes")
    assert c.status == "needs_review"


def test_missing_evidence_drops_value():
    raw = good_hh()
    raw["fields"]["earliest_start"] = _f("10:00", None)
    c = validate_card(TEXT_HH, raw)
    assert c.fields["earliest_start"]["value"] is None
    assert "NO_EVIDENCE" in codes(c, "earliest_start")


def test_evidence_whitespace_normalized():
    raw = good_hh()
    raw["fields"]["service_date"] = _f("2026-11-02", "2026년  11월\n2일")
    assert validate_card(TEXT_HH, raw).fields["service_date"]["value"] == "2026-11-02"


def test_bad_format_dropped():
    raw = good_hh()
    raw["fields"]["earliest_start"] = _f("10시", "오전 10시부터")
    c = validate_card(TEXT_HH, raw)
    assert c.fields["earliest_start"]["value"] is None
    assert "BAD_FORMAT" in codes(c)


def test_date_without_year_needs_confirmation():
    text = TEXT_HH.replace("2026년 11월 2일", "11월 2일")
    raw = good_hh()
    raw["fields"]["service_date"] = _f("2026-11-02", "11월 2일")
    c = validate_card(text, raw)
    assert c.fields["service_date"]["value"] == "2026-11-02"  # 값은 제안으로 남긴다
    assert c.fields["service_date"]["needs_confirmation"] is True
    assert "NO_YEAR" in codes(c)
    assert c.status == "needs_review"


def test_walker_not_converted_to_independent():
    raw = good_hh()
    raw["fields"]["mobility_note"] = _f("자립보행 가능", "보행기 사용")
    c = validate_card(TEXT_HH, raw)
    assert c.fields["mobility_note"]["value"] is None
    assert "MOBILITY_JUDGMENT" in codes(c)


def test_mobility_paraphrase_rejected():
    raw = good_hh()
    raw["fields"]["mobility_note"] = _f("보행 보조기구 필요", "보행기 사용")
    c = validate_card(TEXT_HH, raw)
    assert c.fields["mobility_note"]["value"] is None
    assert "NOT_VERBATIM" in codes(c)


def test_missing_required_blocks():
    raw = good_hh()
    raw["fields"]["latest_start"] = _f(None, None)
    c = validate_card(TEXT_HH, raw)
    assert c.status == "needs_review"
    assert "MISSING_REQUIRED" in codes(c, "latest_start")


def test_missing_optional_does_not_block():
    raw = good_hh()
    raw["fields"]["mobility_note"] = _f(None, None)
    assert validate_card(TEXT_HH, raw).status == "ready"


def test_time_order():
    raw = good_hh()
    raw["fields"]["earliest_start"] = _f("12:00", "낮 12시 사이에")
    raw["fields"]["latest_start"] = _f("10:00", "오전 10시부터")
    c = validate_card(TEXT_HH, raw)
    assert "TIME_ORDER" in codes(c)
    assert c.status == "needs_review"


def test_escort_field_on_household_flagged():
    text = TEXT_HH + "\n한림병원"
    raw = good_hh()
    raw["fields"]["hospital_name"] = _f("한림병원", "한림병원")
    c = validate_card(text, raw)
    assert "WRONG_SERVICE_FIELD" in codes(c)
    assert c.status == "needs_review"


ESCORT = (
    "[상담 메모] 접수 2026-10-31 13:58\n대상자 B0233 병원동행 요청.\n"
    "2026년 11월 2일 오후 1시 10분 한림병원 외래 예약.\n"
    "댁에서 낮 12시 10분~12시 30분 사이 출발 희망.\n병원에 약 120분 머무를 예정."
)


def good_escort():
    f = empty_fields()
    f["beneficiary_id"] = _f("B0233", "B0233")
    f["service_type"] = _f("hospital_escort", "병원동행")
    f["service_date"] = _f("2026-11-02", "2026년 11월 2일")
    f["appointment_time"] = _f("13:10", "오후 1시 10분")
    f["hospital_name"] = _f("한림병원", "한림병원")
    f["earliest_start"] = _f("12:10", "낮 12시 10분")
    f["latest_start"] = _f("12:30", "12시 30분 사이 출발")
    f["hospital_stay_minutes"] = _f(120, "약 120분")
    return {"fields": f}


def test_escort_ready_without_service_minutes():
    assert validate_card(ESCORT, good_escort()).status == "ready"


def test_escort_departure_after_appointment():
    raw = good_escort()
    raw["fields"]["appointment_time"] = _f("12:20", "12시 10분")
    c = validate_card(ESCORT, raw)
    assert "TIME_ORDER" in codes(c, "appointment_time")


def test_input_not_mutated():
    raw = good_hh()
    raw["fields"]["service_minutes"] = _f(180, "3시간")
    validate_card(TEXT_HH, raw)
    assert raw["fields"]["service_minutes"]["value"] == 180
