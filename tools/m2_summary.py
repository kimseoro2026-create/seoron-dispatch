"""M2 효과 증명 수치표 — 시드 10개 기본 실험과 민감도 실험(tools/run_m2_experiments.py) 결과를 계산해
docs/submission/numbers_m2.md 로 쓴다. 숫자는 모두 실험 CSV 에서 계산한다(손으로 만들지 않음).

    python -m tools.m2_summary
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
EXP = ROOT / "data" / "experiments"
OUT = ROOT / "docs" / "submission" / "numbers_m2.md"
ALGO = {"request_order": "신청순", "nearest": "근거리", "optimize": "AI 최적화",
        "request_order_replay": "신청순+취소 재배정", "nearest_replay": "근거리+취소 재배정",
        "request_order_cancel": "신청순+취소 반영", "nearest_cancel": "근거리+취소 반영"}


def latest(job: str) -> Path | None:
    files = sorted(EXP.glob(f"lab-m2-{job}-*.csv"), key=lambda p: p.name)
    files = [f for f in files if not f.name.endswith("-assumptions.json")]
    return files[-1] if files else None


def load(job: str) -> pd.DataFrame | None:
    p = latest(job)
    return None if p is None else pd.read_csv(p, encoding="utf-8-sig").assign(_file=p.name)


def escort_label(jobs: list[str]) -> str:
    """결과 파일의 escort_mode 열로 병원동행 이동 가정을 밝힌다. 열이 없으면 2026-11-16 이전 실행 = 자동차."""
    modes = set()
    for j in jobs:
        d = load(j)
        if d is not None:
            modes |= set(d["escort_mode"].astype(str)) if "escort_mode" in d else {"car_old"}
    words = {"car_old": "자동차(OSRM) — 대중교통 반영 전(2026-11-16 이전) 실행",
             "car": "자동차(OSRM)", "transit": "대중교통(ODsay)"}
    return " / ".join(words.get(m, m) for m in sorted(modes)) or "결과 없음"


def failed(d: pd.DataFrame) -> pd.Series:
    return (d["violations"] > 0) | (~d["status"].astype(str).isin(["ok", "optimal", "feasible"])) | \
           ((d["assigned"] == 0) & (d["requests"] > 0))


def per_seed(d: pd.DataFrame) -> pd.DataFrame:
    """시드마다 하루 평균(고유 인원·적기율·이동)."""
    return d.groupby(["algorithm", "seed"]).agg(uniq=("unique_beneficiaries", "mean"),
                                                rate=("on_time_rate", "mean"),
                                                travel=("travel_minutes", "mean")).reset_index()


def paired(d: pd.DataFrame, a: str, b: str, col: str = "unique_beneficiaries") -> dict:
    """같은 날·같은 시드끼리 a−b 차이: 평균, 시드별 평균의 최소·최대, 날짜별 이김/짐/같음."""
    x = d[d["algorithm"] == a].set_index(["service_date", "seed"])[col]
    y = d[d["algorithm"] == b].set_index(["service_date", "seed"])[col]
    diff = (x - y).dropna()
    by_seed = diff.groupby(level="seed").mean()
    return {"mean": diff.mean(), "seed_min": by_seed.min(), "seed_max": by_seed.max(),
            "win": int((diff > 0).sum()), "lose": int((diff < 0).sum()), "tie": int((diff == 0).sum()), "n": len(diff)}


SCEN = {"car": "자동차 가정", "transit": "대중교통 기본", "fixed": "수요 고정"}


def escort_scenarios() -> list[str]:
    """병원동행 집↔병원 이동 가정 두 시나리오(docs/travel_modes_plan.md 4장)."""
    L = ["## 4. 이동 가정 — 병원동행 집↔병원을 자동차로 보느냐, 대중교통으로 보느냐\n",
         "- 바꾸는 것은 병원동행의 집→병원·병원→집 이동뿐이다. 인력이 다음 집으로 가는 이동은 세 시나리오 모두 자동차(OSRM)다.",
         "- 대중교통: ODsay 대중교통 길찾기(출발 시각 기준 없음), 700m 이내는 OSRM 도보 거리 ÷ 시속 3km(가정값).",
         "- '대중교통 기본'은 부하율도 대중교통 시간으로 잰다. 같은 ρ라도 동행 1건이 무거워 요청 수가 적다.",
         "- '수요 고정'은 자동차 기준 ρ로 고른 같은 요청을 대중교통 시간으로 배정한다(같은 요청에서 이동 가정만의 영향).\n"]
    data = {(sc, r): load(f"{sc}_r{r}") for sc in SCEN for r in ("0.6", "0.9", "1.1")}
    have = {k: d for k, d in data.items() if d is not None}
    if not have:
        return L + ["(결과 없음 — `python -m tools.run_m2_experiments car_r0.9 transit_r0.9 fixed_r0.9` 등으로 만든다)\n"]
    allm = pd.concat(have.values())
    L.append("- 파일: " + ", ".join(f"`{d['_file'].iloc[0]}`" for d in have.values()))
    L.append(f"- 30일 × 시드 10개(42~51), AI 10초 · 하드 제약 위반 합계 **{int(allm['violations'].sum())}** · "
             f"실패 **{int(failed(allm).sum())}** · "
             f"AI 계산 상태: {allm[allm['algorithm']=='optimize']['status'].value_counts().to_dict()}\n")
    L.append("| 부하율 | 시나리오 | 요청/일 | 실제 ρ | 방식 | 고유 인원/일 | 적기율 | 동행 배정/요청 | 이동(분/일) |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in ("0.6", "0.9", "1.1"):
        for sc in SCEN:
            d = have.get((sc, r))
            if d is None:
                continue
            for algo in ("request_order", "nearest", "optimize"):
                s = d[d["algorithm"] == algo]
                L.append(f"| {r} | {SCEN[sc]} | {s['requests'].mean():.1f} | {s['actual_rho'].mean():.2f} | {ALGO[algo]} | "
                         f"{s['unique_beneficiaries'].mean():.1f} | {s['on_time_rate'].mean()*100:.1f}% | "
                         f"{s['assigned_escort'].mean():.1f} / {s['requests_escort'].mean():.1f} | "
                         f"{s['travel_minutes'].mean():.0f} |")
    L.append("\n### 시나리오마다 방식 차이 (같은 날·같은 시드, 고유 인원 명/일)\n")
    L.append("| 부하율 | 시나리오 | AI − 근거리 (이김/짐/같음) | AI − 신청순 (이김/짐/같음) | 근거리 − 신청순 |")
    L.append("|---|---|---|---|---|")
    for r in ("0.6", "0.9", "1.1"):
        for sc in SCEN:
            d = have.get((sc, r))
            if d is None:
                continue
            a = paired(d, "optimize", "nearest")
            b = paired(d, "optimize", "request_order")
            c = paired(d, "nearest", "request_order")
            L.append(f"| {r} | {SCEN[sc]} | {a['mean']:+.2f} ({a['win']}/{a['lose']}/{a['tie']}) | "
                     f"{b['mean']:+.2f} ({b['win']}/{b['lose']}/{b['tie']}) | {c['mean']:+.2f} |")
    L.append("\n### 같은 요청에서 이동 가정만 바꾸면 (수요 고정 − 자동차 가정, 같은 날·같은 시드 평균)\n")
    L.append("| 부하율 | 고유 인원: 신청순 | 근거리 | AI 최적화 | 동행 배정 건수(AI) |")
    L.append("|---|---|---|---|---|")
    for r in ("0.6", "0.9", "1.1"):
        car, fx = have.get(("car", r)), have.get(("fixed", r))
        if car is None or fx is None:
            continue
        both = pd.concat([car.assign(_sc="car"), fx.assign(_sc="fixed")])

        def diff(algo: str, col: str) -> float:
            x = both[both["algorithm"] == algo].pivot_table(index=["service_date", "seed"], columns="_sc", values=col)
            return (x["fixed"] - x["car"]).mean()

        L.append(f"| {r} | {diff('request_order', 'unique_beneficiaries'):+.2f} | "
                 f"{diff('nearest', 'unique_beneficiaries'):+.2f} | {diff('optimize', 'unique_beneficiaries'):+.2f} | "
                 f"{diff('optimize', 'assigned_escort'):+.2f} |")
    L.append("\n- 이 절은 이동 '가정'의 영향이다. 대중교통 시간은 시각별 배차를 반영하지 않으며, 도보 속도 3km/h는 가정값이다.\n")
    return L


WK = {"0": "인력 모두 자차", "50": "인력 절반 대중교통", "100": "인력 모두 대중교통"}


def worker_scenarios() -> list[str]:
    """인력별 이동수단 시나리오(docs/travel_modes_plan.md 5단계)."""
    L = ["\n## 5. 인력 이동수단 — 인력이 다음 집으로 대중교통을 타고 가면\n",
         "- 병원동행 집↔병원은 세 시나리오 모두 대중교통이다. 요청은 자동차 기준 ρ로 골라 4절 '수요 고정'과 같다.",
         "- 대중교통 인력의 이동은 보정 모형 값(estimate): min(a × 자동차분 + b, 도보) + 여유(학습 오차 80% 분위수).",
         "  모형 계수·오차는 `data/processed/worker_transit_model.json`. 실제 자차/대중교통 비율은 확인되지 않아 시나리오로 나눴다.",
         "- 대중교통 인력은 자격 묶음(동행 가능 / 가사만)마다 같은 비율로, 인력 ID 순서를 고정해 골랐다.\n"]
    have = {(k, r): d for k in WK for r in ("0.6", "0.9", "1.1")
            if (d := load(f"wk{k}_r{r}")) is not None}
    if not have:
        return L + ["(결과 없음 — `python -m tools.run_m2_experiments wk0_r0.9 wk50_r0.9 wk100_r0.9`)\n"]
    allm = pd.concat(have.values())
    L.append("- 파일: " + ", ".join(f"`{d['_file'].iloc[0]}`" for d in have.values()))
    L.append(f"- 30일 × 시드 10개, AI 10초 · 하드 제약 위반 합계 **{int(allm['violations'].sum())}** · "
             f"실패 **{int(failed(allm).sum())}** · "
             f"AI 계산 상태: {allm[allm['algorithm']=='optimize']['status'].value_counts().to_dict()}\n")
    L.append("| 부하율 | 시나리오 | 요청/일 | 방식 | 고유 인원/일 | 적기율 | 동행 배정/요청 | 이동(분/일) |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in ("0.6", "0.9", "1.1"):
        for k in WK:
            d = have.get((k, r))
            if d is None:
                continue
            for algo in ("request_order", "nearest", "optimize"):
                x = d[d["algorithm"] == algo]
                L.append(f"| {r} | {WK[k]} | {x['requests'].mean():.1f} | {ALGO[algo]} | "
                         f"{x['unique_beneficiaries'].mean():.1f} | {x['on_time_rate'].mean()*100:.1f}% | "
                         f"{x['assigned_escort'].mean():.1f} / {x['requests_escort'].mean():.1f} | "
                         f"{x['travel_minutes'].mean():.0f} |")
    L.append("\n### 시나리오마다 방식 차이 (같은 날·같은 시드, 고유 인원 명/일)\n")
    L.append("| 부하율 | 시나리오 | AI − 근거리 (이김/짐/같음) | AI − 신청순 (이김/짐/같음) |")
    L.append("|---|---|---|---|")
    for r in ("0.6", "0.9", "1.1"):
        for k in WK:
            d = have.get((k, r))
            if d is None:
                continue
            a_, b_ = paired(d, "optimize", "nearest"), paired(d, "optimize", "request_order")
            L.append(f"| {r} | {WK[k]} | {a_['mean']:+.2f} ({a_['win']}/{a_['lose']}/{a_['tie']}) | "
                     f"{b_['mean']:+.2f} ({b_['win']}/{b_['lose']}/{b_['tie']}) |")
    L.append("\n### 모두 자차와 비교 (같은 날·같은 시드 평균, 고유 인원 명/일)\n")
    L.append("| 부하율 | 시나리오 | 신청순 | 근거리 | AI 최적화 |")
    L.append("|---|---|---|---|---|")
    for r in ("0.6", "0.9", "1.1"):
        base = have.get(("0", r))
        for k in ("50", "100"):
            d = have.get((k, r))
            if base is None or d is None:
                continue
            both = pd.concat([base.assign(_k="0"), d.assign(_k=k)])

            def diff(algo: str) -> float:
                x = both[both["algorithm"] == algo].pivot_table(index=["service_date", "seed"], columns="_k",
                                                                values="unique_beneficiaries")
                return (x[k] - x["0"]).mean()

            L.append(f"| {r} | {WK[k]} | {diff('request_order'):+.2f} | {diff('nearest'):+.2f} | {diff('optimize'):+.2f} |")
    L.append("\n- 모형 값은 실제 조회값이 아니다. 확정 후보안은 `python -m adapters.fetch worker-plan` 으로 연결 구간만 실제 조회해 다시 검증한다.\n")
    return L


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    L = ["# M2 효과 증명 수치표 (자동 생성 - 직접 고치지 말 것)\n",
         "> 가상 데이터 기반 실험 결과다. 실제 기관 효과가 아니다. `python -m tools.m2_summary` 로 다시 만든다.\n"]
    L.append("> **병원동행 이동 가정:** 1~3절은 " + escort_label(["main_r0.9", "sens_base", "sens_cancel0.1"]) +
             ". 자동차와 대중교통을 나란히 비교한 결과는 4절에 있다.\n")

    # ── 1. 기본 실험 ─────────────────────────────────────────────────────
    mains = {r: load(f"main_r{r}") for r in ("0.6", "0.9", "1.1")}
    have = {r: d for r, d in mains.items() if d is not None}
    L.append("## 1. 기본 실험 — 30일 × 시드 10개(42~51), AI 계산 제한 10초\n")
    if not have:
        L.append("(결과 없음)\n")
    else:
        files = ", ".join(f"`{d['_file'].iloc[0]}`" for d in have.values())
        allm = pd.concat(have.values())
        L.append(f"- 파일: {files}")
        L.append(f"- 관측: 방식마다 {allm.groupby(['target_rho','service_date','seed']).ngroups}개(날짜×시드×부하율) · "
                 f"하드 제약 위반 합계 **{int(allm['violations'].sum())}** · 실패(위반·계산 실패·배정 0) "
                 f"**{int(failed(allm).sum())}**")
        L.append(f"- AI 계산 상태: {allm[allm['algorithm']=='optimize']['status'].value_counts().to_dict()}\n")
        L.append("| 부하율 | 방식 | 고유 인원/일 (시드 평균, 최소~최대) | 적기율 | 이동(분/일) |")
        L.append("|---|---|---|---|---|")
        for r, d in have.items():
            ps = per_seed(d)
            for algo in ("request_order", "nearest", "optimize"):
                s = ps[ps["algorithm"] == algo]
                if s.empty:
                    continue
                L.append(f"| {r} | {ALGO[algo]} | {s['uniq'].mean():.1f} ({s['uniq'].min():.1f}~{s['uniq'].max():.1f}) | "
                         f"{s['rate'].mean()*100:.1f}% | {s['travel'].mean():.0f} |")
        L.append("\n### 같은 날·같은 시드끼리 비교 (고유 인원 차이, 명/일)\n")
        L.append("| 부하율 | 비교 | 평균 차이 | 시드별 평균 최소~최대 | 날짜별 이김 / 짐 / 같음 |")
        L.append("|---|---|---|---|---|")
        for r, d in have.items():
            for a, b in (("nearest", "request_order"), ("optimize", "request_order"), ("optimize", "nearest")):
                p = paired(d, a, b)
                L.append(f"| {r} | {ALGO[a]} − {ALGO[b]} | {p['mean']:+.2f} | {p['seed_min']:+.2f}~{p['seed_max']:+.2f} | "
                         f"{p['win']} / {p['lose']} / {p['tie']} (n={p['n']}) |")
        L.append("")

    # ── 2. 민감도(이동·체류) ─────────────────────────────────────────────
    L.append("## 2. 민감도 — 부하율 0.9에서 한 요인씩 (30일 × 시드 10개, AI 5초)\n")
    base = load("sens_base")
    sens = {"이동 ×0.8": load("sens_travel0.8"), "기준(×1.0)": base, "이동 ×1.3": load("sens_travel1.3"),
            "병원 체류 ×0.8": load("sens_stay0.8"), "병원 체류 ×1.3": load("sens_stay1.3")}
    rows = [(k, d) for k, d in sens.items() if d is not None]
    if not rows:
        L.append("(결과 없음)\n")
    else:
        L.append("| 조건 | 신청순 고유/일 | 근거리 고유/일 | AI 고유/일 | AI − 근거리 (날짜별 이김/짐/같음) | AI − 신청순 | 위반 |")
        L.append("|---|---|---|---|---|---|---|")
        for k, d in rows:
            u = d.groupby("algorithm")["unique_beneficiaries"].mean()
            p1, p2 = paired(d, "optimize", "nearest"), paired(d, "optimize", "request_order")
            L.append(f"| {k} | {u.get('request_order', float('nan')):.1f} | {u.get('nearest', float('nan')):.1f} | "
                     f"{u.get('optimize', float('nan')):.1f} | {p1['mean']:+.2f} ({p1['win']}/{p1['lose']}/{p1['tie']}) | "
                     f"{p2['mean']:+.2f} | {int(d['violations'].sum())} |")
        L.append("\n- 파일: " + ", ".join(f"`{d['_file'].iloc[0]}`" for _, d in rows))
        L.append("- 이동·체류 배율은 실험 가정이다(실측 아님). 같은 5초 기준(기준 ×1.0)끼리 비교한다.\n")

    # ── 3. 취소율 ────────────────────────────────────────────────────────
    L.append("## 3. 취소율 — 부하율 0.9, 취소 통보 시각에만 반영(미래 정보 금지)\n")
    cans = [(r, load(f"sens_cancel{r}")) for r in ("0.0", "0.1", "0.2")]
    cans = [(r, d) for r, d in cans if d is not None]
    if not cans:
        L.append("(결과 없음)\n")
    else:
        L.append("| 취소율 | 방식 | 고유 인원/일 | 취소/일 | 배정 있던 취소/일 | 빈자리 다시 채움/일 | 헛걸음 이동(분/일) | 위반 |")
        L.append("|---|---|---|---|---|---|---|---|")
        for r, d in cans:
            for algo in ("request_order_cancel", "request_order_replay", "nearest_cancel", "nearest_replay"):
                s = d[d["algorithm"] == algo]
                if s.empty:
                    continue
                num = lambda c: pd.to_numeric(s[c], errors="coerce").mean() if c in s else float("nan")
                L.append(f"| {int(float(r)*100)}% | {ALGO[algo]} | {s['unique_beneficiaries'].mean():.1f} | "
                         f"{num('cancellations'):.1f} | {num('cancelled_with_assignment'):.1f} | {num('refilled'):.1f} | "
                         f"{num('wasted_travel_min'):.0f} | {int(s['violations'].sum())} |")
        L.append("\n- 파일: " + ", ".join(f"`{d['_file'].iloc[0]}`" for _, d in cans))
        L.append("- 취소율을 정하면 통보 시각은 30·60·120·240분 전 중 하나로 정했다(가정). '취소 반영'은 빈자리를 채우지 않고,"
                 " '취소 재배정'은 통보 시각 이후 빈자리를 다시 채운다.\n")

    L += escort_scenarios()
    L += worker_scenarios()
    OUT.write_text("\n".join(L), encoding="utf-8")
    print(f"썼습니다: {OUT}")


if __name__ == "__main__":
    main()
