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


def widen_windows(instance: dict, escort_width: int | None = None, household_extra: int = 0) -> dict:
    """시간창 폭을 바꾼 새 instance (실험 변수). 원래 instance 는 바꾸지 않는다.

    escort_width   병원동행 픽업창 폭(분). 늦어도 출발 시각(예약에 맞춘 상한)은 그대로 두고,
                   이른 쪽만 '상한 − 폭'으로 옮긴다. 즉 더 일찍 모시고 가 병원에서 기다리는 것을 허용한다.
                   None 이면 생성된 창 그대로(f0 기본 20분).
    household_extra 가사지원 시간창을 앞뒤로 이만큼 넓힌다(분). 0 이면 그대로(f0 기본 ±60분).
    두 경우 모두 인력 근무시간(가장 이른 시작 ~ 가장 늦은 종료) 밖으로는 넓히지 않는다.
    요청 집합·부하율은 바뀌지 않으므로 같은 날·같은 시드끼리 짝지어 비교할 수 있다.
    """
    shift_lo = min(int(w["shift_start_min"]) for w in instance["workers"])
    shift_hi = max(int(w["shift_end_min"]) for w in instance["workers"])
    out_reqs = []
    for q in instance["requests"]:
        q = dict(q)
        if q["service_type"] == "hospital_escort" and escort_width is not None:
            q["earliest_start_min"] = max(shift_lo, int(q["latest_start_min"]) - int(escort_width))
        elif q["service_type"] == "household_support" and household_extra:
            q["earliest_start_min"] = max(shift_lo, int(q["earliest_start_min"]) - int(household_extra))
            q["latest_start_min"] = min(shift_hi - int(q["service_minutes"]),
                                        int(q["latest_start_min"]) + int(household_extra))
        out_reqs.append(q)
    return dict(instance, requests=out_reqs)


ESCORT_SKILL = "hospital_escort"


def change_staffing(instance: dict, retrain: int = 0, add: int = 0) -> dict:
    """동행 가능 인력 수를 바꾼 새 instance (실험 변수). 원래 instance 는 바꾸지 않는다.

    retrain  가사 전담 인력 중 이만큼에게 병원동행 자격을 더한다(같은 인원·같은 예산, 교육 가정).
             worker_id 순서로 고른다(재현 가능).
    add      병원동행 자격 인력을 이만큼 새로 더한다(추가 채용 가정). 기존 동행 인력을
             worker_id 순서로 돌아가며 복제해 출퇴근 위치·근무시간·휴게를 같게 둔다
             (이동시간 행렬에 없는 새 위치를 만들지 않기 위해서다).
    요청 집합은 그대로라서 같은 날·같은 시드끼리 짝지어 비교할 수 있다.
    인력이 늘면 가용시간이 늘어 실제 부하율(actual_rho)은 낮아진다.
    """
    workers = [dict(w, skills=list(w.get("skills") or [])) for w in instance["workers"]]
    if retrain:
        plain = sorted((w for w in workers if ESCORT_SKILL not in w["skills"]),
                       key=lambda w: w["worker_id"])
        if retrain > len(plain):
            raise ValueError(f"교육할 가사 전담 인력이 {len(plain)}명뿐입니다(요청 {retrain}명)")
        for w in plain[:retrain]:
            w["skills"].append(ESCORT_SKILL)
    if add:
        escort = sorted((w for w in instance["workers"] if ESCORT_SKILL in (w.get("skills") or [])),
                        key=lambda w: w["worker_id"])
        if not escort:
            raise ValueError("복제할 동행 인력이 없습니다")
        for i in range(add):
            src = escort[i % len(escort)]
            workers.append(dict(src, skills=list(src["skills"]), worker_id=f"WADD{i + 1:02d}"))
    return dict(instance, workers=workers)


DEFAULT_BREAK_STARTS = (660, 720, 780, 840)   # 11·12·13·14시


def stagger_breaks(instance: dict, starts: Iterable[int] = DEFAULT_BREAK_STARTS) -> dict:
    """휴게 시작 시각을 인력마다 돌아가며 다르게 둔 새 instance (실험 변수).

    기본 데이터는 모든 인력이 같은 시각에 휴게한다. 병원동행은 2.5~4시간 연속 점유라
    휴게를 넘는 예약은 누구도 맡을 수 없다. 휴게 길이는 그대로 두고 시작 시각만
    worker_id 순서로 starts 를 돌아가며 배정한다(휴게 보장은 유지, 시각은 운영 가정).
    근무시간 밖으로 나가는 시작 시각은 쓰지 않는다.
    """
    starts = [int(s) for s in starts]
    workers = []
    order = sorted(instance["workers"], key=lambda w: w["worker_id"])
    pos = {w["worker_id"]: i for i, w in enumerate(order)}
    for w in instance["workers"]:
        w = dict(w)
        s = starts[pos[w["worker_id"]] % len(starts)]
        lo, hi = int(w["shift_start_min"]), int(w["shift_end_min"]) - int(w["break_minutes"])
        if not lo <= s <= hi:
            raise ValueError(f"{w['worker_id']} 휴게 시작 {s}분이 근무시간 밖입니다")
        w["break_start_min"] = s
        workers.append(w)
    return dict(instance, workers=workers)


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
    # 서비스별 요청·배정 수(병원동행이 병목인지 보기 위함)
    requests_escort: int = 0
    assigned_escort: int = 0
    start_note: str = ""
    # 높은 우선순위(priority >= 2, 퇴원 14일 이내 등) 요청·배정 수와 그날 요청이 모두 배정된 사람 수
    requests_high: int = 0
    assigned_high: int = 0
    fully_met_beneficiaries: int = 0
    # 재생(replay) 실행에서만 채워지는 취소 관련 수치
    extra: dict = field(default_factory=dict)
    # 요청별 결과(outcome_rows). 표(as_row)에는 넣지 않는다.
    outcomes: list = field(default_factory=list)

    @property
    def on_time_rate(self) -> float:
        return self.assigned / self.requests if self.requests else 0.0

    @property
    def utilization(self) -> float:
        return self.work_minutes / self.available_minutes if self.available_minutes else 0.0

    def as_row(self) -> dict:
        row = {
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
            "requests_escort": self.requests_escort,
            "assigned_escort": self.assigned_escort,
            "start_note": self.start_note,
            "requests_high": self.requests_high,
            "assigned_high": self.assigned_high,
            "fully_met_beneficiaries": self.fully_met_beneficiaries,
        }
        # 재생 실행이면 취소 관련 열을 덧붙인다. 다른 방식은 비워 둔다.
        for key in ("cancellations", "cancelled_with_assignment", "refilled",
                    "left_empty", "wasted_travel_min", "retimed", "displaced",
                    "initial_assigned", "delays", "delay_kept", "delay_lost",
                    "delay_moved", "delay_late_avg", "delay_late_max"):
            row[key] = self.extra.get(key, "")
        return row


HIGH_PRIORITY = 2


def outcome_rows(instance: dict, assigned: dict[str, str], unassigned: list,
                 area_of: dict[str, str] | None = None) -> list[dict]:
    """요청마다 한 줄: 배정 여부·인력·미배정 사유·확실성·서비스·우선순위·희망 시작 시간대·권역.

    assigned 는 {request_id: worker_id}. 사유가 없는 미배정은 UNKNOWN/unknown 으로 둔다(지어내지 않음).
    권역을 모르면 빈 값으로 둔다.
    """
    why = {}
    for u in unassigned:
        u = u if isinstance(u, dict) else u.as_dict()
        why[u["request_id"]] = (u.get("reason") or "UNKNOWN", u.get("certainty") or "unknown")
    area_of = area_of or {}
    rows = []
    for r in instance["requests"]:
        rid = r["request_id"]
        ok = rid in assigned
        reason, cert = ("", "") if ok else why.get(rid, ("UNKNOWN", "unknown"))
        rows.append({
            "request_id": rid,
            "beneficiary_id": r["beneficiary_id"],
            "service_type": r["service_type"],
            "priority": int(r.get("priority", 1)),
            "start_hour": int(r["earliest_start_min"]) // 60,
            "area": area_of.get(r["beneficiary_id"], ""),
            "assigned": ok,
            "worker_id": assigned.get(rid, ""),
            "reason": reason,
            "certainty": cert,
            "idle_check": "",
        })
    return rows


IDLE_CAPACITY = "CAPACITY"   # 빈 일정의 자격 인력이면 맡을 수 있었다 → 인력 여유(경쟁) 부족
IDLE_BREAK = "BREAK"         # 빈 일정이어도 불가, 휴게를 빼면 가능 → 휴게 시각과 충돌
IDLE_WINDOW = "WINDOW"       # 휴게를 빼도 불가 → 시간창·근무시간 자체가 안 맞음
IDLE_NO_SKILL = "NO_SKILL"   # 자격 맞는 인력이 없음
IDLE_ROUTE = "ROUTE"         # 이동시간 없음


def idle_check(req: dict, workers: list[dict], travel_map: dict) -> str:
    """미배정 요청을 '빈 일정의 자격 인력'으로 다시 시험해 구조적 원인과 인력 여유 부족을 나눈다.

    배정 엔진의 사유 코드를 바꾸지 않는 진단용 분류다(부족 자원 리포트에서만 쓴다).
    """
    from planner.baseline import ROUTE_UNKNOWN, _State, _try_assign

    need = set(req.get("required_skills") or [])
    skilled = [w for w in workers if need.issubset(set(w.get("skills") or []))]
    if not skilled:
        return IDLE_NO_SKILL

    def fits(w):
        return _try_assign(_State(worker=w, cursor=w["base_node"], free=int(w["shift_start_min"])),
                           req, travel_map)

    tried = [fits(w) for w in skilled]
    if any(t is not None for t, _ in tried):
        return IDLE_CAPACITY
    if all(r == ROUTE_UNKNOWN for _, r in tried):
        return IDLE_ROUTE
    # 휴게를 없앤 경우: 길이 0이라도 겹침 검사에 걸리므로 근무 종료 시각으로 옮긴다
    if any(fits(dict(w, break_minutes=0, break_start_min=int(w["shift_end_min"])))[0] is not None
           for w in skilled):
        return IDLE_BREAK
    return IDLE_WINDOW


def fully_met(rows: list[dict]) -> int:
    """그날 요청이 모두 배정된 고유 인원 수(PRD 4.1 '필수 요청 전체 충족 인원'의 하루 단위)."""
    people: dict[str, bool] = {}
    for r in rows:
        people[r["beneficiary_id"]] = people.get(r["beneficiary_id"], True) and r["assigned"]
    return sum(people.values())


def run_day(instance: dict, algorithm: str, seed: int = 42,
            time_limit_sec: int = 30) -> DayResult:
    """하루치를 한 방식으로 돌리고 지표를 계산한다. 결과는 검증기로 다시 확인한다.

    방식 이름의 접미사가 취소 처리를 정한다.
      'request_order'        취소를 무시한다(기준선). 취소된 요청도 배정으로 센다.
      'request_order_cancel' 취소를 반영하되 빈자리는 채우지 않는다.
      'request_order_replay' 취소를 반영하고 빈자리를 재배정한다.
    취소가 있는 날의 공정한 비교는 `_cancel` 행끼리, 또는 `_replay` 행끼리 해야 한다.
    """
    from planner.baseline import nearest, request_order
    from planner.optimize import optimize
    from planner.reschedule import replay
    from verifier import verify

    cancel_stats: dict = {}
    verify_instance = instance
    start_note = ""     # 최적화만: warm(근거리 첫 해에서 출발) / cold / kept_start(첫 해를 그대로 반환)
    if algorithm.endswith("_replay") or algorithm.endswith("_cancel"):
        is_replay = algorithm.endswith("_replay")
        suffix = "_replay" if is_replay else "_cancel"
        base_algo = algorithm[: -len(suffix)]
        rp = replay(instance, base_algo, refill_strategy=base_algo, refill=is_replay)
        assignments = rp.assignments
        unassigned = rp.unassigned
        status, secs = "ok", 0.0
        cancel_stats = rp.summary()
        if rp.violations:
            status = "violations"
        # 지연이 있으면 시간창이 밀린 instance 로 검증해야 한다. 원래 창으로 다시
        # 검증하면 정상적으로 늦춰진 배정이 시간창 위반으로 잘못 잡힌다.
        if rp.verified_instance is not None:
            verify_instance = rp.verified_instance
    elif algorithm == "request_order":
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
        start_note = ("kept_start" if r.kept_start else "warm" if r.warm_started else "cold")
    else:
        raise ValueError(f"알 수 없는 방식: {algorithm}")

    rep = verify(verify_instance, assignments)
    outcomes = outcome_rows(instance, {a["request_id"]: a["worker_id"] for a in assignments},
                            unassigned, instance.get("area_of"))
    if any(not o["assigned"] for o in outcomes):
        from planner.baseline import _travel_map
        tmap = _travel_map(instance["travel"])
        for o, r in zip(outcomes, instance["requests"]):
            if not o["assigned"]:
                o["idle_check"] = idle_check(r, instance["workers"], tmap)
    high = [o for o in outcomes if o["priority"] >= HIGH_PRIORITY]

    req_by_id = {x["request_id"]: x for x in instance["requests"]}
    served = {req_by_id[a["request_id"]]["beneficiary_id"] for a in assignments}
    everyone = {x["beneficiary_id"] for x in instance["requests"]}

    reasons: dict[str, int] = {}
    for u in unassigned:
        key = u["reason"] if isinstance(u, dict) else u.reason
        reasons[key] = reasons.get(key, 0) + 1

    result = DayResult(
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
        requests_escort=sum(1 for x in instance["requests"] if x["service_type"] == "hospital_escort"),
        assigned_escort=sum(1 for a in assignments
                            if req_by_id[a["request_id"]]["service_type"] == "hospital_escort"),
        start_note=start_note,
        requests_high=len(high),
        assigned_high=sum(o["assigned"] for o in high),
        fully_met_beneficiaries=fully_met(outcomes),
        outcomes=outcomes,
    )
    # 재생 실행이면 취소 관련 수치를 함께 남긴다
    if cancel_stats:
        result.extra = {
            "cancellations": cancel_stats.get("cancellations", 0),
            "cancelled_with_assignment": cancel_stats.get("cancelled_with_assignment", 0),
            "refilled": cancel_stats.get("refilled", 0),
            "left_empty": cancel_stats.get("left_empty", 0),
            "wasted_travel_min": cancel_stats.get("wasted_travel_min", 0),
            "initial_assigned": cancel_stats.get("initial_assigned", 0),
            "retimed": cancel_stats.get("retimed", 0),
            "displaced": cancel_stats.get("displaced", 0),
            "delays": cancel_stats.get("delays", 0),
            "delay_kept": cancel_stats.get("delay_kept", 0),
            "delay_lost": cancel_stats.get("delay_lost", 0),
            "delay_moved": cancel_stats.get("delay_moved", 0),
            "delay_late_avg": cancel_stats.get("delay_late_avg", 0.0),
            "delay_late_max": cancel_stats.get("delay_late_max", 0),
        }
    return result


# ── 실험 실행 ─────────────────────────────────────────────────────────────

def run_experiment(data_dir: str | Path,
                   algorithms: Iterable[str] = ("request_order", "nearest", "optimize"),
                   load_factors: Iterable[float] = DEFAULT_LOAD_FACTORS,
                   seeds: Iterable[int] = DEFAULT_SEEDS,
                   dates: Iterable[str] | None = None,
                   time_limit_sec: int = 10,
                   progress: Callable[[str], None] | None = None,
                   transform: Callable[[dict], dict] | None = None,
                   outcome_sink: list | None = None) -> pd.DataFrame:
    """여러 날 × 여러 방식 × 여러 부하율 × 여러 시드를 돌려 한 표로 돌려준다.

    algorithms 에 'request_order_replay' 처럼 '_replay' 를 붙이면 취소 재배정을 포함한다.
    해당 날짜에 취소가 하나도 없으면 재생 결과는 기준선과 같다(그것도 기록한다).

    계산 시간이 크게 늘 수 있으므로 날짜·시드 수를 먼저 줄여 확인할 것.
    transform 은 요청을 고른 '뒤'에 적용한다(예: widen_windows). 요청 집합과 부하율은 그대로다.
    outcome_sink 에 목록을 주면 요청별 결과(outcome_rows + 날짜·시드·부하율·방식)를 덧붙인다.
    """
    from planner.data import load_day, load_travel, service_dates

    d = Path(data_dir)
    travel = load_travel(d)
    area_of = _area_of(d) if outcome_sink is not None else {}
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
                if transform is not None:
                    inst = transform(inst)
                actual = load_factor(inst)
                for algo in algorithms:
                    res = run_day(inst, algo, seed=seed, time_limit_sec=time_limit_sec)
                    res.target_rho = rho
                    res.actual_rho = actual
                    rows.append(res.as_row())
                    if outcome_sink is not None:
                        for o in res.outcomes:
                            o = dict(o, service_date=date, seed=seed, target_rho=rho, algorithm=algo)
                            o["area"] = o["area"] or area_of.get(o["beneficiary_id"], "")
                            outcome_sink.append(o)
                    if progress:
                        progress(f"{date} rho={rho} seed={seed} {algo} "
                                 f"→ {res.assigned}/{res.requests}")

    return pd.DataFrame(rows)


def _area_of(data_dir: Path) -> dict[str, str]:
    """beneficiaries.csv 의 행정동(dong). 파일이 없으면 빈 사전."""
    p = Path(data_dir) / "beneficiaries.csv"
    if not p.exists():
        return {}
    b = pd.read_csv(p, usecols=["beneficiary_id", "dong"], encoding="utf-8-sig")
    return dict(zip(b.beneficiary_id.astype(str), b.dong.astype(str)))


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

    # 재생(replay) 실행이 있으면 취소 관련 지표를 덧붙인다.
    # 비재생 행은 빈 문자열이므로 숫자로 바꿔 계산한다.
    # 열이 아예 없을 수도 있다(예전에 저장한 CSV). 있는 열만 계산한다 —
    # 없는 열을 요구하면 이미 저장된 원본으로는 요약을 복구할 수 없다.
    extra_spec = {
        "취소_평균": ("cancellations", 2),
        "배정있던취소_평균": ("cancelled_with_assignment", 2),
        "재배정성공_평균": ("refilled", 2),
        "빈슬롯_평균": ("left_empty", 2),
        "낭비이동_평균": ("wasted_travel_min", 1),
        "시각변경_평균": ("retimed", 2),
        "밀려남_평균": ("displaced", 2),
        "지연_평균": ("delays", 2),
        "지연지킴_평균": ("delay_kept", 2),
        "지연상실_평균": ("delay_lost", 2),
        "다른인력이동_평균": ("delay_moved", 2),
        "지연폭_평균": ("delay_late_avg", 1),
        "지연폭_최대": ("delay_late_max", 0),
    }
    if "cancellations" in df.columns:
        num = df.copy()
        for col, _digits in extra_spec.values():
            if col in num.columns:
                num[col] = pd.to_numeric(num[col], errors="coerce")
        gr = num.groupby(["target_rho", "algorithm"])
        cols = {}
        for out_name, (src_col, digits) in extra_spec.items():
            if src_col in num.columns:
                cols[out_name] = gr[src_col].mean().round(digits)

        missing = [src for src, _d in extra_spec.values() if src not in num.columns]
        if missing:
            print(f"  참고: 원본 CSV 에 없는 열이라 건너뜁니다 — {', '.join(missing)}")

        if cols:
            out = out.merge(pd.DataFrame(cols).reset_index(),
                            on=["target_rho", "algorithm"], how="left")
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
