"""W02 자료 불러오기 — CSV → 검토 전(draft) 요청. PRD FR-05·07·08·09, AT-01, 8장 CSV 최소 계약."""
from __future__ import annotations

from pathlib import Path

import pytest

from intake.import_csv import (MAX_BYTES, convert_rows, file_hash, parse_csv_bytes, suggest_mapping)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "processed"
pytestmark = pytest.mark.skipif(not (DATA / "beneficiaries.csv").exists(), reason="생성 데이터 없음")

HEADER = ("external_id,beneficiary_id,service_type,service_date,earliest_start,latest_start,"
          "service_minutes,priority,hospital_name,appointment_at,hospital_stay_minutes\n")


def _rows(text: str):
    parsed = parse_csv_bytes((HEADER + text).encode("utf-8"), "requests.csv")
    assert parsed.error is None, parsed.error
    return parsed


def _convert(text: str):
    parsed = _rows(text)
    return convert_rows(parsed.rows, suggest_mapping(parsed.headers), DATA, imported_at="2026-11-14T09:00")


# ── 파일 검사 (FR-07) ──────────────────────────────────────────────────────
def test_utf8_bom_is_accepted():
    p = parse_csv_bytes(("﻿" + HEADER + "X1,B0001,가사지원,2026-11-20,10:00,11:00,90,1,,,\n").encode("utf-8"),
                        "a.csv")
    assert p.error is None and p.headers[0] == "external_id" and len(p.rows) == 1


def test_non_utf8_explains_cause():
    p = parse_csv_bytes((HEADER + "X1,B0001,가사지원,2026-11-20,10:00,11:00,90,1,,,\n").encode("cp949"), "a.csv")
    assert p.error and "UTF-8" in p.error


def test_too_big_or_wrong_extension_is_rejected():
    assert parse_csv_bytes(b"a" * (MAX_BYTES + 1), "big.csv").error
    assert parse_csv_bytes(HEADER.encode(), "requests.xlsx").error
    assert parse_csv_bytes(b"", "empty.csv").error


def test_file_hash_is_stable():
    b = HEADER.encode()
    assert file_hash(b) == file_hash(b) and file_hash(b) != file_hash(b + b"x")


# ── 열 이름 맞추기 (FR-05) ─────────────────────────────────────────────────
def test_suggest_mapping_understands_korean_headers():
    m = suggest_mapping(["요청번호", "대상자 ID", "서비스 종류", "서비스일", "시작 가능 시각",
                         "늦어도 시작할 시각", "서비스 시간(분)", "우선순위", "기타 메모"])
    assert m["external_id"] == "요청번호" and m["beneficiary_id"] == "대상자 ID"
    assert m["latest_start"] == "늦어도 시작할 시각" and m["service_minutes"] == "서비스 시간(분)"
    assert "기타 메모" not in m.values()


def test_latest_finish_is_not_guessed_as_latest_start():
    """'늦어도 끝낼 시각'은 '늦어도 시작할 시각'과 뜻이 달라 자동으로 연결하지 않는다."""
    m = suggest_mapping(["external_id", "latest_finish"])
    assert m.get("latest_start") is None


# ── 행 변환 (FR-09) ────────────────────────────────────────────────────────
def test_household_row_becomes_draft_request_in_contract():
    res = _convert("X1,B0001,가사지원,2026-11-20,10:00,11:00,90,2,,,\n")
    r = res[0]
    assert r.errors == [] and r.line == 2
    q = r.request
    assert q["request_id"] == "가져옴-X1" and q["source"] == "import"
    assert q["service_type"] == "household_support" and q["home_node"] == "BENEFICIARY:B0001"
    assert (q["earliest_start_min"], q["latest_start_min"], q["service_minutes"]) == (600, 660, 90)
    assert q["priority"] == 2 and q["required_skills"] == ["household_support"]
    assert q["requested_at"] == "2026-11-14T09:00" and q["cancelled_same_day"] is False


def test_escort_row_uses_hospital_name_and_config_buffer():
    res = _convert("E1,B0002,병원동행,2026-11-20,09:00,09:20,,1,동탄성모병원,10:30,120\n")
    r = res[0]
    assert r.errors == [], r.errors
    q = r.request
    assert q["hospital_node"].startswith("HOSP:") and q["appointment_min"] == 630
    assert q["hospital_stay_min"] == 120 and q["service_minutes"] is None
    assert q["arrival_buffer_min"] == 10 and any("도착 여유" in w for w in r.warnings)


def test_one_bad_row_does_not_lose_the_file():
    res = _convert("X1,B0001,가사지원,2026-11-20,10:00,11:00,90,1,,,\n"
                   "X2,B0001,가사지원,2026-13-40,10:00,11:00,90,1,,,\n"
                   "X3,B0003,가사지원,2026-11-21,13:00,14:00,60,1,,,\n")
    assert [r.line for r in res if r.errors] == [3]
    assert [r.request["request_id"] for r in res if r.request] == ["가져옴-X1", "가져옴-X3"]
    assert any("서비스일" in e for e in res[1].errors)


@pytest.mark.parametrize("row, word", [
    ("X1,B9999,가사지원,2026-11-20,10:00,11:00,90,1,,,", "대상자"),
    ("X1,B0001,가사지원,2026-11-20,11:00,10:00,90,1,,,", "시작"),
    ("X1,B0001,가사지원,2026-11-20,10:00,11:00,,1,,,", "서비스 시간"),
    ("X1,B0001,청소,2026-11-20,10:00,11:00,90,1,,,", "서비스 종류"),
    ("E1,B0002,병원동행,2026-11-20,09:00,09:20,,1,없는병원,10:30,120", "병원"),
    ("E1,B0002,병원동행,2026-11-20,09:00,09:20,,1,동탄성모병원,,120", "예약"),
    (",B0001,가사지원,2026-11-20,10:00,11:00,90,1,,,", "요청번호"),
    ("X1,B0001,가사지원,2026-11-20,10:00,11:00,90,높음,,,", "우선순위"),
])
def test_bad_values_are_errors_not_guesses(row, word):
    r = _convert(row + "\n")[0]
    assert r.request is None and any(word in e for e in r.errors), r.errors


def test_duplicate_hospital_name_is_blocked():
    import pandas as pd
    h = pd.read_csv(ROOT / "data" / "raw" / "hospitals.csv")
    dup = h["name"].value_counts()
    dup = dup[dup > 1].index[0]
    r = _convert(f"E1,B0002,병원동행,2026-11-20,09:00,09:20,,1,{dup},10:30,120\n")[0]
    assert r.request is None and any("여러 곳" in e for e in r.errors)


def test_missing_priority_is_visible_warning():
    r = _convert("X1,B0001,가사지원,2026-11-20,10:00,11:00,90,,,,\n")[0]
    assert r.request["priority"] == 1 and any("우선순위" in w for w in r.warnings)


def test_same_external_id_twice_in_file_is_error():
    res = _convert("X1,B0001,가사지원,2026-11-20,10:00,11:00,90,1,,,\n"
                   "X1,B0003,가사지원,2026-11-21,13:00,14:00,60,1,,,\n")
    assert res[0].request and res[1].request is None and any("같은 파일" in e for e in res[1].errors)


def test_missing_required_mapping_is_reported_per_row():
    parsed = parse_csv_bytes(b"external_id,beneficiary_id\nX1,B0001\n", "a.csv")
    res = convert_rows(parsed.rows, suggest_mapping(parsed.headers), DATA, imported_at="t")
    assert res[0].request is None and res[0].errors
