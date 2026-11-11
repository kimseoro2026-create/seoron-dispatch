"""30일 취소·지연 재생 실험 (rho=0.9, 시드 42/7/13, 기준선 2 + 재배정 2방식).

    python -m tools.run_replay30

결과: data/experiments/30day-<시각>.csv (화면의 "취소·지연이 생겼을 때" 표가 읽는다)
"""
from datetime import datetime
from pathlib import Path
from simulator.experiment import run_experiment, summarize

out = Path("data/experiments")
out.mkdir(parents=True, exist_ok=True)
stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

df = run_experiment(
    "data/processed",
    algorithms=("request_order", "nearest", "request_order_replay", "nearest_replay"),
    load_factors=(0.9,),
    seeds=(42, 7, 13),
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
