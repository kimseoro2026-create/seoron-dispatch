"""제출 문서용 수치표를 실험 CSV에서 계산해 docs/submission/numbers.md 로 쓴다.

제출 문서(1쪽 요약·시연 대본)의 숫자는 모두 이 파일에서 옮긴다.
손으로 숫자를 만들지 않는다(CLAUDE.md 절대원칙 1).

    python -m tools.submission_numbers
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
EXP = ROOT / "data" / "experiments"
OUT = ROOT / "docs" / "submission" / "numbers.md"

ALGO = {"request_order": "신청순", "nearest": "근거리", "optimize": "최적화(OR-Tools)",
        "request_order_replay": "신청순+재배정", "nearest_replay": "근거리+재배정"}


def _latest(pattern: str) -> Path:
    files = sorted(EXP.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit(f"실험 파일이 없습니다: {pattern}")
    return files[0]


def main() -> None:
    full_p = _latest("full-*.csv")
    rep_p = _latest("30day-*.csv")
    full = pd.read_csv(full_p)
    rep = pd.read_csv(rep_p)
    summary = json.loads((ROOT / "data" / "processed" / "summary.json").read_text(encoding="utf-8"))
    hosp = pd.read_csv(ROOT / "data" / "processed" / "hospitals.csv")
    hosp_mock = hosp["hospital_id"].astype(str).str.startswith("MOCK").any()

    L: list[str] = []
    L.append("# 제출용 수치표 (자동 생성 - 직접 고치지 말 것)\n")
    L.append(f"- 3방식 비교: `{full_p.name}` ({len(full)}행)")
    L.append(f"- 취소·지연 재생: `{rep_p.name}` ({len(rep)}행)")
    L.append(f"- 권역: 대상자 {summary['beneficiaries']} · 인력 {summary['workers']} · "
             f"병원 {len(hosp)}곳({'모의' if hosp_mock else '심평원 API'}) · "
             f"{summary['service_days']}일 · 요청 {summary['requests']:,}건 "
             f"(하루 평균 {summary['requests_per_day_avg']})")
    L.append(f"- 시드: {sorted(full['seed'].unique().tolist())} · "
             f"하드 제약 위반 합계: {int(full['violations'].sum())} (3방식) / "
             f"{int(pd.to_numeric(rep['violations'], errors='coerce').sum())} (재생)\n")

    # ── 3방식 비교 ──
    g = full.groupby(["target_rho", "algorithm"]).agg(
        rho=("actual_rho", "mean"), req=("requests", "mean"), assigned=("assigned", "mean"),
        rate=("on_time_rate", "mean"), rate_sd=("on_time_rate", "std"),
        uniq=("unique_beneficiaries", "mean"), travel=("travel_minutes", "mean"),
        util=("utilization", "mean"), solve=("solve_seconds", "mean"), n=("assigned", "size"))
    L.append("## 1. 하루 평균 (방식 × 부하율, 각 30일 × 3시드)\n")
    L.append("| 목표 ρ | 실제 ρ | 방식 | 요청 | 적기 배정 | 적기율 | 고유 인원 | 총 이동(분) | 활용률 | 계산(초) |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for (rho, alg), r in g.iterrows():
        L.append(f"| {rho} | {r.rho:.2f} | {ALGO[alg]} | {r.req:.1f} | {r.assigned:.1f} | "
                 f"{r.rate*100:.1f}% ± {r.rate_sd*100:.1f} | {r.uniq:.1f} | {r.travel:.0f} | "
                 f"{r.util*100:.1f}% | {r.solve:.1f} |")

    L.append("\n## 2. 신청순 대비 변화\n")
    L.append("| 목표 ρ | 방식 | 적기 배정(배) | 적기율(%p) | 고유 인원(명/일) | 총 이동 |")
    L.append("|---|---|---|---|---|---|")
    for rho in sorted(full["target_rho"].unique()):
        b = g.loc[(rho, "request_order")]
        for alg in ("nearest", "optimize"):
            x = g.loc[(rho, alg)]
            L.append(f"| {rho} | {ALGO[alg]} | {x.assigned/b.assigned:.2f}배 | "
                     f"{(x.rate-b.rate)*100:+.1f} | {x.uniq-b.uniq:+.1f} ({(x.uniq/b.uniq-1)*100:+.1f}%) | "
                     f"{(x.travel/b.travel-1)*100:+.1f}% |")

    p = full.pivot_table(index=["service_date", "seed", "target_rho"],
                         columns="algorithm", values="assigned")
    L.append("\n## 3. 날짜별 승패 (적기 배정 건수, 270관측)\n")
    L.append(f"- 근거리 > 신청순: {(p.nearest > p.request_order).sum()} / "
             f"근거리 < 신청순: {(p.nearest < p.request_order).sum()} / "
             f"같음: {(p.nearest == p.request_order).sum()}")
    L.append(f"- 최적화 > 근거리: {(p.optimize > p.nearest).sum()} / "
             f"최적화 < 근거리: {(p.optimize < p.nearest).sum()} / "
             f"같음: {(p.optimize == p.nearest).sum()}")
    st = full[full["algorithm"] == "optimize"]["status"].value_counts().to_dict()
    note = ("" if "optimal" not in st else
            " (주의: 11/10 수정 전 코드는 OR-Tools SUCCESS 를 'optimal'로 기록했다. "
            "실제 의미는 '제한시간 안에 찾은 해(feasible)'이며 최적 증명이 아니다.)")
    L.append(f"- 최적화 상태 기록: {st}{note}")

    # ── 재생 ──
    num = rep.copy()
    cols = ["on_time_rate", "cancelled_with_assignment", "refilled", "wasted_travel_min",
            "delays", "delay_kept", "delay_lost", "delay_moved", "delay_late_avg", "delay_late_max"]
    for c in cols:
        if c in num.columns:
            num[c] = pd.to_numeric(num[c], errors="coerce")
    num = num[num["algorithm"].isin(["request_order_replay", "nearest_replay"])]
    L.append("\n## 4. 취소·지연 재생 (ρ=0.9, 30일 × 3시드, 합계)\n")
    L.append("| 방식 | 적기율(평균) | 배정있던 취소 | 빈자리 재배정 | 헛걸음 이동(분) | 지연 통보 | 지연 후 지킴 | 지연으로 놓침 | 다른 인력 이동 | 가장 늦어진(분) |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for alg, s in num.groupby("algorithm"):
        L.append(f"| {ALGO[alg]} | {s.on_time_rate.mean()*100:.1f}% | {int(s.cancelled_with_assignment.sum())} | "
                 f"{int(s.refilled.sum())} | {int(s.wasted_travel_min.sum())} | {int(s.delays.sum())} | "
                 f"{int(s.delay_kept.sum())} | {int(s.delay_lost.sum())} | "
                 f"{int(s.delay_moved.sum()) if 'delay_moved' in s else '-'} | {int(s.delay_late_max.max())} |")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("saved:", OUT.relative_to(ROOT))


if __name__ == "__main__":
    main()
