"""다음 주 필요한 서비스 시간(PRD FR-39) — 이미 접수된 요청과 '퇴원 예정 의뢰'를 나눠 보여준다.

  접수된 수요 : 그 주 날짜의 확인된 요청(기본 자료 + 담당자 확인분, 취소 제외)의 점유 시간 합.
                병원동행은 이동(집→병원) + 병원 체류 + 이동(병원→집).
  퇴원 예정 의뢰: 요청 카드에 퇴원 예정일이 그 주 안으로 적혀 있지만 아직 요청으로 확인되지 않은 건.
                필요 시간은 요청이 들어와야 알 수 있으므로 시간을 지어내지 않는다(건수만).
아직 접수되지 않은 수요의 '예측'은 하지 않는다(EXP 범위, 실제 이력 없이 정확도를 주장하지 않음).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path


def next_week_demand(store, data_dir: str | Path, ref_date: str) -> dict:
    from planner.data import load_day, load_travel, service_dates
    from simulator.experiment import service_span

    d = Path(data_dir)
    start = dt.date.fromisoformat(ref_date)
    end = start + dt.timedelta(days=7)
    week = [x for x in service_dates(d) if start.isoformat() <= x < end.isoformat()]
    travel = load_travel(d) if week else {}
    days = []
    for day in week:
        inst = load_day(d, day, travel=travel, **store.day_inputs(day))
        mins, unknown = 0, 0
        for r in inst.requests:
            s = service_span(r, inst.travel)
            if s is None:
                unknown += 1
            else:
                mins += s
        days.append({"service_date": day, "requests": len(inst.requests), "service_hours": round(mins / 60, 1),
                     "no_route": unknown})

    referrals = []
    for key in store.card_review_keys():
        last = store.card_review(key)
        f = last["final"] or {}
        pdd = f.get("planned_discharge_date")
        if not pdd or not (start.isoformat() <= str(pdd) < end.isoformat()):
            continue
        if last["action"] == "확인 완료":
            continue      # 이미 요청으로 확인됨 → 접수된 수요에 들어간다
        referrals.append({"card_id": last["card_id"], "beneficiary_id": f.get("beneficiary_id"),
                          "planned_discharge_date": str(pdd), "service_type": f.get("service_type")})
    return {"ref_date": ref_date, "days": days, "referrals": referrals}
