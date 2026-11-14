"""M2 효과 증명 실험 묶음(PRD 12장): 시드 10개(42~51) 기본 실험과 한 요인씩 바꾼 민감도 실험.

사용: python -m tools.run_m2_experiments <작업 이름>   (작업 이름은 JOBS 참고, 병렬로 여러 개 띄울 수 있다)
결과: data/experiments/lab-m2-<작업>-<시각>.csv + -assumptions.json (simulator.lab.run_lab)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEEDS = list(range(42, 52))
BASE3 = ["request_order", "nearest", "optimize"]
CANCEL4 = ["request_order_replay", "nearest_replay", "request_order_cancel", "nearest_cancel"]

JOBS = {
    # 기본: 부하율 3단계 × 시드 10 × 30일, AI 10초
    "main_r0.6": dict(rhos=[0.6], algorithms=BASE3, time_limit_sec=10),
    "main_r0.9": dict(rhos=[0.9], algorithms=BASE3, time_limit_sec=10),
    "main_r1.1": dict(rhos=[1.1], algorithms=BASE3, time_limit_sec=10),
    # 민감도(기준 ρ0.9에서 한 요인씩), AI 5초 — 같은 5초 기준(sens_base)과 비교한다
    "sens_base": dict(rhos=[0.9], algorithms=BASE3, time_limit_sec=5),
    "sens_travel0.8": dict(rhos=[0.9], algorithms=BASE3, time_limit_sec=5, travel_factor=0.8),
    "sens_travel1.3": dict(rhos=[0.9], algorithms=BASE3, time_limit_sec=5, travel_factor=1.3),
    "sens_stay0.8": dict(rhos=[0.9], algorithms=BASE3, time_limit_sec=5, stay_factor=0.8),
    "sens_stay1.3": dict(rhos=[0.9], algorithms=BASE3, time_limit_sec=5, stay_factor=1.3),
    # 취소율: 재배정·반영 방식(기준선 기반이라 빠름)
    "sens_cancel0.0": dict(rhos=[0.9], algorithms=CANCEL4, time_limit_sec=5, cancel_rate=0.0),
    "sens_cancel0.1": dict(rhos=[0.9], algorithms=CANCEL4, time_limit_sec=5, cancel_rate=0.1),
    "sens_cancel0.2": dict(rhos=[0.9], algorithms=CANCEL4, time_limit_sec=5, cancel_rate=0.2),
}


def main(argv: list[str]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    from simulator.lab import LabConfig, run_lab
    for name in argv:
        spec = JOBS[name]
        cfg = LabConfig(n_days=30, seeds=SEEDS, label=f"m2-{name}", **spec)
        total = 30 * len(SEEDS) * len(cfg.rhos) * len(cfg.algorithms)
        done = [0]

        def progress(msg: str) -> None:
            done[0] += 1
            if done[0] % 20 == 0 or done[0] == total:
                print(f"[{name}] {done[0]}/{total} {msg}", flush=True)

        out = run_lab(cfg, ROOT / "data" / "processed", out_dir=ROOT / "data" / "experiments", progress=progress)
        raw = out["raw"]
        print(f"[{name}] 저장 {out['raw_path'].name} 행 {len(raw)} 위반 {int(raw['violations'].sum())}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
