"""시간창 폭 실험 — 동행 픽업창 폭 × 가사 시간창 추가 폭.

같은 날·같은 시드·같은 요청에서 시간창만 바꿔 세 방식을 비교한다(짝지은 비교).
결과: data/experiments/window-YYYYMMDD-HHMMSS.csv (열 escort_window, household_extra 추가)

실행: python -m tools.run_window_experiment                       # 기본 6조건, ρ 0.9, 30일 × 3시드
      python -m tools.run_window_experiment --days 3 --seeds 42     # 빠른 확인
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

DEFAULT_ESCORT = [20, 60, 120]      # 20 = f0 기본 픽업창(예약 30~10분 전 도착)
DEFAULT_HOUSEHOLD = [0, 60]         # 0 = f0 기본(희망 시각 ±60분)


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data/processed")
    ap.add_argument("--escort", type=int, nargs="+", default=DEFAULT_ESCORT, help="동행 픽업창 폭(분)")
    ap.add_argument("--household", type=int, nargs="+", default=DEFAULT_HOUSEHOLD, help="가사 시간창 추가 폭(분)")
    ap.add_argument("--rho", type=float, default=0.9)
    ap.add_argument("--seeds", type=int, nargs="+", default=[42, 43, 44])
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--time-limit", type=int, default=10)
    a = ap.parse_args(argv)

    from planner.data import service_dates
    from simulator.experiment import run_experiment, widen_windows

    dates = service_dates(a.data_dir)
    dates = dates[: a.days] if a.days else dates
    cells = [(e, h) for e in a.escort for h in a.household]
    frames = []
    for n, (esc, hh) in enumerate(cells, 1):
        print(f"=== 조건 {n}/{len(cells)}: 동행 픽업창 {esc}분 · 가사 +{hh}분 ===")
        # 20분은 생성된 창 그대로 쓴다(재계산하지 않아 기존 실험과 같은 입력이 된다)
        width = None if esc == 20 else esc
        df = run_experiment(
            a.data_dir, load_factors=[a.rho], seeds=a.seeds, dates=dates,
            time_limit_sec=a.time_limit,
            transform=lambda inst, w=width, h=hh: widen_windows(inst, escort_width=w, household_extra=h),
            progress=lambda msg: print("  " + msg),
        )
        df.insert(0, "household_extra", hh)
        df.insert(0, "escort_window", esc)
        frames.append(df)

    out = pd.concat(frames, ignore_index=True)
    out_dir = Path("data/experiments")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"window-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    out.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"\n저장: {path} ({len(out)}행) · 위반 합계 {int(out.violations.sum())}")

    g = out.groupby(["escort_window", "household_extra", "algorithm"]).agg(
        배정=("assigned", "mean"), 동행배정=("assigned_escort", "mean"), 동행요청=("requests_escort", "mean"),
        고유인원=("unique_beneficiaries", "mean"), 이동=("travel_minutes", "mean"), 위반=("violations", "sum"))
    print(g.round(2).to_string())


if __name__ == "__main__":
    main()
