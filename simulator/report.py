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
