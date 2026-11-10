"""OR-Tools Routing 최적화 — PRD 6장 1차 엔진.

모델 구조
  - 인력 = 차량(vehicle). 각자 자기 기지(depot)에서 출발해 자기 기지로 돌아온다.
  - 요청 = 선택적 방문 노드. 미배정을 허용하되 우선순위별 페널티를 준다(PRD 6.2).
  - 노드 배치: 앞쪽 W개는 인력별 기지, 다음 W개는 인력별 휴게 노드, 나머지는 요청.
  - 시간 차원(분): 도착 시각 = 서비스 시작. 이동 + 서비스시간을 arc 비용에 넣는다.
  - 근무 차원(분): 휴게시간을 빼고 이동 + 서비스만 누적해 초과근무를 막는다.

모델 근사(정직하게 남긴다)
  - 휴게는 '제자리 정지'로 모델링한다(휴게 노드의 양방향 이동시간 0).
    실제로는 휴게 장소까지의 이동이 있을 수 있으나, 검증기는 휴게와 점유가 겹치지 않는지만 본다.
  - 병원동행은 픽업~귀가를 점유시간이 고정된 단일 노드로 본다(CLAUDE.md 5절).
    점유시간이 도착 시각과 무관하게 일정하므로 시간 차원에 그대로 넣을 수 있다.
  - 미배정 사유는 솔버가 원인을 증명하지 못한다. PRD 6.5 에 따라 SOLVER_UNRESOLVED 로 남긴다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

BIG_M = 100_000          # 이동시간이 없는 구간. 0으로 대체하지 않는다.
TIME_LIMIT_SEC = 30      # CLAUDE.md 6절 계산 제한

STATUS_OPTIMAL = "optimal"
STATUS_FEASIBLE = "feasible"
STATUS_INFEASIBLE = "infeasible"
STATUS_TIMEOUT = "timeout"


def map_status(raw: int) -> str:
    """OR-Tools RoutingSearchStatus 정수값 → 엔진 상태.

    버전마다 상수 위치가 달라 정수로 비교한다(9.15 기준 enum 값으로 확인).
    0 NOT_SOLVED / 1 SUCCESS / 2 PARTIAL_SUCCESS_LOCAL_OPTIMUM_NOT_REACHED / 3 FAIL /
    4 FAIL_TIMEOUT / 5 INVALID / 6 INFEASIBLE / 7 OPTIMAL

    SUCCESS(1)는 '제한시간 안에 해를 찾았다'는 뜻이지 최적 증명이 아니다 → feasible.
    INVALID(5)는 모델을 잘못 만든 것이므로 infeasible 이 아니라 error 로 둔다.
    """
    if raw == 7:
        return STATUS_OPTIMAL
    if raw in (1, 2):
        return STATUS_FEASIBLE
    if raw == 4:
        return STATUS_TIMEOUT
    if raw in (3, 6):
        return STATUS_INFEASIBLE
    return STATUS_ERROR
STATUS_ERROR = "error"

SOLVER_UNRESOLVED = "SOLVER_UNRESOLVED"


@dataclass
class OptimizeResult:
    algorithm: str = "routing"
    status: str = STATUS_ERROR
    assignments: list[dict] = field(default_factory=list)
    unassigned: list[dict] = field(default_factory=list)
    solve_seconds: float = 0.0
    objective: float = 0.0

    def assigned_count(self) -> int:
        return len(self.assignments)

    def unassigned_count(self) -> int:
        return len(self.unassigned)


def _span(req: dict, travel: dict) -> int | None:
    """요청 하나의 점유 길이(분). 병원동행은 이동까지 포함한다. 경로가 없으면 None."""
    if req["service_type"] != "hospital_escort":
        return int(req["service_minutes"])
    to_h = travel.get((req["home_node"], req["hospital_node"]))
    back = travel.get((req["hospital_node"], req["home_node"]))
    if to_h is None or back is None:
        return None
    return int(round(to_h)) + int(req["hospital_stay_min"]) + int(round(back))


def _escort_latest(req: dict, travel: dict) -> int:
    """예약 도착 여유를 시간창의 상한으로 접어 넣는다.

    병원 도착 = 픽업 + 이동(집→병원) 이므로,
    픽업 ≤ 예약 − 여유 − 이동(집→병원) 이어야 예약을 지킨다.
    """
    to_h = travel.get((req["home_node"], req["hospital_node"]))
    if to_h is None:
        return int(req["latest_start_min"])
    deadline = int(req["appointment_min"]) - int(req["arrival_buffer_min"]) - int(round(to_h))
    return min(int(req["latest_start_min"]), deadline)


def optimize(instance: dict, time_limit_sec: int = TIME_LIMIT_SEC,
             seed: int = 42, repair: bool = True) -> OptimizeResult:
    travel_in = instance["travel"]
    travel = ({(a, b): float(m) for (a, b), m in travel_in.items()}
              if isinstance(travel_in, dict)
              else {(r["from_node"], r["to_node"]): float(r["minutes"]) for r in travel_in})

    workers = instance["workers"]
    requests = instance["requests"]
    W, R = len(workers), len(requests)
    if W == 0 or R == 0:
        return OptimizeResult(status=STATUS_INFEASIBLE,
                              unassigned=[{"request_id": r["request_id"],
                                           "reason": SOLVER_UNRESOLVED, "certainty": "unknown"}
                                          for r in requests])

    depot = lambda k: k                    # noqa: E731
    brk = lambda k: W + k                  # noqa: E731
    rnode = lambda i: 2 * W + i            # noqa: E731
    n_nodes = 2 * W + R

    # 휴게 노드 뒤 요청으로 가는 구간의 이동시간.
    # 휴게에는 장소가 없어 '어디서 출발했는지'를 모델이 알 수 없다. 검증기는
    # 출발(도착 − 실제이동)이 휴게 종료 이후인지 본다. 따라서 도착이 '휴게종료 + 그 요청까지의
    # 최대 이동' 이상이어야 한다.
    #
    # 전역 최댓값을 쓰면 안 된다. 그러면 모든 오후 요청의 도착이 같은 값으로 고정되어,
    # 시간창이 조금이라도 짧은 요청이 통째로 탈락한다(실측: 30초를 줘도 개선이 없었다).
    # 요청마다 '그 집까지 올 수 있는 최대 이동'만 더한다.
    work_nodes = {w["base_node"] for w in workers} | {r["home_node"] for r in requests}

    def _max_inbound(node: str) -> int:
        # 올림(ceil)을 써야 한다. 내림하면 실제 이동 > pad 가 되어
        # 모델이 허용한 도착 시각에서 실제 이동을 빼면 출발이 휴게 시작 전으로 들어간다.
        import math
        vals = [m for (a, b), m in travel.items() if b == node and a in work_nodes]
        return math.ceil(max(vals)) if vals else 0

    pad = {i: _max_inbound(req["home_node"]) for i, req in enumerate(requests)}

    # 요청별 점유 길이와 유효 시간창 (경로가 없으면 모델에서 제외)
    spans, windows, skip = {}, {}, set()
    for i, req in enumerate(requests):
        s = _span(req, travel)
        if s is None:
            skip.add(i)
            continue
        lo = int(req["earliest_start_min"])
        hi = (_escort_latest(req, travel) if req["service_type"] == "hospital_escort"
              else int(req["latest_start_min"]))
        if hi < lo:
            skip.add(i)
            continue
        spans[i], windows[i] = s, (lo, hi)

    manager = pywrapcp.RoutingIndexManager(
        n_nodes, W, [depot(k) for k in range(W)], [depot(k) for k in range(W)])
    routing = pywrapcp.RoutingModel(manager)

    def _location(idx: int) -> str | None:
        if idx < W:
            return workers[idx]["base_node"]
        if idx < 2 * W:
            return None                     # 휴게 노드는 장소가 없다(제자리)
        return requests[idx - 2 * W]["home_node"]

    def _service(idx: int) -> int:
        if W <= idx < 2 * W:
            return int(workers[idx - W]["break_minutes"])
        if idx >= 2 * W:
            i = idx - 2 * W
            return spans.get(i, 0)
        return 0

    def _travel(a: int, b: int) -> int:
        if a == b:
            return 0
        if b < W:
            return 0
        la, lb = _location(a), _location(b)
        # 휴게 노드에서 요청 j로 가는 구간: 그 집까지의 최대 이동만 더한다.
        # 전역 최댓값을 쓰면 시간창이 좁은 오후 요청이 통째로 탈락한다.
        if la is None:
            return pad.get(b - 2 * W, 0) if b >= 2 * W else 0
        if lb is None:
            return 0
        m = travel.get((la, lb))
        return BIG_M if m is None else int(round(m))

    # ── 시간 차원: cumul = 서비스 시작 시각 ────────────────────────────────
    # transit 콜백은 '노드 번호'가 아니라 OR-Tools 내부 routing index 를 받는다.
    # 여기서 노드 번호로 되돌려 계산한다(섞으면 조용히 엉뚱한 이동시간이 나온다).
    #
    # 자격과 휴게 소유권은 '차량별 통행'으로 강제한다.
    # VehicleVar(...).SetValues() 로 막으면 변수 정의역이 잘려 '수행 안 함' 상태가 사라지고,
    # AddDisjunction 으로 버릴 수 있어야 할 요청이 필수 노드가 되어 모델이 infeasible 이 된다.
    # (진단 결과: VehicleVar 만 제거해도 SUCCESS 로 바뀌었다)
    need_ok = [[set(req.get("required_skills") or []).issubset(set(w.get("skills") or []))
                for w in workers] for req in requests]

    def _time_transit(k: int):
        def cb(frm: int, to: int) -> int:
            a, b = manager.IndexToNode(frm), manager.IndexToNode(to)
            if W <= b < 2 * W and (b - W) != k:
                return BIG_M            # 남의 휴게 노드에는 갈 수 없다
            if b >= 2 * W and not need_ok[b - 2 * W][k]:
                return BIG_M            # 자격이 없는 요청에는 갈 수 없다
            return _travel(a, b) + _service(a)
        return cb

    time_ids = [routing.RegisterTransitCallback(_time_transit(k)) for k in range(W)]
    routing.AddDimensionWithVehicleTransits(time_ids, 1440, 24 * 60 * 2, False, "Time")
    time_dim = routing.GetDimensionOrDie("Time")

    for k, w in enumerate(workers):
        lo, hi = int(w["shift_start_min"]), int(w["shift_end_min"])
        time_dim.CumulVar(routing.Start(k)).SetRange(lo, hi)
        time_dim.CumulVar(routing.End(k)).SetRange(lo, hi)
        b_lo = int(w["break_start_min"])
        # 휴게는 [b_lo, b_lo + break_minutes] 를 정확히 차지한다.
        # cumul 을 움직일 수 있게 두면 휴게가 밀려나 검증기 기준(휴게 구간 고정)과 어긋난다.
        time_dim.CumulVar(manager.NodeToIndex(brk(k))).SetRange(b_lo, b_lo)

    for i, (lo, hi) in windows.items():
        time_dim.CumulVar(manager.NodeToIndex(rnode(i))).SetRange(lo, hi)

    # ── 근무 차원: 휴게를 빼고 이동 + 서비스만 누적 ────────────────────────
    def work_cb(frm: int, to: int) -> int:
        a = manager.IndexToNode(frm)
        svc = 0
        if a >= 2 * W:
            svc = spans.get(a - 2 * W, 0)
        b = manager.IndexToNode(to)
        return _travel(a, b) + svc

    work_idx = routing.RegisterTransitCallback(work_cb)
    caps = [int(w.get("max_work_minutes") or 0) or 24 * 60 for w in workers]
    routing.AddDimensionWithVehicleCapacity(work_idx, 0, caps, True, "Work")

    # ── 비용: 이동시간 최소화 ──────────────────────────────────────────────
    # 비용용 콜백은 차량을 구분하지 않는다. 자격 위반 arc 는 시간 차원에서 BIG_M 으로
    # 이미 실행 불가능하므로 비용에서 따로 벌점을 줄 필요가 없다.
    def _cost_cb(frm: int, to: int) -> int:
        a, b = manager.IndexToNode(frm), manager.IndexToNode(to)
        return _travel(a, b) + _service(a)

    cost_idx = routing.RegisterTransitCallback(_cost_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(cost_idx)

    # ── 미배정 허용 ────────────────────────────────────────────────────────
    # 요청 노드는 반드시 버릴 수 있어야 한다(우선순위별 페널티).
    # 차량 제한은 위의 차량별 통행 콜백이 맡는다. VehicleVar 로 정의역을 자르면
    # '수행 안 함' 상태가 사라져 모델이 infeasible 이 된다(진단으로 확인).
    for i, req in enumerate(requests):
        penalty = 100_000 * (int(req.get("priority", 1)) + 1)
        routing.AddDisjunction([manager.NodeToIndex(rnode(i))], penalty)

    params = pywrapcp.DefaultRoutingSearchParameters()
    # 미배정을 허용하는 모델(AddDisjunction)에서는 PATH_CHEAPEST_ARC 가 첫 해를 못 찾는 경우가 많다.
    # 요청을 '버리는' 선택지를 다루지 못하기 때문이다. 삽입 기반 전략을 쓴다.
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION)
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
    params.time_limit.FromSeconds(int(time_limit_sec))
    params.log_search = False

    t0 = time.time()
    assignment = routing.SolveWithParameters(params)
    elapsed = round(time.time() - t0, 2)

    status = map_status(int(routing.status()))

    result = OptimizeResult(status=status, solve_seconds=elapsed)

    if assignment is None:
        # 해가 없다. timeout 을 '미배정 0건'으로 보이게 하지 않는다(CLAUDE.md 6절).
        result.unassigned = [{"request_id": r["request_id"], "reason": SOLVER_UNRESOLVED,
                              "certainty": "unknown"} for r in requests]
        return result

    result.objective = float(assignment.ObjectiveValue())

    visited: set[int] = set()
    for k in range(W):
        idx = routing.Start(k)
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            if node >= 2 * W:
                i = node - 2 * W
                # 모델에서 제외한 요청은 제약 없이 배정될 수 있으므로 결과에서도 버린다.
                # (시간창을 설정하지 않았으므로 그 배정은 시간창을 지킨다는 보장이 없다)
                if i in skip:
                    idx = assignment.Value(routing.NextVar(idx))
                    continue
                visited.add(i)
                result.assignments.append({
                    "request_id": requests[i]["request_id"],
                    "worker_id": workers[k]["worker_id"],
                    "arrive_min": int(assignment.Value(time_dim.CumulVar(idx))),
                })
            idx = assignment.Value(routing.NextVar(idx))

    for i, req in enumerate(requests):
        if i not in visited:
            reason = "ROUTE_UNKNOWN" if i in skip else SOLVER_UNRESOLVED
            certainty = "verified" if i in skip else "unknown"
            result.unassigned.append({"request_id": req["request_id"],
                                      "reason": reason, "certainty": certainty})

    result.assignments.sort(key=lambda a: a["request_id"])

    # ── 후처리: 버려진 요청을 실제 이동시간으로 재시도 ──────────────────────
    # 모델이 pad[j](상한) 기준으로 버린 요청이 실제 이동으로는 가능할 수 있다.
    # 기존 배정은 건드리지 않고, 미배정 중 SOLVER_UNRESOLVED 만 대상으로 한다.
    if repair:
        dropped = [u["request_id"] for u in result.unassigned
                   if u.get("reason") == SOLVER_UNRESOLVED]
        if dropped:
            added = _repair(instance, result.assignments, dropped, travel)
            if added:
                from verifier import verify
                trial = result.assignments + added
                rep = verify(instance, trial)
                if not rep.violations:
                    result.assignments = sorted(trial, key=lambda a: a["request_id"])
                    added_ids = {a["request_id"] for a in added}
                    result.unassigned = [u for u in result.unassigned
                                         if u["request_id"] not in added_ids]

    return result


def _repair(instance: dict, assignments: list[dict], dropped_ids: list[str],
            travel: dict, time_limit_sec: int = 0) -> list[dict]:
    """모델이 버린 요청을 **실제 이동시간**으로 다시 끼워 넣어 본다.

    왜 필요한가:
      휴게 노드에는 장소가 없어서, 휴게 뒤 요청의 도착을 '모델에서는' 840+pad 로 밀어 놓는다
      (pad = 그 집까지 올 수 있는 최대 이동). 실제 이동이 pad 보다 짧은 경우 모델은
      실제로는 가능한 배정을 버리게 된다. 이 후처리는 그 손실만 되돌린다.

    안전장치:
      - 기존 배정은 건드리지 않는다. 각 인력의 마지막 위치·가용 시각 뒤에 덧붙이기만 한다.
      - 그래도 검증기를 다시 통과한 경우에만 채택한다. 실패하면 원래 결과를 그대로 둔다.

    정직하게: 이 후처리를 켠 optimize 는 'OR-Tools 결과 + 조건부 덧붙이기'다.
    비교표에는 그대로 적는다.
    """
    from planner.baseline import _State, _occupancy, _try_assign

    workers = {w["worker_id"]: w for w in instance["workers"]}
    reqs = {r["request_id"]: r for r in instance["requests"]}

    states = {wid: _State(worker=w, cursor=w["base_node"], free=int(w["shift_start_min"]))
              for wid, w in workers.items()}

    # 기존 배정을 시간순으로 재생하며 각 인력의 상태를 맞춘다
    by_worker: dict[str, list[dict]] = {}
    for a in assignments:
        by_worker.setdefault(a["worker_id"], []).append(a)
    for wid, alist in by_worker.items():
        st = states[wid]
        for a in sorted(alist, key=lambda x: x["arrive_min"]):
            req = reqs[a["request_id"]]
            leg = travel.get((st.cursor, req["home_node"]))
            leg = int(round(leg)) if leg is not None else 0
            end, span = _occupancy(req, travel, int(a["arrive_min"]))
            if end is None:
                continue
            st.work_min += leg + span
            st.cursor = req["home_node"]
            st.free = int(end)

    # 버려진 요청을 우선순위·마감 순으로 다시 시도
    cand = [reqs[r] for r in dropped_ids if r in reqs]
    cand.sort(key=lambda r: (-int(r["priority"]), int(r["latest_start_min"]), r["request_id"]))

    added = []
    for req in cand:
        best = None
        for wid, st in states.items():
            need = set(req.get("required_skills") or [])
            if not need.issubset(set(st.worker.get("skills") or [])):
                continue
            t, _ = _try_assign(st, req, travel)
            if t is None:
                continue
            if best is None or t < best[1]:
                best = (st, t)
        if best is None:
            continue

        st, t = best
        leg = travel.get((st.cursor, req["home_node"]))
        leg = int(round(leg)) if leg is not None else 0
        end, span = _occupancy(req, travel, t)
        if end is None:
            continue
        st.work_min += leg + span
        st.cursor = req["home_node"]
        st.free = int(end)
        added.append({"request_id": req["request_id"],
                      "worker_id": st.worker["worker_id"], "arrive_min": t})

    return added
