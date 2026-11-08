"""최적화 모델이 infeasible 로 끝나는 원인을 좁히는 진단 도구.

optimize() 를 그대로 돌리면 "infeasible" 만 보이고 어느 성분이 문제인지 알 수 없다.
여기서는 모델을 성분별로 켜고 끄며 같은 인스턴스를 풀어, 어느 조합에서 풀리는지 본다.

사용:
    python -m tools.diagnose_optimize
    python -m tools.diagnose_optimize --date 2026-11-02 --time-limit 3
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ortools.constraint_solver import pywrapcp, routing_enums_pb2  # noqa: E402

from planner.data import load_day, load_travel, service_dates  # noqa: E402
from planner.optimize import BIG_M, _escort_latest, _span  # noqa: E402

# OR-Tools 상태: 0 NOT_SOLVED / 1 SUCCESS / 2 PARTIAL / 3 FAIL / 4 FAIL_TIMEOUT / 5 INVALID
STATUS_NAME = {0: "NOT_SOLVED", 1: "SUCCESS", 2: "PARTIAL", 3: "FAIL",
               4: "FAIL_TIMEOUT", 5: "INVALID"}


def build_and_solve(inst: dict, *, use_break: bool = True, use_window: bool = True,
                    use_work: bool = True, use_disj: bool = True,
                    use_vehiclevar: bool = True,
                    time_limit: int = 3) -> tuple[int, int, int]:
    """→ (raw_status, 배정 수, 노드 수). 성분을 토글해 가며 같은 인스턴스를 푼다."""
    travel_in = inst["travel"]
    travel = ({(a, b): float(m) for (a, b), m in travel_in.items()}
              if isinstance(travel_in, dict)
              else {(r["from_node"], r["to_node"]): float(r["minutes"]) for r in travel_in})

    workers, requests = inst["workers"], inst["requests"]
    W, R = len(workers), len(requests)
    depot, brk, rnode = (lambda k: k), (lambda k: W + k), (lambda i: 2 * W + i)
    n_nodes = 2 * W + R if use_break else W + R

    def rnode_of(i):
        return rnode(i) if use_break else W + i

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

    work_nodes = {w["base_node"] for w in workers} | {r["home_node"] for r in requests}
    max_travel = int(max((m for (a, b), m in travel.items()
                          if a in work_nodes and b in work_nodes), default=0))

    starts = [depot(k) for k in range(W)]
    manager = pywrapcp.RoutingIndexManager(n_nodes, W, starts, starts)
    routing = pywrapcp.RoutingModel(manager)

    def loc(idx):
        if idx < W:
            return workers[idx]["base_node"]
        if use_break and idx < 2 * W:
            return None
        i = idx - (2 * W if use_break else W)
        return requests[i]["home_node"]

    def svc(idx):
        if use_break and W <= idx < 2 * W:
            return int(workers[idx - W]["break_minutes"])
        if idx >= (2 * W if use_break else W):
            i = idx - (2 * W if use_break else W)
            return spans.get(i, 0)
        return 0

    def tr(a, b):
        if a == b:
            return 0
        if b < W:
            return 0
        la, lb = loc(a), loc(b)
        if la is None:
            return max_travel
        if lb is None:
            return 0
        m = travel.get((la, lb))
        return BIG_M if m is None else int(round(m))

    def time_cb(frm, to):
        a, b = manager.IndexToNode(frm), manager.IndexToNode(to)
        return tr(a, b) + svc(a)

    ti = routing.RegisterTransitCallback(time_cb)
    routing.AddDimension(ti, 1440, 24 * 60 * 2, False, "Time")
    td = routing.GetDimensionOrDie("Time")

    for k, w in enumerate(workers):
        lo, hi = int(w["shift_start_min"]), int(w["shift_end_min"])
        td.CumulVar(routing.Start(k)).SetRange(lo, hi)
        td.CumulVar(routing.End(k)).SetRange(lo, hi)
        if use_break:
            b_lo = int(w["break_start_min"])
            td.CumulVar(manager.NodeToIndex(brk(k))).SetRange(b_lo, b_lo)

    if use_window:
        for i, (lo, hi) in windows.items():
            td.CumulVar(manager.NodeToIndex(rnode_of(i))).SetRange(lo, hi)

    if use_work:
        def work_cb(frm, to):
            a = manager.IndexToNode(frm)
            s = spans.get(a - (2 * W if use_break else W), 0) if a >= (2 * W if use_break else W) else 0
            b = manager.IndexToNode(to)
            return tr(a, b) + s

        wi = routing.RegisterTransitCallback(work_cb)
        caps = [int(w.get("max_work_minutes") or 0) or 24 * 60 for w in workers]
        routing.AddDimensionWithVehicleCapacity(wi, 0, caps, True, "Work")

    routing.SetArcCostEvaluatorOfAllVehicles(ti)

    if use_vehiclevar:
        for k in range(W):
            routing.VehicleVar(manager.NodeToIndex(depot(k))).SetValues([k])
            if use_break:
                routing.VehicleVar(manager.NodeToIndex(brk(k))).SetValues([k])
        for i, req in enumerate(requests):
            need = set(req.get("required_skills") or [])
            allowed = [k for k, w in enumerate(workers)
                       if need.issubset(set(w.get("skills") or []))]
            if allowed:
                routing.VehicleVar(manager.NodeToIndex(rnode_of(i))).SetValues(allowed)

    if use_disj:
        for i, req in enumerate(requests):
            penalty = 100_000 * (int(req.get("priority", 1)) + 1)
            routing.AddDisjunction([manager.NodeToIndex(rnode_of(i))], penalty)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION)
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
    params.time_limit.FromSeconds(int(time_limit))

    assignment = routing.SolveWithParameters(params)
    raw = int(routing.status())
    if assignment is None:
        return raw, 0, n_nodes

    visited = 0
    for k in range(W):
        idx = routing.Start(k)
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            if node >= (2 * W if use_break else W):
                visited += 1
            idx = assignment.Value(routing.NextVar(idx))
    return raw, visited, n_nodes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--time-limit", type=int, default=3)
    args = ap.parse_args()

    data = ROOT / "data" / "processed"
    dates = service_dates(data)
    date = args.date or dates[0]
    inst = load_day(data, date, travel=load_travel(data)).as_instance()

    print(f"인스턴스: {date} | 요청 {len(inst['requests'])} | 인력 {len(inst['workers'])}")
    print()

    combos = [
        ("전체 (현재 설정)",        dict()),
        ("휴게 노드 제거",          dict(use_break=False)),
        ("시간창 제거",             dict(use_window=False)),
        ("근무차원 제거",           dict(use_work=False)),
        ("disjunction 제거",        dict(use_disj=False)),
        ("VehicleVar 제거",         dict(use_vehiclevar=False)),
        ("휴게+시간창 제거",        dict(use_break=False, use_window=False)),
        ("휴게+근무+시간창 제거",   dict(use_break=False, use_work=False, use_window=False)),
    ]

    print(f"{'조합':24s} {'상태':14s} {'배정':>4s} {'노드':>5s}")
    print("-" * 52)
    for name, kw in combos:
        raw, n, nodes = build_and_solve(inst, time_limit=args.time_limit, **kw)
        print(f"{name:24s} {STATUS_NAME.get(raw, str(raw)):14s} {n:4d} {nodes:5d}")

    print()
    print("해석: 한 성분을 끄면 SUCCESS/PARTIAL 로 바뀌면 그 성분이 원인이다.")
    print("      INVALID(5)면 모델 구성 오류, FAIL(3)면 첫 해를 못 찾은 것.")


if __name__ == "__main__":
    main()
