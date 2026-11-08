"""위반 재현 진단 — 2026-11-18 최적화 결과의 구체적 위반 코드를 출력한다."""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from planner.data import load_day, load_travel
from planner.optimize import optimize
from verifier import verify


def main() -> None:
    data = ROOT / "data" / "processed"
    tr = load_travel(data)

    for date in ["2026-11-18", "2026-11-06", "2026-11-20"]:
        print(f"\n{'='*60}")
        print(f"날짜: {date}")
        try:
            inst = load_day(data, date, travel=tr).as_instance()
        except Exception as e:
            print(f"  로드 실패: {e}")
            continue

        print(f"요청 {len(inst['requests'])}건, 인력 {len(inst['workers'])}명")

        for seed in [42, 43, 44]:
            r = optimize(inst, time_limit_sec=10, seed=seed)
            rep = verify(inst, r.assignments)
            if rep.violations:
                print(f"\n  seed={seed} → 배정 {len(r.assignments)}, 위반 {len(rep.violations)}건")
                for v in rep.violations[:5]:
                    print(f"    {json.dumps(v.as_dict(), ensure_ascii=False)}")


if __name__ == "__main__":
    main()
