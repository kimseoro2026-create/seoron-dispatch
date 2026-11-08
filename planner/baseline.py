"""기준선 2종 — 신청순 · 근거리 (PRD 12장).

목적은 '기존 방식'을 재현하는 것이다. 좋은 배정을 만드는 것이 아니라,
AI 배정과 같은 조건에서 비교할 수 있는 대조군을 만드는 것이 목표다.
그래서 두 방식 모두 탐욕적(greedy)이며 되돌리지 않는다.

  신청순: 접수시각(requested_at) 순서로, 가능한 인력 중 가장 빨리 시작할 수 있는 인력.
          동률이면 인력 ID 순.
  근거리: 담당자 우선순위·마감 순으로 요청을 고르고, 가능한 인력 중 추가 이동이 가장 적은 인력.

두 방식 모두 결과는 verifier 로 다시 검증한다. 위반이 있으면 배정안이 아니다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

# 미배정 사유 (PRD 6.5)
MISSING_REQUIRED = "MISSING_REQUIRED"
ROUTE_UNKNOWN = "ROUTE_UNKNOWN"
NO_SKILL_MATCH = "NO_SKILL_MATCH"
TIME_WINDOW_CONFLICT = "TIME_WINDOW_CONFLICT"
NO_CAPACITY = "NO_CAPACITY"


@dataclass
class Unassigned:
    request_id: str
    reason: str
    certainty: str          # PRD 6.5: verified / diagnostic / unknown
    detail: str

    def as_dict(self) -> dict:
        return {"request_id": self.request_id, "reason": self.reason,
                "certainty": self.certainty, "detail": self.detail}


@dataclass
class BaselineResult:
    algorithm: str
    assignments: list[dict] = field(default_factory=list)
    unassigned: list[Unassigned] = field(default_factory=list)

    def assigned_count(self) -> int:
        return len(self.assignments)

    def unassigned_count(self) -> int:
        return len(self.unassigned)


@dataclass
class _State:
    """인력 한 명의 현재 상태. 시간은 모두 서비스일 자정 기준 분."""
    worker: dict
    cursor: str
    free: int
    work_min: int = 0


def _travel_map(travel) -> dict[tuple[str, str], float]:
    """이동시간을 {(from, to): 분} 로 통일한다.

    검증기는 행 목록(list)을, 엔진은 dict 를 쓴다. 형식 차이로 배정이 조용히
    실패하지 않도록 여기서 한 번만 변환한다.
    """
    if isinstance(travel, dict):
        return {(a, b): float(m) for (a, b), m in travel.items()}
    return {(r["from_node"], r["to_node"]): float(r["minutes"]) for r in travel}


def _occupancy(req: dict, travel: dict, arrive: int) -> tuple[int | None, int]:
    """도착 시각 → (점유 종료, 점유 길이). 경로가 없으면 (None, 0).

    병원동행은 픽업 → 병원 → 귀가를 한 구간으로 본다(CLAUDE.md 5절).
    """
    if req["service_type"] != "hospital_escort":
        svc = int(req["service_minutes"])
        return arrive + svc, svc
    to_h = travel.get((req["home_node"], req["hospital_node"]))
    back = travel.get((req["hospital_node"], req["home_node"]))
    if to_h is None or back is None:
        return None, 0
    span = int(round(to_h)) + int(req["hospital_stay_min"]) + int(round(back))
    return arrive + span, span


def _try_assign(state: _State, req: dict, travel: dict) -> tuple[int | None, str]:
    """이 인력이 이 요청을 지금 상태에서 수행할 수 있는 '가장 이른 도착 시각'.

    불가능하면 (None, 사유코드). 이동시간을 0으로 대체하지 않는다.
    """
    w = state.worker
    leg = travel.get((state.cursor, req["home_node"]))
    if leg is None:
        return None, ROUTE_UNKNOWN
    leg = int(round(leg))

    shift_start, shift_end = int(w["shift_start_min"]), int(w["shift_end_min"])
    brk_start = int(w["break_start_min"])
    brk_end = brk_start + int(w["break_minutes"])
    cap = int(w.get("max_work_minutes") or 0)
    earliest = int(req["earliest_start_min"])
    latest = int(req["latest_start_min"])

    # 가장 이른 도착 후보: 시간창 시작 / 근무시작+이동 / 직전 일 종료+이동 중 늦은 것
    t = max(earliest, shift_start + leg, state.free + leg)

    # 휴게를 피해 뒤로 미룬다. 최대 몇 번만 돌면 수렴한다.
    for _ in range(8):
        if t > latest:
            return None, TIME_WINDOW_CONFLICT
        end, span = _occupancy(req, travel, t)
        if end is None:
            return None, ROUTE_UNKNOWN
        depart = t - leg
        if depart < brk_end and brk_start < t:       # 이동 구간이 휴게와 겹침
            t = brk_end + leg
            continue
        if t < brk_end and brk_start < end:          # 서비스 구간이 휴게와 겹침
            t = brk_end
            continue
        break
    else:
        return None, NO_CAPACITY

    end, span = _occupancy(req, travel, t)
    if end is None:
        return None, ROUTE_UNKNOWN

    if t > latest:
        return None, TIME_WINDOW_CONFLICT
    if t < shift_start or end > shift_end:
        return None, NO_CAPACITY
    if cap and state.work_min + leg + span > cap:
        return None, NO_CAPACITY
    if req["service_type"] == "hospital_escort":
        to_h = travel.get((req["home_node"], req["hospital_node"]))
        if to_h is None:
            return None, ROUTE_UNKNOWN
        hospital_arrive = t + int(round(to_h))
        deadline = int(req["appointment_min"]) - int(req["arrival_buffer_min"])
        if hospital_arrive > deadline:
            return None, TIME_WINDOW_CONFLICT
    return t, ""


def _diagnose(req: dict, states: list[_State], travel: dict) -> Unassigned:
    """왜 아무도 못 맡았는지 사유를 정한다.

    PRD 6.5: 단일 원인을 항상 증명할 수는 없다. 확실한 것과 추정을 구분해 기록한다.
    """
    need = set(req.get("required_skills") or [])
    skilled = [s for s in states if need.issubset(set(s.worker.get("skills") or []))]
    if not skilled:
        return Unassigned(req["request_id"], NO_SKILL_MATCH, "verified",
                          f"필요 자격 {sorted(need)} 보유 인력 없음")

    no_route = [s for s in skilled
                if travel.get((s.cursor, req["home_node"])) is None]
    if len(no_route) == len(skilled):
        return Unassigned(req["request_id"], ROUTE_UNKNOWN, "verified",
                          f"{req['home_node']} 로 가는 이동시간이 모든 후보에게 없음")

    reasons = {_try_assign(s, req, travel)[1] for s in skilled}
    if reasons == {TIME_WINDOW_CONFLICT}:
        return Unassigned(req["request_id"], TIME_WINDOW_CONFLICT, "diagnostic",
                          "자격 있는 모든 후보가 시간창·예약 도착을 지킬 수 없음")
    return Unassigned(req["request_id"], NO_CAPACITY, "diagnostic",
                      "자격은 있으나 가용시간(근무·휴게·상한)이 부족")


def _run(order: Iterable[dict], instance: dict, choose) -> BaselineResult:
    """주어진 순서로 요청을 하나씩 확정 배정한다(되돌리지 않음).

    choose(req, feasible) → 고른 인력 상태. feasible 은 [(상태, 도착시각)].
    """
    travel = _travel_map(instance["travel"])
    states = [_State(worker=w, cursor=w["base_node"], free=int(w["shift_start_min"]))
              for w in instance["workers"]]
    result = BaselineResult(algorithm="")

    for req in order:
        feasible = []
        for st in states:
            need = set(req.get("required_skills") or [])
            if not need.issubset(set(st.worker.get("skills") or [])):
                continue
            t, _ = _try_assign(st, req, travel)
            if t is not None:
                feasible.append((st, t))
        if not feasible:
            result.unassigned.append(_diagnose(req, states, travel))
            continue

        st, t = choose(req, feasible, travel)
        leg = int(round(travel[(st.cursor, req["home_node"])]))
        end, span = _occupancy(req, travel, t)
        result.assignments.append({"request_id": req["request_id"],
                                   "worker_id": st.worker["worker_id"],
                                   "arrive_min": t})
        st.work_min += leg + span
        st.cursor = req["home_node"]
        st.free = int(end)

    return result


def both_orders_are_ok(instance: dict, result: BaselineResult) -> bool:
    """결과가 실제로 실행 가능한지 verifier 로 확인한다."""
    from verifier import verify
    return verify(instance, result.assignments).ok


def request_order(instance: dict) -> BaselineResult:
    """신청순: 접수시각 순서, 가장 빨리 시작할 수 있는 인력, 동률은 인력 ID 순."""
    order = sorted(instance["requests"],
                   key=lambda r: (r["requested_at"], r["request_id"]))

    def choose(req, feasible, travel):
        return min(feasible, key=lambda f: (f[1], f[0].worker["worker_id"]))

    res = _run(order, instance, choose)
    res.algorithm = "request_order"
    return res


def nearest(instance: dict) -> BaselineResult:
    """근거리: 우선순위·마감 순서, 추가 이동이 가장 적은 인력, 동률은 도착·ID 순."""
    # priority 숫자가 클수록 급하다(f0: 퇴원 사례 2). 우선순위 내림차순, 마감 오름차순.
    order = sorted(instance["requests"],
                   key=lambda r: (-int(r["priority"]), int(r["latest_start_min"]), r["request_id"]))

    def choose(req, feasible, travel):
        def key(f):
            st, t = f
            leg = travel.get((st.cursor, req["home_node"]))
            return (float("inf") if leg is None else round(float(leg), 3),
                    t, st.worker["worker_id"])
        return min(feasible, key=key)

    res = _run(order, instance, choose)
    res.algorithm = "nearest"
    return res


BASELINES = {"request_order": request_order, "nearest": nearest}
