"""재배정 결과 상세 확인 — 위반이 남았는지, 어떤 코드인지 출력한다."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from planner.data import load_day, load_travel, service_dates  # noqa: E402
from planner.reschedule import replay  # noqa: E402


def main() -> None:
    data = ROOT / "data" / "processed"
    travel = load_travel(data)
    dates = service_dates(data)

    # 취소가 있는 날을 우선 확인하기 위해 앞쪽 여러 날을 돌린다
    for date in dates[:5]:
        inst = load_day(data, date, travel=travel).as_instance()
        for algo in ("request_order", "nearest"):
            res = replay(inst, algo)
            s = res.summary()
            status = "OK" if s["violations"] == 0 else f"위반 {s['violations']}"
            print(f"[{date}] {algo}_replay: 최초 {s['initial_assigned']} → "
                  f"최종 {s['final_assigned']} | 취소 {s['cancellations']} "
                  f"(배정있던 것 {s['cancelled_with_assignment']}) | "
                  f"채움 {s['refilled']} | 빈슬롯 {s['left_empty']} | "
                  f"시각변경 {s['retimed']} | 밀려남 {s['displaced']} | {status}")
            for v in res.violations[:3]:
                print(f"    {v['code']} {v['request_id']} {v['worker_id']}: {v['detail'][:70]}")
        print()


if __name__ == "__main__":
    main()
