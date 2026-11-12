"""부족 자원·비용 리포트를 최신 shortage 실험 결과에서 계산해 쓴다(자동 생성, 손으로 숫자를 만들지 않음).

    python -m tools.shortage_report
결과: docs/reports/shortage_report.md, docs/reports/shortage_scenarios.csv, docs/reports/shortage_unmet.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
NA = "계산 불가"
ALGO_KO = {"optimize": "최적화", "nearest": "근거리"}

SERVICE = {"hospital_escort": "병원동행", "household_support": "가사지원"}
REASON = {
    "TIME_WINDOW_CONFLICT": "시간창 충돌", "NO_CAPACITY": "인력 여유 없음", "NO_SKILL_MATCH": "자격 맞는 인력 없음",
    "SOLVER_UNRESOLVED": "계산 중 미해결", "UNKNOWN": "사유 미상",
}
IDLE = {
    "CAPACITY": "빈 일정이면 가능 → 인력 여유 부족", "BREAK": "빈 일정이어도 불가 → 휴게 시각과 충돌",
    "WINDOW": "휴게를 빼도 불가 → 시간창·근무시간 불일치", "NO_SKILL": "자격 맞는 인력 없음", "ROUTE": "이동시간 없음",
}
CERT = {"verified": "확인됨", "diagnostic": "진단", "unknown": "미상"}


def latest_pair(exp: Path) -> tuple[Path, Path]:
    days = sorted((p for p in exp.glob("shortage-*.csv") if "outcomes" not in p.name), key=lambda p: p.name)
    if not days:
        raise SystemExit(f"shortage 실험 결과가 없습니다: {exp}")
    d = days[-1]
    o = exp / d.name.replace("shortage-", "shortage-outcomes-").replace(".csv", ".csv.gz")
    if not o.exists():
        raise SystemExit(f"요청별 결과 파일이 없습니다: {o.name}")
    return d, o


def build(exp: Path, data_dir: Path) -> dict:
    """리포트에 들어가는 표를 계산해 돌려준다(화면에서도 같은 함수를 쓴다)."""
    from planner.data import load_day, service_dates
    from simulator.report import SCENARIOS, added_labor_cost_per_day, load_cost_config, scenario_table, unmet_breakdown

    d_path, o_path = latest_pair(exp)
    days = pd.read_csv(d_path)
    outcomes = pd.read_csv(o_path)
    cost = load_cost_config(ROOT)
    inst = load_day(data_dir, service_dates(data_dir)[0]).as_instance()
    extra = {s.key: added_labor_cost_per_day(inst, s, cost.get("hourly_krw"), cost["paid_time"]) for s in SCENARIOS}
    tables = {a: scenario_table(days, extra, algorithm=a) for a in sorted(days["algorithm"].unique())}
    unmet_algo = "optimize" if "optimize" in set(outcomes["algorithm"]) else "nearest"
    unmet = {by: unmet_breakdown(outcomes, by, algorithm=unmet_algo)
             for by in ("service_type", "start_hour", "priority", "area", "reason", "idle_check")}
    return dict(unmet_algo=unmet_algo, days_path=d_path, outcomes_path=o_path, days=days, cost=cost, tables=tables, unmet=unmet,
                seeds=sorted(days["seed"].unique().tolist()), n_dates=days["service_date"].nunique(),
                rho=sorted(days["target_rho"].unique().tolist()))


def _won(x) -> str:
    return NA if x is None or pd.isna(x) else f"{x:,.0f}원"


def to_markdown(r: dict) -> str:
    c = r["cost"]
    L = ["# 부족 자원·비용 리포트 (자동 생성 - 직접 고치지 말 것)\n",
         "> **가상 입력 기반 추정**이다. 모든 숫자는 합성 데이터 시뮬레이션 결과다. 실제 기관 효과가 아니다.\n",
         f"- 실험: `{r['days_path'].name}` ({len(r['days'])}행) · 요청별 결과 `{r['outcomes_path'].name}`",
         f"- 조건: 부하율 ρ {r['rho']} · {r['n_dates']}일 × 시드 {r['seeds']} · 같은 요청 집합에서 자원만 바꿔 다시 계산",
         f"- 하드 제약 위반 합계: {int(r['days']['violations'].sum())}",
         f"- 비용 가정: 시급 {_won(c.get('hourly_krw'))}(가정) · 유급시간 `{c['paid_time']}` · "
         f"월급 모델 {_won(c.get('monthly_salary_krw'))} · 이동 1분당 교통비 {_won(c.get('transport_krw_per_min'))}",
         "- 추가 인력은 기존 동행 인력의 근무시간·휴게·출발지를 복제해 다시 배정했다(부족 시간을 인원으로 환산하지 않음).",
         "- 휴게 분산은 유급시간이 같아 추가 인건비 0으로 계산했다. 근무 규정상 가능한지는 확인하지 않았다.\n"]
    for algo, title in (("optimize", "최적화"), ("nearest", "근거리(참고)")):
        if algo not in r["tables"]:
            continue
        t = r["tables"][algo]
        L.append(f"## {'1' if algo == 'optimize' else '2'}. 시나리오 비교 - {title} (하루 평균)\n")
        L.append("| 시나리오 | 적기 배정 | 고유 인원 | 전체 충족 인원 | 고우선 미충족률 | 동행 배정/요청 | 총 이동(분) | 활용률 | 고유 인원 변화 | 추가 인건비/일 | 추가 1명당/일 |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for _, x in t.iterrows():
            L.append(f"| {x.label} | {x.assigned:.1f} | {x.uniq:.1f} | {x.fully_met:.1f} | {x.high_unmet_rate*100:.1f}% | "
                     f"{x.escort:.1f}/{x.requests_escort:.1f} | {x.travel:.0f} | {x.utilization*100:.1f}% | "
                     f"{x.d_unique:+.1f} | {_won(x.extra_cost_per_day)} | {_won(x.cost_per_added_person)} |")
        L.append("")
    u = r["unmet"]
    L.append(f"## 3. 현재 조건({ALGO_KO.get(r['unmet_algo'], r['unmet_algo'])})에서 미배정이 생기는 곳\n")
    L.append("### 서비스별\n\n| 서비스 | 요청/일 | 미배정/일 | 미충족률 |\n|---|---|---|---|")
    for _, x in u["service_type"].iterrows():
        L.append(f"| {SERVICE.get(x.service_type, x.service_type)} | {x.requests_per_day:.1f} | {x.unmet_per_day:.1f} | {x.unmet_rate*100:.1f}% |")
    L.append("\n### 우선순위별 (2 이상 = 퇴원 14일 이내 등 높은 우선순위)\n\n| 우선순위 | 요청/일 | 미배정/일 | 미충족률 |\n|---|---|---|---|")
    for _, x in u["priority"].iterrows():
        L.append(f"| {int(x.priority)} | {x.requests_per_day:.1f} | {x.unmet_per_day:.1f} | {x.unmet_rate*100:.1f}% |")
    L.append("\n### 희망 시작 시간대별\n\n| 시작 시각 | 요청/일 | 미배정/일 | 미충족률 |\n|---|---|---|---|")
    for _, x in u["start_hour"].iterrows():
        L.append(f"| {int(x.start_hour)}시 | {x.requests_per_day:.1f} | {x.unmet_per_day:.1f} | {x.unmet_rate*100:.1f}% |")
    L.append("\n### 권역(가상 행정동)별\n\n| 권역 | 요청/일 | 미배정/일 | 미충족률 |\n|---|---|---|---|")
    for _, x in u["area"].iterrows():
        L.append(f"| {x.area} | {x.requests_per_day:.1f} | {x.unmet_per_day:.1f} | {x.unmet_rate*100:.1f}% |")
    L.append("\n### 미배정 사유\n\n| 사유 | 확실성 | 미배정/일 | 비중 |\n|---|---|---|---|")
    for _, x in u["reason"].iterrows():
        L.append(f"| {REASON.get(x.reason, x.reason)} | {CERT.get(x.certainty, x.certainty)} | {x.unmet_per_day:.1f} | {x.share*100:.1f}% |")
    L.append("\n### 미배정 진단: 빈 일정의 자격 인력이면 맡을 수 있었나\n")
    L.append("엔진 사유 코드와 별개인 진단용 분류다. '인력 여유 부족'은 인력·배치를 늘리면 줄 수 있고, "
             "'휴게 충돌'은 휴게 운영으로, '시간창 불일치'는 요청 조건 조정으로만 줄 수 있다.\n")
    L.append("| 진단 | 서비스 | 미배정/일 | 비중 |\n|---|---|---|---|")
    for _, x in u["idle_check"].iterrows():
        L.append(f"| {IDLE.get(x.idle_check, x.idle_check)} | {SERVICE.get(x.service_type, x.service_type)} | "
                 f"{x.unmet_per_day:.1f} | {x.share*100:.1f}% |")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp-dir", default=str(ROOT / "data" / "experiments"))
    ap.add_argument("--data-dir", default=str(ROOT / "data" / "processed"))
    ap.add_argument("--out-dir", default=str(ROOT / "docs" / "reports"))
    a = ap.parse_args(argv)
    r = build(Path(a.exp_dir), Path(a.data_dir))
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "shortage_report.md").write_text(to_markdown(r), encoding="utf-8")
    sc = pd.concat([t.assign(algorithm=k) for k, t in r["tables"].items()]).assign(
        source=r["days_path"].name, paid_time=r["cost"]["paid_time"], hourly_krw=r["cost"].get("hourly_krw"))
    sc.to_csv(out / "shortage_scenarios.csv", encoding="utf-8-sig")
    pd.concat([t.assign(by=k) for k, t in r["unmet"].items()], ignore_index=True).assign(
        source=r["outcomes_path"].name).to_csv(
        out / "shortage_unmet.csv", index=False, encoding="utf-8-sig")
    print("saved:", out / "shortage_report.md")


if __name__ == "__main__":
    main()
