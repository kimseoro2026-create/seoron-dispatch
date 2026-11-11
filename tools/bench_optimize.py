"""최적화 소규모 비교 — 표본 날짜에서 근거리와 최적화를 나란히 돌려 본다.

30일 전체 실험(약 45분) 전에 바뀐 엔진을 빨리 확인하는 용도다. 숫자는 모두 계산값이고,
모든 결과는 검증기로 다시 확인한다(위반이 있으면 표시).

실행: python -m tools.bench_optimize --dates 6 --rho 0.9 --seeds 1 2 --limit 10
"""
from __future__ import annotations

import argparse
import sys
import time


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data/processed")
    ap.add_argument("--dates", type=int, default=6, help="앞에서부터 날짜 수(0=전체)")
    ap.add_argument("--rho", type=float, nargs="+", default=[0.9])
    ap.add_argument("--seeds", type=int, nargs="+", default=[1])
    ap.add_argument("--limit", type=int, default=10, help="최적화 계산 제한(초)")
    a = ap.parse_args(argv)

    from planner.baseline import nearest
    from planner.data import load_day, load_travel, service_dates
    from planner.optimize import optimize
    from simulator.experiment import select_requests
    from verifier import verify

    travel = load_travel(a.data_dir)
    dates = service_dates(a.data_dir)
    dates = dates[: a.dates] if a.dates else dates

    def people(inst, assigns):
        by = {r["request_id"]: r["beneficiary_id"] for r in inst["requests"]}
        return len({by[x["request_id"]] for x in assigns})

    def travel_min(inst, assigns):
        reqs = {r["request_id"]: r for r in inst["requests"]}
        base = {w["worker_id"]: w["base_node"] for w in inst["workers"]}
        tot = 0.0
        by_w: dict[str, list] = {}
        for x in assigns:
            by_w.setdefault(x["worker_id"], []).append(x)
        for wid, xs in by_w.items():
            cur = base[wid]
            for x in sorted(xs, key=lambda y: y["arrive_min"]):
                h = reqs[x["request_id"]]["home_node"]
                tot += inst["travel"].get((cur, h), 0.0)
                cur = h
        return round(tot)

    win = lose = tie = 0
    sums = {"near": [0, 0, 0], "opt": [0, 0, 0]}
    print(f"{'날짜':<11}{'ρ':>4}{'시드':>4} | 근거리 건/명/이동 | 최적화 건/명/이동  상태    초  위반")
    for date in dates:
        base = load_day(a.data_dir, date, travel=travel).as_instance()
        for rho in a.rho:
            for seed in a.seeds:
                inst = dict(select_requests(base, rho, seed), service_date=date)
                n = nearest(inst)
                t0 = time.time()
                o = optimize(inst, time_limit_sec=a.limit, seed=seed)
                secs = time.time() - t0
                vn, vo = verify(inst, n.assignments), verify(inst, o.assignments)
                rn = (len(n.assignments), people(inst, n.assignments), travel_min(inst, n.assignments))
                ro = (len(o.assignments), people(inst, o.assignments), travel_min(inst, o.assignments))
                for k, r in (("near", rn), ("opt", ro)):
                    sums[k] = [s + v for s, v in zip(sums[k], r)]
                key_n, key_o = (rn[0], rn[1]), (ro[0], ro[1])
                if key_o > key_n:
                    win += 1
                elif key_o < key_n:
                    lose += 1
                else:
                    tie += 1
                viol = len(vn.violations) + len(vo.violations)
                print(f"{date:<11}{rho:>4}{seed:>4} | {rn[0]:>4}/{rn[1]:>3}/{rn[2]:>4}   | "
                      f"{ro[0]:>4}/{ro[1]:>3}/{ro[2]:>4}   {o.status:<8}{secs:>4.1f} {viol}", flush=True)
    n_obs = win + lose + tie
    print()
    print(f"관측 {n_obs} · 최적화 승 {win} / 패 {lose} / 동률 {tie}  (비교 기준: 배정 건수, 다음 고유 인원)")
    for k, label in (("near", "근거리"), ("opt", "최적화")):
        s = sums[k]
        print(f"{label}: 평균 배정 {s[0] / n_obs:.2f}건 · 고유 {s[1] / n_obs:.2f}명 · 이동 {s[2] / n_obs:.0f}분")


if __name__ == "__main__":
    main()
