"""오늘의 돌봄 현황(W01) 집계 — 하루치 요청마다 지금 상태 하나를 정한다.

상태(PRD FR-02, 단일 기관 범위)
  자료 확인 필요 : 가져오기·카드에서 들어왔지만 아직 담당자가 확인하지 않은 요청(draft·보류)
  계산 전        : 확인된 요청인데, 지금 자료로 계산한 기록이 없다(자료가 바뀌었으면 예전 계산을 쓰지 않는다)
  제안           : 지금 자료로 계산한 배정안에 들어 있고 아직 확정하지 않음
  미배정         : 지금 자료로 계산했지만 배정안에 없음(엔진 사유 함께)
  확정           : 담당자가 승인함(저장소의 활성 확정)
  취소           : 담당자가 취소 처리함
  승인 대기(협약기관·인력 수락)·진행·완료는 후속 단계(W08·협약) 기능이라 이 범위에 없다. 화면에 '해당 없음'으로 적는다.

미래 정보 금지(CLAUDE.md 원칙 7): 가상 요청의 cancelled_same_day·delayed_same_day 는 '나중에 일어날 일'이라 현황에 쓰지 않는다.
취소·지연은 담당자가 등록한 것만 보인다.
"""
from __future__ import annotations

from pathlib import Path

STATUS_NEEDS_REVIEW = "자료 확인 필요"
STATUS_NOT_RUN = "계산 전"
STATUS_PROPOSED = "제안"
STATUS_UNASSIGNED = "미배정"
STATUS_CONFIRMED = "확정"
STATUS_CANCELLED = "취소"
ALL_STATUSES = (STATUS_NEEDS_REVIEW, STATUS_NOT_RUN, STATUS_PROPOSED, STATUS_UNASSIGNED,
                STATUS_CONFIRMED, STATUS_CANCELLED)


def _source(rid: str) -> str:
    return "요청 카드" if rid.startswith("카드-") else ("가져온 자료" if rid.startswith("가져옴-") else "기본 자료")


def day_status(store, data_dir: str | Path, service_date: str, *, algorithm: str = "AI 최적화",
               ref_min: int | None = None, urgent_window: int = 120) -> dict:
    """하루 현황. 반환: {rows, run, n_valid}.

    ref_min: 기준 시각(자정부터 분). 주면 확정되지 않은 요청 중 '늦어도 시작할 시각'이
             기준~기준+urgent_window 안이면 '임박', 이미 지났으면 '시간 지남'.
    """
    from planner.data import load_day

    inst = load_day(data_dir, service_date, extra_requests=store.ready_requests(service_date))
    run = store.find_run(service_date, algorithm, inst.as_instance())
    confirmed = {c["request_id"]: c for c in store.confirmed(service_date)}
    proposed = {a["request_id"]: a for a in (run["assignments"] if run else [])}
    unassigned = {u["request_id"]: u for u in (run["unassigned"] if run else [])}

    rows = []
    for r in inst.requests:
        rid = r["request_id"]
        worker = arrive = reason = None
        if rid in confirmed:
            status, worker, arrive = STATUS_CONFIRMED, confirmed[rid]["worker_id"], confirmed[rid]["arrive_min"]
        elif run is None:
            status = STATUS_NOT_RUN
        elif rid in proposed:
            status, worker, arrive = STATUS_PROPOSED, proposed[rid]["worker_id"], proposed[rid]["arrive_min"]
        else:
            status = STATUS_UNASSIGNED
            reason = (unassigned.get(rid) or {}).get("reason")
        rows.append(_row(r, status, worker, arrive, reason))

    for q in store.requests():
        if q["service_date"] != service_date or q["status"] not in ("draft", "needs_review", "cancelled"):
            continue
        status = STATUS_CANCELLED if q["status"] == "cancelled" else STATUS_NEEDS_REVIEW
        rows.append(_row(q["payload"], status, None, None, _last_reason(store, q["request_id"])))

    for row in rows:
        row["urgent"] = ""
        if ref_min is not None and row["status"] in (STATUS_NOT_RUN, STATUS_PROPOSED, STATUS_UNASSIGNED,
                                                       STATUS_NEEDS_REVIEW):
            gap = row["latest_start_min"] - ref_min
            if gap < 0:
                row["urgent"] = "시간 지남"
            elif gap <= urgent_window:
                row["urgent"] = "임박"
    return {"rows": rows, "run": run, "n_valid": len(inst.requests)}


def _row(r: dict, status: str, worker, arrive, reason) -> dict:
    return {"request_id": r["request_id"], "beneficiary_id": r["beneficiary_id"],
            "service_type": r["service_type"], "priority": int(r.get("priority") or 1),
            "priority_reason": r.get("priority_reason") or "", "earliest_start_min": r["earliest_start_min"],
            "latest_start_min": r["latest_start_min"], "appointment_min": r.get("appointment_min"),
            "status": status, "worker_id": worker, "arrive_min": arrive, "reason": reason,
            "source": _source(r["request_id"])}


def _last_reason(store, request_id: str) -> str | None:
    for a in reversed(store.audit(limit=100000)):
        if a["entity_id"] == request_id and a["detail"].get("reason"):
            return a["detail"]["reason"]
    return None


def filter_rows(rows: list[dict], *, service: str | None, priority: str | None,
                statuses: list[str] | None) -> list[dict]:
    """service: hospital_escort / household_support / None, priority: '높음'(2 이상) / '일반'(1) / None."""
    out = []
    for r in rows:
        if service and r["service_type"] != service:
            continue
        if priority == "높음" and r["priority"] < 2:
            continue
        if priority == "일반" and r["priority"] >= 2:
            continue
        if statuses and r["status"] not in statuses:
            continue
        out.append(r)
    return out


def summarize(rows: list[dict]) -> dict:
    """상태별 건수와 고유 인원(같은 사람 2건 = 1명). 표에 보이는 행만 넣으면 카드 숫자와 표가 일치한다."""
    c = {s: 0 for s in ALL_STATUSES}
    for r in rows:
        c[r["status"]] += 1
    c["confirmed_people"] = len({r["beneficiary_id"] for r in rows if r["status"] == STATUS_CONFIRMED})
    c["planned_people"] = len({r["beneficiary_id"] for r in rows
                               if r["status"] in (STATUS_CONFIRMED, STATUS_PROPOSED)})
    c["total"] = len(rows)
    return c
