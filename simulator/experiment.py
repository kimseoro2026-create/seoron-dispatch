"""시뮬레이터 — 다일·다중 시드·부하율 실험 (PRD 12장, M2).

목적은 하나다: **같은 요청·인력·근무조건에서 세 방식이 무엇을 바꾸는가.**
하루 표본으로는 그 주장을 할 수 없으므로 30일·여러 시드·여러 부하율을 돌린다.

부하율 ρ (PRD 4.1)
  ρ = (요청의 필수 점유시간 합 + 예상 이동시간 합) ÷ (인력 가용 근무시간 합)
  - 필수 점유시간: 병원동행은 픽업~귀가 전체, 가사는 서비스 시간
  - 예상 이동시간: 요청마다 '자격 있는 인력의 기지 → 대상자 집' 최소 이동
  - 가용 근무시간: 인력별 (근무종료 - 근무시작 - 휴게)
  기본 실험: ρ = 0.6(여유) / 0.9(포화 직전) / 1.1(초과)

부하율을 맞추는 방법
  요청을 새로 지어내지 않는다. 생성된 요청 집합에서 목표 ρ에 해당하는 만큼만 골라 쓴다.
  ρ는 요청 수에 거의 비례하므로 목표/현재 비율만큼 무작위로 남긴다.
  목표가 현재보다 크면(예: 현재 1.08 인데 목표 1.1) 있는 요청을 모두 쓴다.
  부족분을 복제하면 같은 대상자에게 중복 요청이 생겨 '고유 인원' 지표가 왜곡된다.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
import pandas as pd

DEFAULT_LOAD_FACTORS = (0.6, 0.9, 1.1)
DEFAULT_SEEDS = (42, 43, 44)


# ── 부하율 ────────────────────────────────────────────────────────────────

def service_span(req: dict, travel: dict) -> int | None:
    """요청 하나의 필수 점유시간(분). 경로가 없으면 None."""
    if req["service_type"] != "hospital_escort":
        return int(req["service_minutes"])
    to_h = travel.get((req["home_node"], req["hospital_node"]))
    back = travel.get((req["hospital_node"], req["home_node"]))
    if to_h is None or back is None:
        return None
    return int(round(to_h)) + int(req["hospital_stay_min"]) + int(round(back))


def _min_pickup_travel(req: dict, workers: list[dict], travel: dict) -> int | None:
    """자격 있는 인력 기지에서 대상자 집까지 최소 이동시간(분). 없으면 None."""
    need = set(req.get("required_skills") or [])
    best = None
    for w in workers:
        if not need.issubset(set(w.get("skills") or [])):
            continue
        m = travel.get((w["base_node"], req["home_node"]))
        if m is None:
            continue
        m = float(m)
        if best is None or m < best:
            best = m
    return None if best is None else int(round(best))


def available_minutes(workers: list[dict]) -> int:
    """인력 가용 근무시간 합(분). 휴게는 빼고 센다."""
    return sum(max(0, int(w["shift_end_min"]) - int(w["shift_start_min"])
                   - int(w.get("break_minutes") or 0))
               for w in workers)


def load_factor(instance: dict) -> float:
    """ρ = (필수 점유 + 예상 이동) / 인력 가용시간."""
    travel = _travel_map(instance["travel"])
    workers = instance["workers"]
    cap = available_minutes(workers)
    if cap <= 0:
        return float("nan")
    need = 0
    for req in instance["requests"]:
        span = service_span(req, travel)
        if span is None:
            continue
        pickup = _min_pickup_travel(req, workers, travel)
        need += span + (pickup or 0)
    return need / cap


def _travel_map(travel) -> dict[tuple[str, str], float]:
    if isinstance(travel, dict):
        return {(a, b): float(m) for (a, b), m in travel.items()}
    return {(r["from_node"], r["to_node"]): float(r["minutes"]) for r in travel}


# ── 부하율 맞추기 ─────────────────────────────────────────────────────────

def select_requests(instance: dict, target_rho: float, seed: int) -> dict:
    """목표 부하율에 맞게 요청을 골라 새 instance 를 돌려준다.

    요청을 지어내지 않고 부분집합만 쓴다. 목표가 현재보다 크면 있는 것을 모두 쓴다.
    """
    current = load_factor(instance)
    if not np.isfinite(current) or current <= 0:
        return instance

    requests = instance["requests"]
    n = len(requests)
    ratio = target_rho / current
    if ratio >= 1.0:
        keep = n
    else:
        keep = max(1, int(round(n * ratio)))

    rng = np.random.default_rng(seed)
    idx = rng.choice(n, size=keep, replace=False)
    chosen = [requests[i] for i in sorted(idx)]

    out = dict(instance)
    out["requests"] = chosen
    return out


# ── 하루 실행 ─────────────────────────────────────────────────────────────

@dataclass
class DayResult:
    algorithm: str
    service_date: str
    seed: int
    target_rho: float
    actual_rho: float
    requests: int
    assigned: int
    unique_beneficiaries: int
    total_beneficiaries: int
    travel_minutes: int
    work_minutes: int
    available_minutes: int
    solve_seconds: float
    violations: int
    status: str
    reasons: dict = field(default_factory=dict)

    @property
    def on_time_rate(self) -> float:
        return self.assigned / self.requests if self.requests else 0.0

    @property
    def utilization(self) -> float:
        return self.work_minutes / self.available_minutes if self.available_minutes else 0.0

    def as_row(self) -> dict:
        return {
            "algorithm": self.algorithm,
            "service_date": self.service_date,
            "seed": self.seed,
            "target_rho": self.target_rho,
            "actual_rho": round(self.actual_rho, 3),
            "requests": self.requests,
            "assigned": self.assigned,
            "on_time_rate": round(self.on_time_rate, 4),
            "unique_beneficiaries": self.unique_beneficiaries,
            "total_beneficiaries": self.total_beneficiaries,
            "travel_minutes": self.travel_minutes,
            "utilization": round(self.utilization, 4),
            "solve_seconds": round(self.solve_seconds, 2),
            "violations": self.violations,
            "status": self.status,
        }


def run_day(instance: dict, algorithm: str, seed: int = 42,
            time_limit_sec: int = 30) -> DayResult:
    """하루치를 한 방식으로 돌리고 지표를 계산한다. 결과는 검증기로 다시 확인한다."""
    from planner.baseline import nearest, request_order
    from planner.optimize import optimize
    from verifier import verify

    if algorithm == "request_order":
        r = request_order(instance)
        assignments, unassigned = r.assignments, r.unassigned
        status, secs = "ok", 0.0
    elif algorithm == "nearest":
        r = nearest(instance)
        assignments, unassigned = r.assignments, r.unassigned
        status, secs = "ok", 0.0
    elif algorithm == "optimize":
        r = optimize(instance, time_limit_sec=time_limit_sec, seed=seed)
        assignments, unassigned = r.assignments, r.unassigned
        status, secs = r.status, r.solve_seconds
    else:
        raise ValueError(f"알 수 없는 방식: {algorithm}")

    rep = verify(instance, assignments)

    req_by_id = {x["request_id"]: x for x in instance["requests"]}
    served = {req_by_id[a["request_id"]]["beneficiary_id"] for a in assignments}
    everyone = {x["beneficiary_id"] for x in instance["requests"]}

    reasons: dict[str, int] = {}
    for u in unassigned:
        key = u["reason"] if isinstance(u, dict) else u.reason
        reasons[key] = reasons.get(key, 0) + 1

    return DayResult(
        algorithm=algorithm,
        service_date=str(instance.get("service_date", "")),
        seed=seed,
        target_rho=0.0,
        actual_rho=load_factor(instance),
        requests=len(instance["requests"]),
        assigned=len(assignments),
        unique_beneficiaries=len(served),
        total_beneficiaries=len(everyone),
        travel_minutes=int(sum(t.travel_min for t in rep.timelines)),
        work_minutes=int(sum(rep.work_minutes.values())),
        available_minutes=available_minutes(instance["workers"]),
        solve_seconds=secs,
        violations=len(rep.violations),
        status=status,
        reasons=reasons,
    )


# ── 실험 실행 ─────────────────────────────────────────────────────────────

def run_experiment(data_dir: str | Path,
                   algorithms: Iterable[str] = ("request_order", "nearest", "optimize"),
                   load_factors: Iterable[float] = DEFAULT_LOAD_FACTORS,
                   seeds: Iterable[int] = DEFAULT_SEEDS,
                   dates: Iterable[str] | None = None,
                   time_limit_sec: int = 10,
                   progress: Callable[[str], None] | None = None) -> pd.DataFrame:
    """여러 날 × 여러 방식 × 여러 부하율 × 여러 시드를 돌려 한 표로 돌려준다.

    계산 시간이 크게 늘 수 있으므로 날짜·시드 수를 먼저 줄여 확인할 것.
    """
    from planner.data import load_day, load_travel, service_dates

    d = Path(data_dir)
    travel = load_travel(d)
    all_dates = list(dates) if dates is not None else service_dates(d)

    rows: list[dict] = []
    cache: dict[str, dict] = {}

    for date in all_dates:
        if date not in cache:
            cache[date] = load_day(d, date, travel=travel).as_instance()
        base = cache[date]
        base_rho = load_factor(base)

        for rho in load_factors:
            for seed in seeds:
                inst = select_requests(base, rho, seed)
                inst = dict(inst, service_date=date)
                actual = load_factor(inst)
                for algo in algorithms:
                    res = run_day(inst, algo, seed=seed, time_limit_sec=time_limit_sec)
                    res.target_rho = rho
                    res.actual_rho = actual
                    rows.append(res.as_row())
                    if progress:
                        progress(f"{date} rho={rho} seed={seed} {algo} "
                                 f"→ {res.assigned}/{res.requests}")

    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    """방식×부하율로 묶어 평균과 변동을 낸다.

    평균만 보면 시드에 따라 얼마나 흔들리는지가 사라진다. 표준편차를 함께 낸다.
    """
    if df.empty:
        return df
    g = df.groupby(["target_rho", "algorithm"])
    out = pd.DataFrame({
        "적기배정률_평균": g["on_time_rate"].mean().round(4),
        "적기배정률_표준편차": g["on_time_rate"].std().round(4),
        "고유인원_평균": g["unique_beneficiaries"].mean().round(2),
        "총이동_평균": g["travel_minutes"].mean().round(1),
        "활용률_평균": g["utilization"].mean().round(4),
        "계산시간_평균": g["solve_seconds"].mean().round(2),
        "위반_합계": g["violations"].sum(),
        "관측수": g.size(),
    }).reset_index()
    return out


def save(df: pd.DataFrame, out_dir: str | Path = "data/experiments",
         tag: str = "run") -> Path:
    """결과를 CSV로 남긴다. 재현을 위해 실행 시각과 행 수를 파일명·헤더에 남긴다."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out / f"{tag}-{stamp}.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path
