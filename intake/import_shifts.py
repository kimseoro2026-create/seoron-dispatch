"""근무표 CSV 가져오기(W04, FR-17) — PRD 8장 shifts.csv 계약:
worker_external_id,date,start_time,end_time,break_start,break_end

- 시작·끝 시각이 모두 비어 있으면 그날 휴무로 본다.
- 등록되지 않은 인력, 날짜·시각 형식 오류, 휴게가 근무 밖인 행은 오류로 두고 나머지 행은 살린다.
- 자격(수행 서비스)은 근무표에서 바꾸지 않는다(인력 화면에서만). 동행 가능을 가사 가능으로 넓히지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from intake.import_csv import ParsedCsv, parse_csv_bytes
from intake.schema import check_format, to_minutes

COLUMNS = ("worker_external_id", "date", "start_time", "end_time", "break_start", "break_end")


@dataclass
class ShiftRow:
    line: int
    row: dict | None
    errors: list[str] = field(default_factory=list)


def parse_shifts_csv(data: bytes, filename: str) -> ParsedCsv:
    parsed = parse_csv_bytes(data, filename)
    if parsed.error:
        return parsed
    missing = [c for c in COLUMNS if c not in parsed.headers]
    if missing:
        return ParsedCsv(error="근무표에 필요한 열이 없습니다: " + ", ".join(missing))
    return parsed


def _t(v: str) -> int | None:
    return to_minutes(v) if v and check_format("earliest_start", v) else None


def convert_shift_rows(rows: list[tuple[int, dict]], data_dir: str | Path) -> list[ShiftRow]:
    workers = set(pd.read_csv(Path(data_dir) / "workers.csv", dtype={"worker_id": str})["worker_id"])
    out = []
    for line, r in rows:
        errs = []
        wid, date = r.get("worker_external_id", ""), r.get("date", "")
        if wid not in workers:
            errs.append(f"인력 '{wid}': 등록된 인력이 아닙니다")
        if not date or not check_format("service_date", date):
            errs.append(f"날짜 '{date}': 2026-11-21 형식이 아닙니다")
        st_s, en_s = r.get("start_time", ""), r.get("end_time", "")
        if not st_s and not en_s:
            out.append(ShiftRow(line, None if errs else {"worker_id": wid, "service_date": date, "off": True}, errs))
            continue
        start, end = _t(st_s), _t(en_s)
        bs, be = _t(r.get("break_start", "")), _t(r.get("break_end", ""))
        if start is None or end is None:
            errs.append("근무 시작·끝: 13:10 형식이어야 합니다(둘 다 비우면 휴무)")
        elif start >= end:
            errs.append("근무 시작이 끝보다 빨라야 합니다")
        if bs is None or be is None or be < bs:
            errs.append("휴게 시작·끝: 13:10 형식이고 끝이 시작보다 늦어야 합니다")
        elif start is not None and end is not None and not (start <= bs and be <= end):
            errs.append("휴게가 근무시간 안에 있어야 합니다")
        if errs:
            out.append(ShiftRow(line, None, errs))
            continue
        out.append(ShiftRow(line, {"worker_id": wid, "service_date": date, "off": False, "start_min": start,
                                   "end_min": end, "break_start_min": bs, "break_minutes": be - bs}))
    return out
