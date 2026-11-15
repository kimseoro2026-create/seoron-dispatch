"""부족 자원·비용 리포트 (PRD W10, 13장).

같은 요청 집합에서 자원(휴게 운영·인력)만 바꿔 다시 계산한 결과로
1) 어디서 미배정이 생기는지(서비스·시간대·우선순위·권역·사유)
2) 자원을 바꾸면 적기 지원 인원이 얼마나 늘고 비용이 얼마나 드는지
를 집계한다. 숫자는 모두 시뮬레이터 결과에서 계산한다. 단가가 없으면 금액은 None(계산 불가).

부족 시간을 '인력 N명 부족'으로 자동 환산하지 않는다(PRD W10 검수). 추가 인력은 근무패턴·이동을 적용해 재계산한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from simulator.experiment import change_staffing, stagger_breaks


@dataclass(frozen=True)
class Scenario:
    key: str
    label: str
    add: int = 0            # 동행 가능 인력 추가(기존 동행 인력의 근무패턴 복제)
    staggered: bool = False  # 휴게 시작을 11·12·13·14시로 나눔
    extend_end: int = 0      # 특정 시간대 근무 확대: 근무 끝을 이만큼(분) 늦춤(FR-42). 하루 최대 근무도 같이 늘림
    extend_scope: str = "escort"   # escort(동행 가능 인력) / all(모든 인력)

    def apply(self, instance: dict) -> dict:
        out = change_staffing(instance, add=self.add) if self.add else instance
        out = stagger_breaks(out) if self.staggered else out
        return extend_shifts(out, self.extend_end, self.extend_scope) if self.extend_end else out


def extend_shifts(instance: dict, minutes: int, scope: str = "escort") -> dict:
    """근무 끝을 minutes 만큼 늦춘 새 instance(시간대 근무 확대). 하루 최대 근무도 같은 만큼 늘린다.
    24시를 넘기지 않는다. 원래 instance 는 바꾸지 않는다."""
    if scope not in ("escort", "all"):
        raise ValueError(f"알 수 없는 범위: {scope}")
    workers = []
    for w in instance["workers"]:
        w = dict(w)
        if scope == "all" or "hospital_escort" in (w.get("skills") or []):
            new_end = min(24 * 60, int(w["shift_end_min"]) + int(minutes))
            added = new_end - int(w["shift_end_min"])
            w["shift_end_min"] = new_end
            if w.get("max_work_minutes"):
                w["max_work_minutes"] = int(w["max_work_minutes"]) + added
        workers.append(w)
    return dict(instance, workers=workers)


SCENARIOS = [
    Scenario("base", "현재(휴게 12시 동일)"),
    Scenario("add1", "동행 인력 +1", add=1),
    Scenario("add2", "동행 인력 +2", add=2),
    Scenario("stagger", "휴게 분산", staggered=True),
    Scenario("stagger_add1", "휴게 분산 + 동행 인력 +1", add=1, staggered=True),
    Scenario("stagger_add2", "휴게 분산 + 동행 인력 +2", add=2, staggered=True),
]
BY_KEY = {s.key: s for s in SCENARIOS}


def paid_minutes(worker: dict, paid_time: str = "shift_minus_break") -> int:
    """인력 1명의 하루 유급시간(분). 가정값 paid_time 에 따라 계산한다."""
    shift = int(worker["shift_end_min"]) - int(worker["shift_start_min"])
    if paid_time == "shift":
        return shift
    if paid_time == "shift_minus_break":
        return shift - int(worker.get("break_minutes") or 0)
    raise ValueError(f"알 수 없는 유급시간 가정: {paid_time}")


def added_paid_minutes(instance: dict, scenario: Scenario, paid_time: str = "shift_minus_break") -> int:
    """시나리오가 늘리는 하루 유급시간(분) = (바뀐 인력 전체 유급시간) − (원래 유급시간).
    추가 인력의 유급시간과 근무 연장분을 함께 센다. 휴게 분산은 0."""
    after = scenario.apply(instance)["workers"]
    return (sum(paid_minutes(w, paid_time) for w in after)
            - sum(paid_minutes(w, paid_time) for w in instance["workers"]))


def added_workers(instance: dict, scenario: Scenario) -> list[dict]:
    """시나리오가 추가하는 인력(기존 인력에 없는 worker_id)."""
    before = {w["worker_id"] for w in instance["workers"]}
    return [w for w in scenario.apply(instance)["workers"] if w["worker_id"] not in before]


def added_labor_cost_per_day(instance: dict, scenario: Scenario, hourly_krw: float | None,
                             paid_time: str = "shift_minus_break") -> float | None:
    """시간급 모델에서 시나리오가 하루에 더 드는 인건비. 시급이 없으면 None.
    휴게 분산은 유급시간이 같으므로 0이다(운영 가능성은 별도 확인)."""
    if hourly_krw is None:
        return None
    return added_paid_minutes(instance, scenario, paid_time) / 60 * float(hourly_krw)


def scenario_table(days: pd.DataFrame, extra_cost: dict[str, float | None],
                   algorithm: str = "optimize") -> pd.DataFrame:
    """시나리오별 하루 평균과 '현재' 대비 변화, 추가 적기 지원 1명당 추가 비용.

    days: 시나리오 열(scenario)이 붙은 run_experiment 결과.
    extra_cost: 시나리오별 하루 추가 인건비(None = 계산 불가).
    """
    d = days[days["algorithm"] == algorithm]
    g = d.groupby("scenario").agg(
        days=("assigned", "size"), assigned=("assigned", "mean"),
        uniq=("unique_beneficiaries", "mean"), fully_met=("fully_met_beneficiaries", "mean"),
        assigned_high=("assigned_high", "mean"), requests_high=("requests_high", "mean"),
        escort=("assigned_escort", "mean"), requests_escort=("requests_escort", "mean"),
        requests=("requests", "mean"), travel=("travel_minutes", "mean"),
        utilization=("utilization", "mean"), violations=("violations", "sum"))
    order = [s.key for s in SCENARIOS if s.key in g.index]
    g = g.loc[order]
    base = g.loc["base"] if "base" in g.index else None
    g.insert(0, "label", [BY_KEY[k].label for k in g.index])
    g["high_unmet_rate"] = 1 - g["assigned_high"] / g["requests_high"].where(g["requests_high"] > 0)
    if base is not None:
        g["d_unique"] = g["uniq"] - base["uniq"]
        g["d_assigned"] = g["assigned"] - base["assigned"]
    g["extra_cost_per_day"] = [extra_cost.get(k) for k in g.index]

    def per_person(row):
        c, du = row["extra_cost_per_day"], row.get("d_unique")
        if c is None or pd.isna(c) or du is None or pd.isna(du) or du <= 0:
            return None      # 단가 없음 또는 분모 0 이하 → N/A
        return c / du
    g["cost_per_added_person"] = g.apply(per_person, axis=1)
    return g


def unmet_breakdown(outcomes: pd.DataFrame, by: str, scenario: str = "base",
                    algorithm: str = "optimize") -> pd.DataFrame:
    """한 시나리오·방식의 미배정을 by(service_type / start_hour / priority / area / reason) 로 집계.
    하루 평균 요청·미배정과 미충족률."""
    o = outcomes[(outcomes["scenario"] == scenario) & (outcomes["algorithm"] == algorithm)]
    n_days = o.groupby(["service_date", "seed"]).ngroups or 1
    if by == "idle_check":
        u = o[~o["assigned"]]
        t = u.groupby(["idle_check", "service_type"]).size().rename("unmet").reset_index()
        t["unmet_per_day"] = t["unmet"] / n_days
        t["share"] = t["unmet"] / max(len(u), 1)
        return t.sort_values("unmet", ascending=False).reset_index(drop=True)
    if by == "reason":
        u = o[~o["assigned"]]
        t = u.groupby(["reason", "certainty"]).size().rename("unmet").reset_index()
        t["unmet_per_day"] = t["unmet"] / n_days
        t["share"] = t["unmet"] / max(len(u), 1)
        return t.sort_values("unmet", ascending=False).reset_index(drop=True)
    t = o.groupby(by).agg(requests=("assigned", "size"), assigned=("assigned", "sum")).reset_index()
    t["unmet"] = t["requests"] - t["assigned"]
    t["requests_per_day"] = t["requests"] / n_days
    t["unmet_per_day"] = t["unmet"] / n_days
    t["unmet_rate"] = t["unmet"] / t["requests"]
    return t


def load_cost_config(root: Path) -> dict:
    import yaml
    cfg = yaml.safe_load((root / "config" / "region.yaml").read_text(encoding="utf-8"))
    cost = dict(cfg.get("cost") or {})
    cost["hourly_krw"] = (cfg.get("simulation") or {}).get("hourly_cost_krw")
    cost.setdefault("paid_time", "shift_minus_break")
    return cost


def custom_compare(data_dir: str | Path, scenario: Scenario, *, n_days: int = 3, algorithm: str = "nearest",
                   seeds: list[int] | None = None, rho: float = 0.9, time_limit_sec: int = 5,
                   hourly_krw: float | None = None, paid_time: str = "shift_minus_break") -> dict:
    """담당자가 고른 조건(FR-42)으로 같은 요청을 다시 계산해 '현재'와 나란히 비교한다.

    → {table, raw}. table: 시나리오별 하루 평균(고유 인원·배정·동행·고우선 미충족률·이동·위반)과 변화, 추가 인건비·1명당 비용.
    """
    from planner.data import load_day, service_dates
    from simulator.experiment import run_experiment

    d = Path(data_dir)
    dates = service_dates(d)[: max(1, int(n_days))]
    seeds = seeds or [42]
    frames = []
    for sc in (BY_KEY["base"], scenario):
        df = run_experiment(d, algorithms=[algorithm], load_factors=[rho], seeds=seeds, dates=dates,
                            time_limit_sec=time_limit_sec, transform=sc.apply if sc.key != "base" else None)
        frames.append(df.assign(scenario=sc.key))
    raw = pd.concat(frames, ignore_index=True)
    inst = load_day(d, dates[0]).as_instance()
    g = raw.groupby("scenario").agg(
        uniq=("unique_beneficiaries", "mean"), assigned=("assigned", "mean"), escort=("assigned_escort", "mean"),
        requests_escort=("requests_escort", "mean"), assigned_high=("assigned_high", "mean"),
        requests_high=("requests_high", "mean"), travel=("travel_minutes", "mean"), violations=("violations", "sum"))
    g = g.loc[["base", scenario.key]]
    g.insert(0, "label", [BY_KEY["base"].label, scenario.label])
    g["high_unmet_rate"] = 1 - g["assigned_high"] / g["requests_high"].where(g["requests_high"] > 0)
    g["d_unique"] = g["uniq"] - g.loc["base", "uniq"]
    mins = {"base": 0, scenario.key: added_paid_minutes(inst, scenario, paid_time)}
    g["added_paid_min"] = [mins[k] for k in g.index]
    g["extra_cost_per_day"] = [None if hourly_krw is None else m / 60 * float(hourly_krw) for m in g["added_paid_min"]]
    g["cost_per_added_person"] = [None if (c is None or du <= 0) else c / du
                                  for c, du in zip(g["extra_cost_per_day"], g["d_unique"])]
    return {"table": g, "raw": raw, "dates": dates, "seeds": seeds, "rho": rho, "algorithm": algorithm}


# ── 행정동별 미충족률 × 가까운 인력까지 거리 (PRD 6장: 가까운 경증 대상자만 반복 배정되는지 탐지) ──
def nearest_worker_minutes(data_dir: str | Path) -> dict:
    """행정동마다 '어르신 집에서 가장 가까운 인력 출발지까지 자동차 분'의 중앙값.

    인력 출발지 → 어르신 집 방향 구간을 쓴다(배정 엔진이 출발할 때와 같은 방향). 구간이 없는 어르신은
    0분으로 채우지 않고 빼며 no_route 로 센다. 행렬이 없으면 추정 행렬(estimate)을 쓰고 source 에 남긴다.
    → {by_area: DataFrame[area, near_worker_min, beneficiaries, no_route], source}
    """
    from planner.data import BENEFICIARY_PREFIX, WORKER_PREFIX, load_travel, matrix_path
    d = Path(data_dir)
    ben = pd.read_csv(d / "beneficiaries.csv", usecols=["beneficiary_id", "dong"], dtype=str, encoding="utf-8-sig")
    wk = pd.read_csv(d / "workers.csv", usecols=["worker_id"], dtype=str, encoding="utf-8-sig")
    bases = [f"{WORKER_PREFIX}{w}" for w in wk["worker_id"]]
    homes = {f"{BENEFICIARY_PREFIX}{b}": b for b in ben["beneficiary_id"]}
    mp = matrix_path(d)
    source = "estimate"
    if mp is not None:
        try:
            source = str(pd.read_csv(mp, nrows=1)["source"].iloc[0])
        except Exception:
            source = "unknown"
    travel = load_travel(d, set(bases) | set(homes))
    near = {}
    for node, bid in homes.items():
        mins = [travel[(w, node)] for w in bases if (w, node) in travel]
        near[bid] = min(mins) if mins else None
    ben["near"] = ben["beneficiary_id"].map(near)
    g = ben.groupby("dong")
    t = pd.DataFrame({"near_worker_min": g["near"].median(), "beneficiaries": g.size(),
                      "no_route": g["near"].apply(lambda s: int(s.isna().sum()))}).reset_index()
    return {"by_area": t.rename(columns={"dong": "area"}), "source": source}


def rank_corr(x, y) -> float | None:
    """순위 상관(스피어만, 동률은 평균 순위). 빈 값 쌍은 빼고, 3곳 미만이거나 한쪽이 모두 같으면 None."""
    s = pd.DataFrame({"x": list(x), "y": list(y)}).apply(pd.to_numeric, errors="coerce").dropna()
    if len(s) < 3 or s["x"].nunique() < 2 or s["y"].nunique() < 2:
        return None
    return float(s["x"].rank().corr(s["y"].rank()))


def partial_rank_corr(x, y, z) -> float | None:
    """z(요청량)를 고정했을 때 x(거리)와 y(미충족률)의 순위 편상관.

    세 값이 모두 있는 행정동만 쓴다. 3곳 미만이거나, 어느 한 쌍의 순위가 완전히 같이 움직여(|r|=1)
    둘을 나눌 수 없으면 None.
    """
    s = pd.DataFrame({"x": list(x), "y": list(y), "z": list(z)}).apply(pd.to_numeric, errors="coerce").dropna()
    if len(s) < 3:
        return None
    rxy, rxz, ryz = rank_corr(s["x"], s["y"]), rank_corr(s["x"], s["z"]), rank_corr(s["y"], s["z"])
    if None in (rxy, rxz, ryz):
        return None
    den = (1 - rxz ** 2) * (1 - ryz ** 2)
    if den <= 1e-12:
        return None
    return float(max(-1.0, min(1.0, (rxy - rxz * ryz) / den ** 0.5)))


def area_distance(outcomes: pd.DataFrame, data_dir: str | Path, scenario: str = "base") -> dict:
    """방식별 행정동 미충족률과 가까운 인력까지 거리를 한 표로, 방식별 순위 상관을 함께.

    양(+)이면 인력에서 먼 동일수록 미충족이 많다는 뜻. 요청이 많은 동은 같은 시간대에 경쟁이 몰려 미충족이
    늘 수 있으므로, 동별 하루 요청 수(requests_per_day)와 미충족률의 상관(corr_volume), 요청 수를 고정한
    거리 편상관(partial), 거리를 고정한 요청 수 편상관(partial_volume), 거리와 요청 수의 상관(dist_volume)을
    함께 낸다. 행정동 수가 적어 참고용이다(원인 증명 아님).
    """
    acc = nearest_worker_minutes(data_dir)
    t = acc["by_area"][["area", "near_worker_min"]].copy()
    corr, corr_volume, partial, partial_volume = {}, {}, {}, {}
    algos = sorted(outcomes.loc[outcomes["scenario"] == scenario, "algorithm"].unique())
    for algo in algos:
        u = unmet_breakdown(outcomes, "area", scenario=scenario, algorithm=algo)
        if "requests_per_day" not in t.columns:     # 요청은 방식과 무관하게 같다 → 첫 방식에서 한 번만
            t = t.merge(u[["area", "requests_per_day"]], on="area", how="outer")
        t = t.merge(u[["area", "unmet_rate"]].rename(columns={"unmet_rate": f"unmet_rate_{algo}"}),
                    on="area", how="outer")
        y = t[f"unmet_rate_{algo}"]
        corr[algo] = rank_corr(t["near_worker_min"], y)
        corr_volume[algo] = rank_corr(t["requests_per_day"], y)
        partial[algo] = partial_rank_corr(t["near_worker_min"], y, t["requests_per_day"])
        partial_volume[algo] = partial_rank_corr(t["requests_per_day"], y, t["near_worker_min"])
    dist_volume = rank_corr(t["near_worker_min"], t["requests_per_day"]) if algos else None
    t = t.sort_values("near_worker_min", na_position="last").reset_index(drop=True)
    return {"table": t, "corr": corr, "corr_volume": corr_volume, "partial": partial, "partial_volume": partial_volume,
            "dist_volume": dist_volume,
            "n_areas": int(t["near_worker_min"].notna().sum()), "source": acc["source"]}


def corr_words(r: float | None) -> str:
    """담당자용 쉬운 말. 기준(|r|<0.3 뚜렷하지 않음, 0.3~0.6 약한, ≥0.6 뚜렷한)은 해석 편의용 관례다."""
    if r is None:
        return "계산할 수 없음"
    a = abs(r)
    if a < 0.3:
        return "뚜렷한 관계 없음"
    strength = "뚜렷하게" if a >= 0.6 else "약하게"
    return f"인력에서 먼 동일수록 미충족이 {strength} 많음" if r > 0 else f"인력에서 가까운 동일수록 미충족이 {strength} 많음"


def volume_words(r: float | None) -> str:
    """요청량과 미충족률의 순위 상관을 쉬운 말로."""
    if r is None:
        return "계산할 수 없음"
    a = abs(r)
    if a < 0.3:
        return "요청 수와 뚜렷한 관계 없음"
    strength = "뚜렷하게" if a >= 0.6 else "약하게"
    return f"요청이 많은 동일수록 미충족이 {strength} 많음" if r > 0 else f"요청이 적은 동일수록 미충족이 {strength} 많음"


def separation_words(p_dist: float | None, p_vol: float | None,
                     raw_dist: float | None, raw_vol: float | None) -> str:
    """거리·요청 수 가운데 하나를 같게 봤을 때 다른 하나의 관계가 남는지. 남는 쪽이 없으면 나눌 수 없다고 말한다
    (한쪽으로 단정하지 않음)."""
    if p_dist is None or p_vol is None:
        return "거리와 요청 수가 완전히 함께 움직여 나눠 볼 수 없음"
    d, v = abs(p_dist) >= 0.3, abs(p_vol) >= 0.3
    if d and not v:
        return "요청 수를 같게 봐도 거리 관계가 남음 → " + corr_words(p_dist)
    if v and not d:
        return "거리를 같게 보면 거리 관계는 사라지고 요청 수 관계가 남음 → " + volume_words(p_vol)
    if d and v:
        return "거리와 요청 수 모두 따로 관계가 남음"
    if (raw_dist is not None and abs(raw_dist) >= 0.3) or (raw_vol is not None and abs(raw_vol) >= 0.3):
        return "거리와 요청 수가 함께 움직여, 이 행정동 수로는 어느 쪽 때문인지 나눌 수 없음"
    return "거리·요청 수 모두 뚜렷한 관계 없음"


def area_corr_lines(ad: dict, algo_names: dict | None = None) -> list[str]:
    """area_distance 결과를 리포트·화면 공통 문장으로. 수치는 모두 계산값."""
    names = algo_names or {"request_order": "신청순", "nearest": "근거리", "optimize": "AI 최적화"}
    f = lambda v: "계산 불가" if v is None else f"{v:+.2f}"
    out = []
    dv = ad.get("dist_volume")
    out.append(f"거리와 요청 수의 순위 상관 {f(dv)}" + (" (먼 동일수록 요청도 많음)" if dv is not None and dv >= 0.6 else ""))
    for a in ad["corr"]:
        n = names.get(a, a)
        out.append(f"{n}: 거리 상관 {f(ad['corr'][a])}, 요청 수 상관 {f(ad['corr_volume'][a])} · "
                   f"요청 수를 같게 본 거리 {f(ad['partial'][a])}, 거리를 같게 본 요청 수 {f(ad['partial_volume'][a])} → "
                   + separation_words(ad["partial"][a], ad["partial_volume"][a], ad["corr"][a], ad["corr_volume"][a]))
    return out


# ── 이동 감소의 의미 (AT-15) ─────────────────────────────────────────────────
def travel_saving(minutes_saved: float, cost: dict) -> dict:
    """이동이 줄어든 만큼을 '돌봄에 쓸 수 있는 시간'으로 바꾼다. 인건비 절감으로 세지 않는다.

    인력은 근무시간 기준으로 급여를 받는다(시급×유급시간 또는 월급). 이동이 줄어도 근무시간은 그대로라
    인건비는 줄지 않는다(labor_saving_krw = 0). 교통비는 1분당 단가가 있을 때만 계산하고, 없으면 None(계산 불가).
    """
    m = max(0.0, float(minutes_saved))
    rate = cost.get("transport_krw_per_min")
    return {"freed_minutes": m, "labor_saving_krw": 0,
            "transport_saving_krw": None if rate is None else m * float(rate)}


def travel_saving_text(minutes_saved: float, cost: dict, versus: str) -> str | None:
    """담당자용 한 줄. 이동이 줄지 않았으면 None(아무 말도 하지 않는다)."""
    if minutes_saved <= 0:
        return None
    s = travel_saving(minutes_saved, cost)
    m = int(round(s["freed_minutes"]))
    tr = ("교통비 절감은 1분당 단가가 정해지지 않아 계산하지 않았습니다" if s["transport_saving_krw"] is None
          else f"교통비는 약 {s['transport_saving_krw']:,.0f}원 줄어듭니다(가정 단가)")
    return (f"{versus} 대비 인력 이동이 {m // 60}시간 {m % 60}분 줄었습니다. 이 시간은 다른 어르신을 돌볼 수 있는 여력입니다. "
            f"인력은 근무시간으로 급여를 받으므로 인건비는 줄지 않습니다. {tr}.")
