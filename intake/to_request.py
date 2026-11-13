"""담당자가 확인한 요청 카드 → 배정 엔진 요청(planner 데이터 계약).

원칙
- 지어내지 않는다. 등록되지 않은 대상자, 병원 목록에 같은 이름이 없는 병원은 막는다.
  비슷한 이름으로 추측해 고르지 않는다(담당자가 '고칠 값'에 정확한 이름을 적는다).
- 우선순위는 AI 판단이 아니라, 담당자가 확인한 실제 퇴원일에 가상 데이터(f0)와 같은 규칙을 적용한다.
- 병원 도착 여유처럼 메모에 없는 운영 가정은 config/region.yaml(card_intake)에서만 읽는다.
- 병원동행의 service_minutes 는 엔진·검증기가 쓰지 않는다(점유는 이동+체류로 다시 계산).
  이동시간을 안 뒤 planner.data.load_day 가 채운다.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import yaml

from intake.schema import BLOCKING_BY_SERVICE, FIELD_LABELS, check_format, to_minutes

ROOT = Path(__file__).resolve().parent.parent
CARD_PREFIX = "카드-"
BENEFICIARY_PREFIX = "BENEFICIARY:"   # planner.data 와 같은 노드 이름
HOSPITAL_PREFIX = "HOSP:"


def _config() -> dict:
    cfg = yaml.safe_load((ROOT / "config" / "region.yaml").read_text(encoding="utf-8"))
    c = cfg.get("card_intake") or {}
    return {"arrival_buffer_min": int(c["arrival_buffer_min"]),
            "high_priority_discharge_days": int(c["high_priority_discharge_days"])}


def card_to_request(final: dict, data_dir: str | Path, card_id: str,
                    confirmed_at: str) -> tuple[dict | None, list[str]]:
    """확인된 카드 값(final) → (요청 dict, 문제 목록). 문제가 있으면 요청은 None."""
    d = Path(data_dir)
    problems: list[str] = []
    service = final.get("service_type")
    if service not in BLOCKING_BY_SERVICE:
        return None, ["서비스 종류: 병원동행 또는 가사지원이어야 합니다"]

    for name in BLOCKING_BY_SERVICE[service]:
        v = final.get(name)
        if v is None or v == "":
            problems.append(f"{FIELD_LABELS[name]}: 값이 없습니다")
        elif not check_format(name, v):
            problems.append(f"{FIELD_LABELS[name]}: 형식이 맞지 않습니다")
    if problems:
        return None, problems

    ben = pd.read_csv(d / "beneficiaries.csv", dtype={"beneficiary_id": str})
    if final["beneficiary_id"] not in set(ben["beneficiary_id"]):
        problems.append(f"대상자 ID {final['beneficiary_id']}: 등록된 대상자가 아닙니다(집 위치를 알 수 없음)")

    lo, hi = to_minutes(final["earliest_start"]), to_minutes(final["latest_start"])
    if lo > hi:
        problems.append("시작 시각: '시작 가능 시각'이 '늦어도 시작할 시각'보다 늦습니다")

    cfg = _config()
    hospital_id = None
    if service == "hospital_escort":
        hos = pd.read_csv(d / "hospitals.csv", dtype={"hospital_id": str})
        match = hos[hos["name"] == str(final["hospital_name"]).strip()]
        if match.empty:
            problems.append(f"병원 이름 '{final['hospital_name']}': 병원 목록에서 같은 이름을 찾지 못했습니다. "
                            "정확한 이름을 '고칠 값'에 적어 주세요")
        elif len(match) > 1:
            problems.append(f"병원 이름 '{final['hospital_name']}': 같은 이름의 병원이 여러 곳입니다")
        else:
            hospital_id = match.iloc[0]["hospital_id"]
    if problems:
        return None, problems

    priority, reason = _priority(final, cfg["high_priority_discharge_days"])
    req = {
        "request_id": f"{CARD_PREFIX}{card_id}",
        "beneficiary_id": final["beneficiary_id"],
        "service_type": service,
        "service_date": final["service_date"],
        "home_node": f"{BENEFICIARY_PREFIX}{final['beneficiary_id']}",
        "earliest_start_min": lo,
        "latest_start_min": hi,
        "service_minutes": int(final["service_minutes"]) if service == "household_support" else None,
        "priority": priority,
        "priority_reason": reason,
        "requested_at": confirmed_at,
        "required_skills": [service],
        # 당일 취소·지연은 카드에서 알 수 없다(미래 정보). 재생 실험에서만 쓰는 값이라 '없음'으로 둔다.
        "cancelled_same_day": False, "cancel_notice_min_before": None,
        "delayed_same_day": False, "delay_minutes": None, "delay_notice_min_before": None,
        "source": "card",
    }
    if service == "hospital_escort":
        req.update(hospital_node=f"{HOSPITAL_PREFIX}{hospital_id}",
                   appointment_min=to_minutes(final["appointment_time"]),
                   arrival_buffer_min=cfg["arrival_buffer_min"],
                   hospital_stay_min=int(final["hospital_stay_minutes"]))
    return req, []


def _priority(final: dict, days_limit: int) -> tuple[int, str]:
    """실제 퇴원일이 서비스일 기준 days_limit 일 이내면 2(높음). 퇴원 예정일만 있으면 판단하지 않는다."""
    actual = final.get("actual_discharge_date")
    if actual and check_format("actual_discharge_date", actual):
        days = (dt.date.fromisoformat(final["service_date"]) - dt.date.fromisoformat(actual)).days
        if 0 <= days <= days_limit:
            return 2, f"퇴원 {days}일 경과(카드 확인값)"
    return 1, "정기 서비스(카드)"


def merge_card_requests(base: list[dict], cards: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """그날 요청 목록에 카드 요청을 더한다.

    같은 대상자·같은 서비스 요청이 이미 있으면 카드 값으로 바꾼다(같은 요청을 두 번 세지 않기 위해).
    반환: (합친 목록, {카드 요청 ID: 바뀐 기존 요청 ID})
    """
    replaced: dict[str, str] = {}
    keys = {}
    for c in cards:
        keys[(c["beneficiary_id"], c["service_type"])] = c["request_id"]
    kept = []
    for r in base:
        k = (r["beneficiary_id"], r["service_type"])
        if k in keys and keys[k] not in replaced:
            replaced[keys[k]] = r["request_id"]
            continue
        kept.append(r)
    return kept + list(cards), replaced
