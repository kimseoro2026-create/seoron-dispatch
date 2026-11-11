"""OR-Tools Routing 최적화 — PRD 6장 1차 엔진.

모델 구조
  - 인력 = 차량(vehicle). 각자 자기 기지(depot)에서 출발해 자기 기지로 돌아온다.
  - 요청 = 선택적 방문 노드. 미배정을 허용하되 우선순위별 페널티를 준다(PRD 6.2).
  - 노드 배치: 앞쪽 W개는 인력별 기지, 나머지는 요청. 휴게는 노드가 아니라 고정 시각 구간이다.
  - 시간 차원(분): 도착 시각 = 서비스 시작. 이동 + 서비스시간을 통행에 넣는다.
  - 근무 차원(분): 휴게시간을 빼고 이동 + 서비스만 누적해 초과근무를 막는다.

모델 근사(정직하게 남긴다)
  - 휴게는 '제자리 정지'다(휴게 장소까지의 이동은 따로 없다). 검증기는 휴게가 이동·점유와
    겹치지 않는지 본다.
  - 병원동행은 픽업~귀가를 점유시간이 고정된 단일 노드로 본다(CLAUDE.md 5절).
    점유시간이 도착 시각과 무관하게 일정하므로 시간 차원에 그대로 넣을 수 있다.
  - 미배정 사유는 솔버가 원인을 증명하지 못한다. PRD 6.5 에 따라 SOLVER_UNRESOLVED 로 남긴다.
  - '고유 인원'(같은 사람 2건 = 1명)은 목적에 직접 넣지 않았다. 배정 건수 벌점이 이를 근사한다.

v2(2026-11-11) 변경 — v1 이 근거리 기준선을 이기지 못한 원인을 고쳤다:
  - 휴게를 '장소 없는 노드'가 아니라 OR-Tools 휴게 구간(break interval)으로 둔다.
    v1 은 휴게 뒤 첫 요청의 도착을 '그 집까지 올 수 있는 최대 이동'만큼 일괄로 늦춰,
    실제로는 가능한 오후 배정을 버렸다(후처리 덧붙이기로 일부만 되찾았다).
  - 근거리 기준선의 배정을 첫 해로 넣는다(warm_start). 탐색은 이 해에서 출발해 목적값이
    줄어드는 방향으로만 움직이므로, 목적(우선순위 가중 배정 ≫ 이동) 기준으로 근거리보다 나빠지지 않는다.
  - 비용은 이동시간만 센다. v1 은 서비스시간까지 비용에 넣어 긴 요청을 덜 고르는 치우침이 있었다.
  - 우선순위별 미배정 벌점을 10배 간격으로 둔다. v1 은 2:3 비율이라 일반 2건 > 우선 1건이었다.
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
STATUS_ERROR = "error"

SOLVER_UNRESOLVED = "SOLVER_UNRESOLVED"

PRIORITY_PENALTY_BASE = 100_000   # 일반(1) 요청을 버리는 벌점. 하루 이동시간 합(수백 분)보다 훨씬 크다.
PRIORITY_PENALTY_STEP = 10        # 우선순위가 한 단계 높으면 벌점 10배(높은 우선순위 ≫ 일반, CLAUDE.md 6절)


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


def drop_penalty(priority: int) -> int:
    """요청을 버릴 때의 벌점. 우선순위 2 는 일반 요청 10건을 버리는 것과 같다."""
    return PRIORITY_PENALTY_BASE * PRIORITY_PENALTY_STEP ** max(int(priority) - 1, 0)


@dataclass
class OptimizeResult:
    algorithm: str = "routing"
    status: str = STATUS_ERROR
    assignments: list[dict] = field(default_factory=list)
    unassigned: list[dict] = field(default_factory=list)
    solve_seconds: float = 0.0
    objective: float = 0.0
    warm_started: bool = False

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
             seed: int = 42, repair: bool = True, warm_start: bool = True) -> OptimizeResult:
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
    rnode = lambda i: W + i                # noqa: E731
    n_nodes = W + R

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

    def _location(node: int) -> str:
        return workers[node]["base_node"] if node < W else requests[node - W]["home_node"]

    def _service(node: int) -> int:
        return spans.get(node - W, 0) if node >= W else 0

    def _travel(a: int, b: int) -> int:
        if a == b or b < W:
            return 0        # 기지로 돌아오는 이동은 근무에 넣지 않는다(기준선·검증기와 같은 기준)
        m = travel.get((_location(a), _location(b)))
        return BIG_M if m is None else int(round(m))

    # ── 시간 차원: cumul = 서비스 시작(도착) 시각 ─────────────────────────
    # transit 콜백은 '노드 번호'가 아니라 OR-Tools 내부 routing index 를 받는다.
    # 여기서 노드 번호로 되돌려 계산한다(섞으면 조용히 엉뚱한 이동시간이 나온다).
    # 자격은 '차량별 통행'으로 강제한다. VehicleVar 로 정의역을 자르면 '수행 안 함'이 사라져
    # 버릴 수 있어야 할 요청이 필수 노드가 되고 모델이 infeasible 이 된다.
    need_ok = [[set(req.get("required_skills") or []).issubset(set(w.get("skills") or []))
                for w in workers] for req in requests]

    def _time_transit(k: int):
        def cb(frm: int, to: int) -> int:
            a, b = manager.IndexToNode(frm), manager.IndexToNode(to)
            if b >= W and not need_ok[b - W][k]:
                return BIG_M            # 자격이 없는 요청에는 갈 수 없다
            return _service(a) + _travel(a, b)
        return cb

    time_ids = [routing.RegisterTransitCallback(_time_transit(k)) for k in range(W)]
    routing.AddDimensionWithVehicleTransits(time_ids, 1440, 24 * 60 * 2, False, "Time")
    time_dim = routing.GetDimensionOrDie("Time")

    for k, w in enumerate(workers):
        lo, hi = int(w["shift_start_min"]), int(w["shift_end_min"])
        time_dim.CumulVar(routing.Start(k)).SetRange(lo, hi)
        time_dim.CumulVar(routing.End(k)).SetRange(lo, hi)

    for i, (lo, hi) in windows.items():
        time_dim.CumulVar(manager.NodeToIndex(rnode(i))).SetRange(lo, hi)

    # ── 휴게: 고정 시각 구간. 서비스·이동과 겹치지 않는다 ─────────────────
    # (1) OR-Tools 휴게 구간: 휴게가 서비스(점유)와 겹치지 않게 하고, 휴게 시간만큼 대기를 늘린다.
    #     Python 바인딩에는 '이동 전후' 지정 버전이 없어, 휴게가 이동 도중에 들어가는 것은 막지 못한다.
    # (2) 그래서 구간마다 직접 제약을 더한다. 노드 i 에서 다음 노드로 가는 이동이 휴게와 겹치면 안 된다:
    #       (가) 바로 출발해 휴게 시작 전에 도착: cumul(i) + transit(i) ≤ 휴게 시작
    #       (나) 휴게가 끝난 뒤 출발:          cumul(i) + 점유(i) + 대기(i) ≥ 휴게 끝
    #     (cumul(다음) = cumul(i) + transit(i) + 대기(i), transit = 점유 + 이동)
    #     둘 중 하나는 성립해야 한다. 검증기 기준(이동·점유 모두 휴게와 겹치지 않음)과 같다.
    solver = routing.solver()
    size = routing.Size()
    node_visit = [_service(manager.IndexToNode(ix)) if ix < size else 0 for ix in range(size)]
    has_break = []
    for k, w in enumerate(workers):
        b_lo, b_len = int(w["break_start_min"]), int(w["break_minutes"])
        has_break.append(b_len > 0)
        if b_len <= 0:
            continue
        brk = solver.FixedDurationIntervalVar(b_lo, b_lo, b_len, False, f"break_{k}")
        time_dim.SetBreakIntervalsOfVehicle([brk], k, node_visit)

    if any(has_break):
        far = -10 ** 6      # 수행하지 않는 노드(차량 −1)는 제약이 저절로 성립하도록 아주 이른 휴게로 둔다
        b_starts = [far] + [int(w["break_start_min"]) if hb else far
                            for w, hb in zip(workers, has_break)]
        b_ends = [far] + [int(w["break_start_min"]) + int(w["break_minutes"]) if hb else far
                          for w, hb in zip(workers, has_break)]
        for ix in range(size):
            if routing.IsEnd(ix):
                continue
            veh1 = routing.VehicleVar(ix) + 1
            b_s = solver.Element(b_starts, veh1)
            b_e = solver.Element(b_ends, veh1)
            cum, tr, sl = time_dim.CumulVar(ix), time_dim.TransitVar(ix), time_dim.SlackVar(ix)
            svc = node_visit[ix]
            before = solver.IsLessOrEqualCstVar(cum + tr - b_s, 0)
            after = solver.IsGreaterOrEqualCstVar(cum + sl + svc - b_e, 0)
            solver.Add(before + after >= 1)

    # ── 근무 차원: 휴게를 빼고 이동 + 서비스만 누적(초과근무 금지) ─────────
    def work_cb(frm: int, to: int) -> int:
        a, b = manager.IndexToNode(frm), manager.IndexToNode(to)
        return _service(a) + _travel(a, b)

    work_idx = routing.RegisterTransitCallback(work_cb)
    caps = [int(w.get("max_work_minutes") or 0) or 24 * 60 for w in workers]
    routing.AddDimensionWithVehicleCapacity(work_idx, 0, caps, True, "Work")

    # ── 비용: 이동시간만 ──────────────────────────────────────────────────
    cost_idx = routing.RegisterTransitCallback(
        lambda frm, to: _travel(manager.IndexToNode(frm), manager.IndexToNode(to)))
    routing.SetArcCostEvaluatorOfAllVehicles(cost_idx)

    # ── 미배정 허용(우선순위별 벌점) ───────────────────────────────────────
    for i, req in enumerate(requests):
        routing.AddDisjunction([manager.NodeToIndex(rnode(i))],
                               drop_penalty(req.get("priority", 1)))

    params = pywrapcp.DefaultRoutingSearchParameters()
    # 미배정을 허용하는 모델에서는 PATH_CHEAPEST_ARC 가 첫 해를 못 찾는 경우가 많아 삽입 기반을 쓴다.
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION)
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
    params.time_limit.FromSeconds(int(time_limit_sec))
    params.log_search = False

    # ── 첫 해: 근거리 기준선의 배정 순서 ───────────────────────────────────
    t0 = time.time()
    assignment = None
    warm = False
    # 휴게 제약을 직접 더한 모델에서는 삽입 기반 첫 해 전략이 해를 못 찾는 경우가 많다(실측: 표본 8일 모두 실패).
    # 그래서 탐색은 항상 주어진 첫 해에서 출발한다: 근거리 배정, 그것이 없으면 '아무도 배정하지 않은 빈 일정'
    # (빈 일정은 모든 제약을 지키므로 언제나 출발점이 된다).
    routes = _warm_start_routes(instance, workers, requests, skip) if warm_start else None
    warm = routes is not None
    if routes is None:
        routes = [[] for _ in workers]
    routing.CloseModelWithParameters(params)
    init = routing.ReadAssignmentFromRoutes(
        [[manager.NodeToIndex(rnode(i)) for i in route] for route in routes], True)
    if init is not None:
        assignment = routing.SolveFromAssignmentWithParameters(init, params)
    else:
        warm = False
    if assignment is None:
        assignment = routing.SolveWithParameters(params)
    elapsed = round(time.time() - t0, 2)

    status = map_status(int(routing.status()))
    result = OptimizeResult(status=status, solve_seconds=elapsed, warm_started=warm)

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
            if node >= W:
                i = node - W
                # 모델에서 제외한 요청은 시간창을 걸지 않았으므로 결과에서도 버린다.
                if i not in skip:
                    visited.add(i)
                    result.assignments.append({
                        "request_id": requests[i]["request_id"],
                        "worker_id": workers[k]["worker_id"],
                        # 가장 이른 가능 도착(시간창·휴게를 지키는 하한)
                        "arrive_min": int(assignment.Min(time_dim.CumulVar(idx))),
                    })
            idx = assignment.Value(routing.NextVar(idx))

    for i, req in enumerate(requests):
        if i not in visited:
            reason = "ROUTE_UNKNOWN" if i in skip else SOLVER_UNRESOLVED
            certainty = "verified" if i in skip else "unknown"
            result.unassigned.append({"request_id": req["request_id"],
                                      "reason": reason, "certainty": certainty})

    result.assignments.sort(key=lambda a: a["request_id"])

    # ── 후처리(안전장치): 버려진 요청을 실제 이동시간으로 덧붙여 본다 ─────────
    # v2 모델은 휴게를 정확히 다루므로 보통 아무것도 더하지 않는다. 검증기를 통과할 때만 채택.
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


def _warm_start_routes(instance: dict, workers: list[dict], requests: list[dict],
                       skip: set[int]) -> list[list[int]] | None:
    """근거리 기준선 배정 → 인력별 요청 순서(요청 번호 목록). 검증을 통과하지 못하면 None."""
    from planner.baseline import nearest
    from verifier import verify

    base = nearest(instance)
    if not base.assignments or not verify(instance, base.assignments).ok:
        return None
    pos = {r["request_id"]: i for i, r in enumerate(requests)}
    wpos = {w["worker_id"]: k for k, w in enumerate(workers)}
    routes: list[list[int]] = [[] for _ in workers]
    for a in sorted(base.assignments, key=lambda x: x["arrive_min"]):
        i = pos[a["request_id"]]
        if i not in skip:
            routes[wpos[a["worker_id"]]].append(i)
    return routes


def _repair(instance: dict, assignments: list[dict], dropped_ids: list[str],
            travel: dict, time_limit_sec: int = 0) -> list[dict]:
    """모델이 버린 요청을 **실제 이동시간**으로 다시 끼워 넣어 본다.

    v1 에서는 휴게 근사 때문에 버려진 배정을 되찾는 데 필요했다. v2 에서는 안전장치로만 남긴다.

    안전장치:
      - 기존 배정은 건드리지 않는다. 각 인력의 마지막 위치·가용 시각 뒤에 덧붙이기만 한다.
      - 그래도 검증기를 다시 통과한 경우에만 채택한다. 실패하면 원래 결과를 그대로 둔다.

    정직하게: 이 후처리를 켠 optimize 는 'OR-Tools 결과 + 조건부 덧붙이기'다.
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
