"""M2 수치표 생성(tools.m2_summary): 숫자는 실험 CSV 에서만 계산한다."""
import pandas as pd

from tools import m2_summary as m


def _rows(job_algos, seeds=(42, 43), dates=("2026-11-02", "2026-11-03"), rho=0.9, bonus=None):
    out = []
    for algo in job_algos:
        for s in seeds:
            for i, d in enumerate(dates):
                u = 30 + (2 if algo == "optimize" else 1 if algo == "nearest" else 0) + (bonus or 0)
                out.append({"algorithm": algo, "service_date": d, "seed": s, "target_rho": rho, "requests": 50,
                            "assigned": u, "on_time_rate": u / 50, "unique_beneficiaries": u, "travel_minutes": 200 - u,
                            "violations": 0, "status": "feasible" if algo == "optimize" else "ok",
                            "cancellations": 3, "cancelled_with_assignment": 2, "refilled": 1, "wasted_travel_min": 10})
    return pd.DataFrame(out)


def test_summary_from_files(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "EXP", tmp_path)
    monkeypatch.setattr(m, "OUT", tmp_path / "numbers_m2.md")
    base3 = ["request_order", "nearest", "optimize"]
    _rows(base3).to_csv(tmp_path / "lab-m2-main_r0.9-20261114-100000.csv", index=False)
    _rows(base3).to_csv(tmp_path / "lab-m2-sens_base-20261114-100000.csv", index=False)
    _rows(base3, bonus=-3).to_csv(tmp_path / "lab-m2-sens_travel1.3-20261114-100000.csv", index=False)
    _rows(["request_order_cancel", "request_order_replay"]).to_csv(tmp_path / "lab-m2-sens_cancel0.1-20261114-100000.csv",
                                                                   index=False)
    m.main()
    text = (tmp_path / "numbers_m2.md").read_text(encoding="utf-8")
    assert "| 0.9 | AI 최적화 | 32.0 (32.0~32.0)" in text
    assert "AI 최적화 − 근거리 | +1.00 | +1.00~+1.00 | 4 / 0 / 0 (n=4)" in text
    assert "| 이동 ×1.3 | 27.0 | 28.0 | 29.0 |" in text
    assert "| 10% | 신청순+취소 재배정 | 30.0 | 3.0 | 2.0 | 1.0 | 10 | 0 |" in text
    assert "하드 제약 위반 합계 **0**" in text
