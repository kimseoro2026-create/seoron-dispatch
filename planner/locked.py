"""확정 일정 잠금 — 확정된 배정(인력·도착 시각)은 그대로 두고 나머지 요청만 배정한다(PRD FR-20, 6.3-8).

기준선(신청순·근거리)은 '하루를 처음부터 차례로 채우는' 방식이라, 하루 중간에 확정 일정이 박혀 있으면 그대로 쓸 수 없다.
여기서는 같은 순서·같은 선택 규칙을 쓰되, 각 인력 일정에 '끼워 넣기'로 넣는다. 확정 일정은 시각을 바꾸지 않고,
끼워 넣은 뒤에도 모든 확정 일정이 원래 시각에 가능해야 한다.
  - 신청순: 접수 시각 순서, 가장 빨리 도착할 수 있는 인력(동률은 인력 ID).
  - 근거리: 우선순위·마감 순서, 추가 이동이 가장 적은 인력(동률은 도착·ID).
확정이 없으면 기존 기준선 함수를 그대로 쓴다(결과·실험 수치가 바뀌지 않게).
"""
from __future__ import annotations

from planner.baseline import (BaselineResult, NO_SKILL_MATCH, ROUTE_UNKNOWN, TIME_WINDOW_CONFLICT, Unassigned,
                              _State, _occupancy, _travel_map, _try_assign)
from planner.reschedule import _fits_at


def _sequence(worker: dict, jobs: list[dict], by_id: dict, travel: dict, locked_ids: set[str]) -> list[dict] | None:
    """주어진 순서대로 일정을 다시 따라간다. 확정 일정은 원래 시각에 가능해야 하고, 나머지는 가장 이른 가능 시각.

    → [{request_id, worker_id, arrive_min}] 또는 불가능하면 None.
    """
    st = _State(worker=worker, cursor=worker["base_node"], free=int(worker["shift_start_min"]))
    out = []
    for j in jobs:
        req = by_id[j["request_id"]]
        if j["request_id"] in locked_ids:
            t = int(j["arrive_min"])
            if not _fits_at(st, req, travel, t):
                return None
        else:
            orig = j.get("arrive_min")
            if orig is not None and _fits_at(st, req, travel, int(orig)):
                t = int(orig)
            else:
                t, _ = _try_assign(st, req, travel)
                if t is None:
                    return None
        end, span = _occupancy(req, travel, t)
        leg = travel.get((st.cursor, req["home_node"]))
        st.work_min += (int(round(leg)) if leg is not None else 0) + span
        st.cursor, st.free = req["home_node"], int(end)
        out.append({"request_id": j["request_id"], "worker_id": worker["worker_id"], "arrive_min": int(t)})
    return out


def _travel_sum(worker: dict, jobs: list[dict], by_id: dict, travel: dict) -> float:
    prev, total = worker["base_node"], 0.0
    for j in jobs:
        node = by_id[j["request_id"]]["home_node"]
        total += float(travel.get((prev, node), 0.0))
        prev = node
    return total


def plan_with_locks(instance: dict, locked: list[dict], strategy: str) -> BaselineResult:
    """strategy: 'request_order' 또는 'nearest'."""
    from planner.baseline import nearest, request_order
    if not locked:
        return {"request_order": request_order, "nearest": nearest}[strategy](instance)

    travel = _travel_map(instance["travel"])
    by_id = {r["request_id"]: r for r in instance["requests"]}
    workers = {w["worker_id"]: w for w in instance["workers"]}
    locked = [{"request_id": a["request_id"], "worker_id": a["worker_id"], "arrive_min": int(a["arrive_min"])}
              for a in locked if a["request_id"] in by_id]
    locked_ids = {a["request_id"] for a in locked}
    sched: dict[str, list[dict]] = {wid: sorted((a for a in locked if a["worker_id"] == wid),
                                                key=lambda a: a["arrive_min"]) for wid in workers}

    if strategy == "request_order":
        order = sorted(instance["requests"], key=lambda r: (r["requested_at"], r["request_id"]))
    elif strategy == "nearest":
        order = sorted(instance["requests"],
                       key=lambda r: (-int(r["priority"]), int(r["latest_start_min"]), r["request_id"]))
    else:
        raise ValueError(f"알 수 없는 방식: {strategy}")

    result = BaselineResult(algorithm=strategy + "+locked")
    for req in order:
        rid = req["request_id"]
        if rid in locked_ids:
            continue
        need = set(req.get("required_skills") or [])
        best = None   # (key, wid, new_jobs, arrive)
        skilled = False
        for wid in sorted(workers):
            w = workers[wid]
            if not need.issubset(set(w.get("skills") or [])):
                continue
            skilled = True
            jobs = sched[wid]
            before = _travel_sum(w, jobs, by_id, travel)
            for pos in range(len(jobs) + 1):
                trial = jobs[:pos] + [{"request_id": rid, "arrive_min": None}] + jobs[pos:]
                seq = _sequence(w, trial, by_id, travel, locked_ids)
                if seq is None:
                    continue
                arrive = next(a["arrive_min"] for a in seq if a["request_id"] == rid)
                if strategy == "request_order":
                    key = (arrive, wid)
                else:
                    key = (round(_travel_sum(w, seq, by_id, travel) - before, 3), arrive, wid)
                if best is None or key < best[0]:
                    best = (key, wid, seq, arrive)
                break     # 이 인력에서는 가장 앞쪽 가능한 자리 하나만 본다(기준선의 '가장 이른 자리')
        if best is None:
            if not skilled:
                reason = NO_SKILL_MATCH
            elif all(travel.get((n, req["home_node"])) is None for n in
                     [w["base_node"] for w in workers.values()] + [r["home_node"] for r in by_id.values()]):
                reason = ROUTE_UNKNOWN
            else:
                reason = TIME_WINDOW_CONFLICT
            result.unassigned.append(Unassigned(rid, reason, "diagnostic",
                                                "확정 일정을 지키면서 넣을 자리가 없음"))
            continue
        _, wid, seq, _arrive = best
        sched[wid] = seq
    result.assignments = sorted((a for jobs in sched.values() for a in jobs), key=lambda a: a["request_id"])
    return result
