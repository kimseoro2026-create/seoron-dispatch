"""W02 자료 불러오기 — CSV 파일 → 검토 전(draft) 요청. PRD FR-05·07·08·09, 8장 CSV 최소 계약.

원칙
- 한 행이 틀려도 파일 전체를 버리지 않는다. 행마다 오류 위치(줄 번호·항목)를 남긴다.
- 지어내지 않는다. 등록되지 않은 대상자, 병원 목록에 없는 병원·같은 이름이 여러 곳인 병원은 오류로 둔다.
  '늦어도 끝낼 시각(latest_finish)'은 '늦어도 시작할 시각'과 뜻이 달라 자동으로 연결하지 않는다.
- 비어 있어도 배정에 꼭 필요하지 않은 값(우선순위·병원 도착 여유)만 정해진 값으로 채우고, 그 사실을 경고로 보여준다.
- 정상 행도 draft 로만 만든다. 담당자가 확인해야 배정 계산에 들어간다.
- 파일 원문은 저장하지 않는다(파일 지문만).

내부 이름은 배정 엔진 계약(CLAUDE.md 5절)을 따른다. PRD 8장 CSV 계약의 latest_finish 대신 latest_start 를 쓴다.
"""
from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from intake.schema import check_format, to_minutes

MAX_BYTES = 10 * 1024 * 1024          # FR-07 문서 파일 10MB
ROOT = Path(__file__).resolve().parent.parent
IMPORT_PREFIX = "가져옴-"
BENEFICIARY_PREFIX = "BENEFICIARY:"
HOSPITAL_PREFIX = "HOSP:"
SERVICE_ALIASES = {"hospital_escort": "hospital_escort", "household_support": "household_support",
                   "병원동행": "hospital_escort", "병원 동행": "hospital_escort",
                   "가사지원": "household_support", "가사 지원": "household_support"}

# 내부 항목 → (화면 이름, 자동으로 알아볼 열 이름들)
TARGETS: dict[str, tuple[str, list[str]]] = {
    "external_id": ("요청번호", ["external_id", "요청번호", "요청 번호", "요청 ID", "요청ID", "request_id"]),
    "beneficiary_id": ("대상자 ID", ["beneficiary_id", "대상자", "대상자 ID", "대상자ID", "어르신 ID"]),
    "service_type": ("서비스 종류", ["service_type", "서비스", "서비스 종류", "서비스종류"]),
    "service_date": ("서비스일", ["service_date", "서비스일", "서비스 날짜", "날짜"]),
    "earliest_start": ("시작 가능 시각", ["earliest_start", "시작 가능 시각", "시작가능시각", "시작 가능"]),
    "latest_start": ("늦어도 시작할 시각", ["latest_start", "늦어도 시작할 시각", "늦어도 시작"]),
    "service_minutes": ("서비스 시간(분)", ["service_minutes", "서비스 시간(분)", "서비스 시간", "소요시간(분)"]),
    "priority": ("우선순위", ["priority", "우선순위"]),
    "priority_reason": ("우선순위 이유", ["priority_reason", "우선순위 이유", "우선순위 사유"]),
    "hospital_id": ("병원 ID", ["hospital_id", "병원 ID", "병원ID"]),
    "hospital_name": ("병원 이름", ["hospital_name", "병원", "병원 이름", "병원명"]),
    "appointment_at": ("외래 예약 시각", ["appointment_at", "appointment_time", "예약 시각", "외래 예약 시각"]),
    "hospital_stay_minutes": ("병원 체류(분)", ["hospital_stay_minutes", "병원 체류(분)", "병원 체류", "체류시간(분)"]),
    "arrival_buffer_minutes": ("병원 도착 여유(분)", ["arrival_buffer_minutes", "도착 여유(분)", "병원 도착 여유(분)"]),
    "requested_at": ("신청 시각", ["requested_at", "신청 시각", "접수 시각"]),
}
REQUIRED = ("external_id", "beneficiary_id", "service_type", "service_date", "earliest_start", "latest_start")


@dataclass
class ParsedCsv:
    headers: list[str] = field(default_factory=list)
    rows: list[tuple[int, dict]] = field(default_factory=list)   # (줄 번호, {열: 값})
    error: str | None = None


@dataclass
class RowResult:
    line: int
    external_id: str
    request: dict | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_csv_bytes(data: bytes, filename: str) -> ParsedCsv:
    """파일 검사(크기·확장자·인코딩) 뒤 행을 읽는다. 실패하면 error 에 쉬운 말로 이유를 담는다."""
    if not filename.lower().endswith(".csv"):
        return ParsedCsv(error="CSV 파일(.csv)만 올릴 수 있습니다. 엑셀은 'CSV UTF-8'로 저장해 주세요.")
    if len(data) > MAX_BYTES:
        return ParsedCsv(error=f"파일이 {MAX_BYTES // 1024 // 1024}MB보다 큽니다. 나눠서 올려 주세요.")
    if not data.strip():
        return ParsedCsv(error="빈 파일입니다.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return ParsedCsv(error="UTF-8 형식이 아닙니다. 엑셀에서 '다른 이름으로 저장 → CSV UTF-8(쉼표로 분리)'로 "
                               "저장한 뒤 다시 올려 주세요.")
    if "\x00" in text:
        return ParsedCsv(error="글자 파일이 아닙니다(CSV 내용을 읽을 수 없음).")
    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip() for h in (reader.fieldnames or []) if h is not None]
    if not headers:
        return ParsedCsv(error="첫 줄에 열 이름이 없습니다.")
    rows = []
    try:
        for raw in reader:
            row = {(k or "").strip(): (v or "").strip() if isinstance(v, str) else "" for k, v in raw.items()}
            if any(row.values()):
                rows.append((reader.line_num, row))
    except csv.Error as e:
        return ParsedCsv(error=f"CSV 형식 오류: {e}")
    return ParsedCsv(headers=headers, rows=rows)


def suggest_mapping(headers: list[str]) -> dict[str, str]:
    """열 이름을 보고 내부 항목을 짐작한다(정확히 같은 이름만, 대소문자·공백 무시). 담당자가 화면에서 고친다."""
    norm = {h.replace(" ", "").lower(): h for h in headers}
    out = {}
    for target, (_, aliases) in TARGETS.items():
        for a in aliases:
            h = norm.get(a.replace(" ", "").lower())
            if h is not None and h not in out.values():
                out[target] = h
                break
    return out


def _cfg_buffer() -> int:
    import yaml
    cfg = yaml.safe_load((ROOT / "config" / "region.yaml").read_text(encoding="utf-8"))
    return int(cfg["card_intake"]["arrival_buffer_min"])


def _minutes(v: str) -> int | None:
    return int(v) if v.isdigit() else None


def convert_rows(rows: list[tuple[int, dict]], mapping: dict[str, str], data_dir: str | Path,
                 imported_at: str) -> list[RowResult]:
    """행 → 요청(draft). 행마다 오류·경고를 따로 모은다."""
    d = Path(data_dir)
    bens = set(pd.read_csv(d / "beneficiaries.csv", dtype={"beneficiary_id": str})["beneficiary_id"])
    hos_path = d / "hospitals.csv" if (d / "hospitals.csv").exists() else ROOT / "data" / "raw" / "hospitals.csv"
    hos = pd.read_csv(hos_path, dtype={"hospital_id": str})
    hos_ids = set(hos["hospital_id"])
    buffer = _cfg_buffer()
    seen: set[str] = set()
    out = []
    for line, row in rows:
        def get(t: str) -> str:
            col = mapping.get(t)
            return row.get(col, "").strip() if col else ""

        errors, warnings = [], []
        missing_map = [TARGETS[t][0] for t in REQUIRED if not mapping.get(t)]
        if missing_map:
            out.append(RowResult(line, get("external_id"), None,
                                 [f"열이 연결되지 않았습니다: {', '.join(missing_map)}"]))
            continue
        ext = get("external_id")
        if not ext:
            errors.append("요청번호: 비어 있습니다")
        elif ext in seen:
            errors.append(f"요청번호 {ext}: 같은 파일에 두 번 있습니다")
        seen.add(ext)

        ben = get("beneficiary_id")
        if not ben:
            errors.append("대상자 ID: 비어 있습니다")
        elif ben not in bens:
            errors.append(f"대상자 ID {ben}: 등록된 대상자가 아닙니다(집 위치를 알 수 없음)")

        service = SERVICE_ALIASES.get(get("service_type"))
        if service is None:
            errors.append(f"서비스 종류 '{get('service_type')}': 병원동행 또는 가사지원이어야 합니다")

        date = get("service_date")
        if not check_format("service_date", date or None) or not date:
            errors.append(f"서비스일 '{date}': 2026-11-20 형식의 날짜가 아닙니다")

        lo_s, hi_s = get("earliest_start"), get("latest_start")
        lo = to_minutes(lo_s) if lo_s and check_format("earliest_start", lo_s) else None
        hi = to_minutes(hi_s) if hi_s and check_format("latest_start", hi_s) else None
        if lo is None:
            errors.append(f"시작 가능 시각 '{lo_s}': 13:10 형식이 아닙니다")
        if hi is None:
            errors.append(f"늦어도 시작할 시각 '{hi_s}': 13:10 형식이 아닙니다")
        if lo is not None and hi is not None and lo > hi:
            errors.append("시작 시각: '시작 가능 시각'이 '늦어도 시작할 시각'보다 늦습니다")

        pr_s = get("priority")
        if not pr_s:
            priority = 1
            warnings.append("우선순위가 비어 있어 일반(1)으로 두었습니다")
        elif pr_s.isdigit() and 1 <= int(pr_s) <= 3:
            priority = int(pr_s)
        else:
            priority = None
            errors.append(f"우선순위 '{pr_s}': 1(일반)~3 사이 숫자여야 합니다")

        svc_min = None
        escort = {}
        if service == "household_support":
            svc_min = _minutes(get("service_minutes"))
            if svc_min is None or not check_format("service_minutes", svc_min):
                errors.append(f"서비스 시간(분) '{get('service_minutes')}': 1~720 사이 분이어야 합니다")
        elif service == "hospital_escort":
            hid, hname = get("hospital_id"), get("hospital_name")
            if hid:
                if hid not in hos_ids:
                    errors.append("병원 ID: 병원 목록에 없습니다")
            elif hname:
                m = hos[hos["name"] == hname]
                if m.empty:
                    errors.append(f"병원 이름 '{hname}': 병원 목록에서 같은 이름을 찾지 못했습니다")
                elif len(m) > 1:
                    errors.append(f"병원 이름 '{hname}': 같은 이름의 병원이 여러 곳입니다. 병원 ID를 적어 주세요")
                else:
                    hid = m.iloc[0]["hospital_id"]
            else:
                errors.append("병원: 병원 ID나 병원 이름이 필요합니다")
            appt_s = get("appointment_at")
            appt = to_minutes(appt_s) if appt_s and check_format("appointment_time", appt_s) else None
            if appt is None:
                errors.append(f"외래 예약 시각 '{appt_s}': 13:10 형식이 아닙니다")
            stay = _minutes(get("hospital_stay_minutes"))
            if stay is None or not check_format("hospital_stay_minutes", stay):
                errors.append(f"병원 체류(분) '{get('hospital_stay_minutes')}': 1~720 사이 분이어야 합니다")
            buf_s = get("arrival_buffer_minutes")
            if buf_s:
                buf = _minutes(buf_s)
                if buf is None:
                    errors.append(f"병원 도착 여유(분) '{buf_s}': 0 이상 분이어야 합니다")
            else:
                buf = buffer
                warnings.append(f"병원 도착 여유가 비어 있어 설정값 {buffer}분으로 두었습니다")
            escort = {"hospital_node": f"{HOSPITAL_PREFIX}{hid}", "appointment_min": appt,
                      "arrival_buffer_min": buf, "hospital_stay_min": stay}

        if errors:
            out.append(RowResult(line, ext, None, errors, warnings))
            continue
        req = {
            "request_id": f"{IMPORT_PREFIX}{ext}", "external_id": ext, "beneficiary_id": ben,
            "service_type": service, "service_date": date, "home_node": f"{BENEFICIARY_PREFIX}{ben}",
            "earliest_start_min": lo, "latest_start_min": hi, "service_minutes": svc_min,
            "priority": priority, "priority_reason": get("priority_reason") or "가져온 자료",
            "requested_at": get("requested_at") or imported_at, "required_skills": [service],
            # 당일 취소·지연은 계획 시점에 알 수 없다(미래 정보). 재생 실험에서만 쓰는 값.
            "cancelled_same_day": False, "cancel_notice_min_before": None,
            "delayed_same_day": False, "delay_minutes": None, "delay_notice_min_before": None,
            "source": "import",
        }
        req.update(escort)
        out.append(RowResult(line, ext, req, [], warnings))
    return out
