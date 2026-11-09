"""1회성 진단: 낭비이동 비대칭 원인 + moved_to_worker 발동 집계."""
import csv, statistics
from collections import defaultdict

SRC = "data/experiments/30day-20261109-231245.csv"
rows = list(csv.DictReader(open(SRC, encoding="utf-8-sig")))

def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

# 1) 방식별 낭비이동 / 지연 요약
print("=== 방식별 요약 (replay 행만) ===")
agg = defaultdict(lambda: defaultdict(list))
for r in rows:
    if not r["algorithm"].endswith("_replay"):
        continue
    for k in ("cancelled_with_assignment", "wasted_travel_min", "refilled",
              "delay_lost", "delay_late_avg", "delay_late_max"):
        v = num(r[k])
        if v is not None:
            agg[r["algorithm"]][k].append(v)

for alg in sorted(agg):
    a = agg[alg]
    print(f"\n{alg}")
    print(f"  일수              : {len(a['wasted_travel_min'])}")
    print(f"  배정된 취소 합계   : {sum(a['cancelled_with_assignment']):.0f}")
    print(f"  낭비이동 합계(분)  : {sum(a['wasted_travel_min']):.0f}")
    print(f"  낭비이동>0 인 일수 : {sum(1 for v in a['wasted_travel_min'] if v > 0)}")
    print(f"  재배정 합계        : {sum(a['refilled']):.0f}")
    print(f"  지연유실 합계      : {sum(a['delay_lost']):.0f}")

print("\n=== 낭비이동>0 인 행 전체 ===")
for r in rows:
    if (num(r["wasted_travel_min"]) or 0) > 0:
        print(f"{r['service_date']} {r['seed']} {r['algorithm']:24s} "
              f"wasted={r['wasted_travel_min']:>4s} cancelled_assign={r['cancelled_with_assignment']}")
