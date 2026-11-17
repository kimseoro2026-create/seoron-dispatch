"""30일 취소·지연 재생 실험 (rho=0.9, 시드 42/7/13, 기준선 2 + 재배정 2방식).

    python -m tools.run_replay30
    python -m tools.run_replay30 --escort-mode transit --select-escort car   # 같은 요청, 동행만 대중교통

결과: data/experiments/30day-<시각>.csv (화면의 "취소·지연이 생겼을 때" 표가 읽는다)
"""
import argparse
from datetime import datetime
from pathlib import Path
from simulator.experiment import run_experiment, summarize

ap = argparse.ArgumentParser()
ap.add_argument("--escort-mode", choices=["transit", "car"], default=None,
                help="병원동행 집↔병원 이동 (기본: config/region.yaml)")
ap.add_argument("--select-escort", choices=["transit", "car"], default=None,
                help="부하율로 요청을 고를 때 쓰는 이동 가정 (기본: --escort-mode 와 같음)")
a = ap.parse_args()

out = Path("data/experiments")
out.mkdir(parents=True, exist_ok=True)
stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

df = run_experiment(
    "data/processed",
    algorithms=("request_order", "nearest", "request_order_replay", "nearest_replay"),
    load_factors=(0.9,),
    seeds=(42, 7, 13),
    escort=a.escort_mode,
    select_escort=a.select_escort,
)
p = out / f"30day-{stamp}.csv"
df.to_csv(p, index=False, encoding="utf-8-sig")
print("saved:", p, "rows:", len(df))
print("violations 합계:", int(df["violations"].sum()))

for alg in ("request_order_replay", "nearest_replay"):
    sub = df[df["algorithm"] == alg]
    if sub.empty:
        continue
    print(f"\n{alg}  (n={len(sub)}일)")
    for col in ("cancelled_with_assignment", "refilled", "wasted_travel_min",
                "delays", "delay_kept", "delay_lost", "delay_moved"):
        s = sub[col].dropna()
        print(f"  {col:26s} 합계={s.sum():7.0f}  평균={s.mean():6.2f}  >0인날={int((s>0).sum()):3d}")
