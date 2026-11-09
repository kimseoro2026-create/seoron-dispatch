"""이미 저장된 실험 원본 CSV 로 요약만 다시 만든다.

원본 1080행은 남아 있는데 요약 단계에서 오류가 났을 때, 실험을 30분 더 돌리지 않고
요약만 복구하기 위한 도구다.

사용:
    python -m tools.resummarize data/experiments/cancel30-20261109-131309.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from simulator.experiment import save, summarize  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print("사용: python -m tools.resummarize <원본CSV경로> [태그]")
        raise SystemExit(1)

    src = Path(sys.argv[1])
    if not src.is_absolute():
        src = ROOT / src
    if not src.exists():
        print(f"파일이 없습니다: {src}")
        raise SystemExit(1)

    tag = sys.argv[2] if len(sys.argv) > 2 else src.stem.split("-")[0]

    df = pd.read_csv(src)
    print(f"원본: {src.name} ({len(df)}행)")
    print(f"방식: {sorted(df['algorithm'].unique())}")
    print()

    summ = summarize(df)
    path = save(summ, ROOT / "data" / "experiments", tag=f"summary-{tag}")
    print(f"요약 저장: {path}")
    print()

    with pd.option_context("display.width", 250, "display.max_columns", 40):
        print(summ.to_string(index=False))

    viol = int(df["violations"].sum())
    print()
    if viol:
        print(f"경고: 위반 {viol}건. 해당 배정안은 사용할 수 없습니다.")
    else:
        print("위반 0건.")


if __name__ == "__main__":
    main()
