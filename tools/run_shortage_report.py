"""부족 자원·비용 리포트용 실험 — 같은 요청에서 휴게 운영·인력만 바꿔 다시 계산한다.

결과:
  data/experiments/shortage-YYYYMMDD-HHMMSS.csv            하루 결과(열 scenario 추가)
  data/experiments/shortage-outcomes-YYYYMMDD-HHMMSS.csv.gz 요청별 결과(배정 여부·사유·권역 등)

실행: python -m tools.run_shortage_report                    # 6시나리오, ρ0.9, 30일 × 3시드
      python -m tools.run_shortage_report --days 1 --seeds 42  # 빠른 확인
리포트 작성: python -m tools.shortage_report
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    from simulator.report import BY_KEY, SCENARIOS

    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data/processed")
    ap.add_argument("--scenarios", nargs="+", default=[s.key for s in SCENARIOS], choices=list(BY_KEY))
    ap.add_argument("--algorithms", nargs="+", default=["nearest", "optimize"])
    ap.add_argument("--rho", type=float, default=0.9)
    ap.add_argument("--seeds", type=int, nargs="+", default=[42, 43, 44])
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--time-limit", type=int, default=10)
    ap.add_argument("--out-dir", default="data/experiments")
    a = ap.parse_args(argv)

    from planner.data import service_dates
    from simulator.experiment import run_experiment

    dates = service_dates(a.data_dir)
    dates = dates[: a.days] if a.days else dates
    frames, outcomes = [], []
    for n, key in enumerate(a.scenarios, 1):
        sc = BY_KEY[key]
        print(f"=== 시나리오 {n}/{len(a.scenarios)}: {sc.label} ===")
        sink: list = []
        df = run_experiment(a.data_dir, algorithms=a.algorithms, load_factors=[a.rho], seeds=a.seeds,
                            dates=dates, time_limit_sec=a.time_limit, transform=sc.apply,
                            progress=lambda msg: print("  " + msg), outcome_sink=sink)
        df.insert(0, "scenario", key)
        frames.append(df)
        outcomes.extend(dict(o, scenario=key) for o in sink)

    days = pd.concat(frames, ignore_index=True)
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    p1 = out_dir / f"shortage-{stamp}.csv"
    p2 = out_dir / f"shortage-outcomes-{stamp}.csv.gz"
    days.to_csv(p1, index=False, encoding="utf-8-sig")
    pd.DataFrame(outcomes).to_csv(p2, index=False, encoding="utf-8-sig", compression="gzip")
    print(f"\n저장: {p1} ({len(days)}행) · {p2} ({len(outcomes)}행) · 위반 합계 {int(days.violations.sum())}")
    g = days.groupby(["scenario", "algorithm"]).agg(
        배정=("assigned", "mean"), 고유인원=("unique_beneficiaries", "mean"),
        전체충족=("fully_met_beneficiaries", "mean"), 고우선배정=("assigned_high", "mean"),
        고우선요청=("requests_high", "mean"), 이동=("travel_minutes", "mean"), 위반=("violations", "sum"))
    print(g.round(2).to_string())


if __name__ == "__main__":
    main()
