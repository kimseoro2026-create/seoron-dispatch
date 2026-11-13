"""승인 표의 줄 → 담당자 결정·수정(W06 FR-26). 화면에서 떼어 내어 표 편집 없이도 시험할 수 있게 했다."""
from __future__ import annotations

import re

from planner.approval import ACCEPT, PENDING, REJECT, Decision

LABEL_TO_ACTION = {"수락": ACCEPT, "거부": REJECT, "보류": PENDING}
_TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def _s(v) -> str:
    """표 칸 값 → 문자열. 비어 있으면 None·NaN 이 올 수 있어 ''로 바꾼다."""
    return v.strip() if isinstance(v, str) else ""


def parse_decisions(rows: list[dict], proposal: list[dict], confirmed_ids: set[str]
                    ) -> tuple[list[dict], list[Decision], list[str]]:
    """→ (최종 제안, 결정, 오류). 확정된(잠긴) 줄은 건너뛴다.

    rows: [{요청, 결정, 거부 사유, 바꿀 인력, 바꿀 도착}]
    수락한 줄만 인력·도착을 바꿀 수 있고, 바꾼 줄에는 modified=True 를 붙인다(감사 기록·재검증용).
    """
    by_pid = {a["request_id"]: a for a in proposal}
    modified: dict[str, dict] = {}
    decisions: list[Decision] = []
    errors: list[str] = []
    for row in rows:
        rid = row["요청"]
        if rid in confirmed_ids:
            continue
        action = LABEL_TO_ACTION.get(row.get("결정"), PENDING)
        reason = _s(row.get("거부 사유"))
        new_w = _s(row.get("바꿀 인력"))
        new_t = _s(row.get("바꿀 도착"))
        if new_w or new_t:
            if action != ACCEPT:
                errors.append(f"{rid}: 인력·도착을 바꾸려면 '수락'을 골라 주세요.")
                continue
            if new_t and not _TIME.fullmatch(new_t):
                errors.append(f"{rid}: 바꿀 도착은 13:10 형식으로 적어 주세요.")
                continue
            orig = by_pid[rid]
            modified[rid] = {"request_id": rid, "worker_id": new_w or orig["worker_id"],
                             "arrive_min": (int(new_t[:2]) * 60 + int(new_t[3:])) if new_t else int(orig["arrive_min"]),
                             "modified": True}
        if action == REJECT and not reason:
            errors.append(f"{rid}: 거부 사유를 입력하세요.")
            continue
        if action != PENDING:
            decisions.append(Decision(rid, action, reason))
    final = [modified.get(a["request_id"], a) for a in proposal]
    return final, decisions, errors
