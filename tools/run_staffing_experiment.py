"""동행 인력 실험 — 가사 인력 교육(같은 인원) · 동행 인력 추가 채용 · 휴게 시각 분산.

같은 날·같은 시드·같은 요청에서 인력 구성만 바꿔 세 방식을 비교한다(짝지은 비교).
부하율 ρ 는 원래 인력 기준으로 요청을 고른 뒤 인력을 바꾸므로, 인력이 늘면 actual_rho 가 낮아진다.
휴게 분산(staggered)은 인력마다 휴게 시작을 11·12·13·14시로 돌아가며 둔다(기본은 모두 12시).
결과: data/experiments/staffing-YYYYMMDD-HHMMSS.csv (열 retrain, add, breaks 추가)

실행: python -m tools.run_staffing_experiment                     # 기본 6조건, ρ 0.9, 30일 × 3시드
      python -m tools.run_staffing_experiment --days 2 --seeds 42 # 빠른 확인
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

# (교육 인원, 추가 채용 인원, 휴게). (0, 0, fixed) = 현재 구성(동행 가능 8명, 모두 12시 휴게)
DEFAULT_CELLS = [(0, 0, "fixed"), (4, 0, "fixed"), (0, 4, "fixed"),
                 (0, 0, "staggered"), (4, 0, "staggered"), (0, 4, "staggered")]


def _cell(s: str) -> tuple[int, int, str]:
    r, a, b = s.split(":")
    if b not in ("fixed", "staggered"):
        raise argparse.ArgumentTypeError("휴게는 fixed 또는 staggered")
    return int(r), int(a), b


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data/processed")
    ap.add_argument("--cells", type=_cell, nargs="+", default=DEFAULT_CELLS, help="교육:추가:휴게 (예: 0:0:fixed 0:4:staggered)")
    ap.add_argument("--rho", type=float, default=0.9)
    ap.add_argument("--seeds", type=int, nargs="+", default=[42, 43, 44])
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--time-limit", type=int, default=10)
    a = ap.parse_args(argv)

    from planner.data import service_dates
    from simulator.experiment import change_staffing, run_experiment, stagger_breaks

    dates = service_dates(a.data_dir)
    dates = dates[: a.days] if a.days else dates
    frames = []
    def transform(inst, r, d, b):
        inst = change_staffing(inst, retrain=r, add=d)
        return stagger_breaks(inst) if b == "staggered" else inst

    for n, (rt, ad, br) in enumerate(a.cells, 1):
        print(f"=== 조건 {n}/{len(a.cells)}: 가사 인력 교육 {rt}명 · 동행 인력 추가 {ad}명 · 휴게 {br} ===")
        df = run_experiment(
            a.data_dir, load_factors=[a.rho], seeds=a.seeds, dates=dates,
            time_limit_sec=a.time_limit,
            transform=lambda inst, r=rt, d=ad, b=br: transform(inst, r, d, b),
            progress=lambda msg: print("  " + msg),
        )
        df.insert(0, "breaks", br)
        df.insert(0, "add", ad)
        df.insert(0, "retrain", rt)
        frames.append(df)

    out = pd.concat(frames, ignore_index=True)
    out_dir = Path("data/experiments")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"staffing-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    out.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"\n저장: {path} ({len(out)}행) · 위반 합계 {int(out.violations.sum())}")

    g = out.groupby(["breaks", "retrain", "add", "algorithm"]).agg(
        실제부하=("actual_rho", "mean"), 배정=("assigned", "mean"), 동행배정=("assigned_escort", "mean"),
        동행요청=("requests_escort", "mean"), 고유인원=("unique_beneficiaries", "mean"),
        이동=("travel_minutes", "mean"), 활용률=("utilization", "mean"), 위반=("violations", "sum"))
    print(g.round(2).to_string())


if __name__ == "__main__":
    main()
