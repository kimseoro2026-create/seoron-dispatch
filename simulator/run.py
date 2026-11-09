"""실험 실행 CLI.

사용 예:
    # 파일럿 (1일·1시드·2부하율·최적화 제외) — 약 1분
    python -m simulator.run --days 3 --seeds 42 --rhos 0.6,0.9 --no-optimize

    # 소규모 전체 (5일·3시드·3부하율·최적화 포함) — 약 30분
    python -m simulator.run --days 5

    # 전체 30일
    python -m simulator.run

결과 CSV:
    data/experiments/run-<날짜시각>.csv    원본 행
    data/experiments/summary-<날짜시각>.csv 요약 집계
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser(description="배정 방식 비교 실험")
    ap.add_argument("--days",       type=int,   default=None,    help="날짜 수 (기본: 전체 30일)")
    ap.add_argument("--seeds",      type=str,   default="42,43,44", help="시드 목록 (쉼표 구분)")
    ap.add_argument("--rhos",       type=str,   default="0.6,0.9,1.1", help="부하율 목록 (쉼표 구분)")
    ap.add_argument("--time-limit", type=int,   default=10,      help="최적화 계산 제한(초)")
    ap.add_argument("--no-optimize",action="store_true",          help="최적화 방식 제외 (빠른 기준선 비교)")
    ap.add_argument("--replay",     action="store_true",          help="취소 재배정 포함 (request_order_replay, nearest_replay)")
    ap.add_argument("--tag",        type=str,   default="run",   help="출력 파일 접두어")
    args = ap.parse_args()

    seeds  = [int(s) for s in args.seeds.split(",")]
    rhos   = [float(r) for r in args.rhos.split(",")]
    algos  = ["request_order", "nearest"] if args.no_optimize else \
             ["request_order", "nearest", "optimize"]
    if args.replay:
        # 같은 날 초기 배정을 같게 하고, 취소 후 재배정만 다른 결과를 비교한다
        algos = algos + ["request_order_replay", "nearest_replay"]

    from planner.data import service_dates
    from simulator.experiment import run_experiment, save, summarize

    data = ROOT / "data" / "processed"
    all_dates = service_dates(data)
    if args.days:
        all_dates = all_dates[:args.days]

    total = len(all_dates) * len(seeds) * len(rhos) * len(algos)
    print(f"실험 설정: {len(all_dates)}일 × {len(seeds)}시드 × {len(rhos)}부하율 × {len(algos)}방식 = {total}건")
    print(f"방식: {algos}")
    print(f"부하율: {rhos}  |  시드: {seeds}")
    if not args.no_optimize:
        print(f"최적화 계산 제한: {args.time_limit}초/건  |  최적화 예상 시간: {len(all_dates)*len(seeds)*len(rhos)*args.time_limit//60}분+")
    print()

    done = [0]

    def progress(msg: str) -> None:
        done[0] += 1
        print(f"[{done[0]:4d}/{total}] {msg}", flush=True)

    df = run_experiment(
        data,
        algorithms=algos,
        load_factors=rhos,
        seeds=seeds,
        dates=all_dates,
        time_limit_sec=args.time_limit,
        progress=progress,
    )

    raw_path = save(df, ROOT / "data" / "experiments", tag=args.tag)
    print(f"\n원본 저장: {raw_path}  ({len(df)}행)")

    summ = summarize(df)
    summ_path = save(summ, ROOT / "data" / "experiments", tag=f"summary-{args.tag}")
    print(f"요약 저장: {summ_path}")
    print()

    # 화면 출력 — 부하율별 방식 비교
    print(summ.to_string(index=False))

    # 실패를 성공으로 보이게 하지 않는다 (CLAUDE.md 절대원칙 3)
    total_viol = int(df["violations"].sum())
    empty = df[(df["assigned"] == 0) & (df["requests"] > 0)]
    bad_status = df[~df["status"].isin(["ok", "optimal", "feasible"])]

    problems = []
    if total_viol:
        problems.append(f"하드 제약 위반 {total_viol}건 — 해당 배정안은 사용할 수 없습니다.")
    if len(empty):
        names = sorted(empty["algorithm"].unique())
        problems.append(
            f"배정이 0건인 실행이 {len(empty)}건 있습니다 ({', '.join(names)}). "
            f"이 결과는 '잘 지켰다'가 아니라 '아무것도 못 했다'입니다.")
    if len(bad_status):
        counts = bad_status["status"].value_counts().to_dict()
        problems.append(f"정상 종료가 아닌 실행: {counts}")

    if problems:
        print()
        for p in problems:
            print(f"경고: {p}")
    else:
        print("\n위반 0건, 모든 실행에서 배정이 있었습니다.")


if __name__ == "__main__":
    main()
