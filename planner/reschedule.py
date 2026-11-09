"""취소·재배정 — 하루 중 취소가 닿았을 때 빈 슬롯을 다시 채우는 재생 엔진.

PRD 12장이 요구하는 두 가지를 구조로 지킨다.

  1. 취소·지연은 **이벤트 접수 시점에 공개**한다. 아직 통보되지 않은 취소를 미리 알고
     배정을 피하면 그것은 반칙이다. 통보 시각 이전에는 그 요청을 '아직 살아 있는 요청'으로 본다.
  2. 계획 시점과 실행 시점을 분리한다. 초기 배정은 서비스일 시작에 확정된 정보로 하고,
     이후 취소가 닿을 때마다 그 시점까지 알려진 것만으로 재배정한다.

동작 순서
  1. 초기 배정 — 전체 요청을 기준선 방식(신청순/근거리)으로 배정. 취소는 아직 모른다.
  2. 취소 이벤트를 통보 시각 순으로 정렬.
  3. 한 건씩 처리:
     - 배정돼 있던 요청이면 인력 일정에서 뺀다.
     - **남은 일정을 처음부터 다시 따라가며 확인한다(_retime).**
       일정 하나가 빠지면 앞뒤 경로가 바뀐다(A→B→C 가 A→C 로). A→C 가 B→C 보다 길면
       C 의 출발이 앞당겨져 휴게와 겹칠 수 있다. 원래 도착이 여전히 가능하면 그대로 두고,
       아니면 가장 이른 가능 시각으로 재조정하고, 그래도 안 되면 밀려난 것으로 기록한다.
       조용히 위반을 남기지 않는다.
     - 통보 시각 기준으로 아직 취소되지 않은 미배정 요청 중에서 빈자리 후보를 찾는다.
       후보를 넣은 뒤에도 같은 방식으로 다시 확인하고, 다른 일정이 밀려나면 넣지 않는다.
  4. 결과를 검증기로 다시 확인한다. 위반이 있으면 그 결과는 배정안이 아니다.

정직하게 남기는 한계
  - 취소 통보가 출발 이후에 닿으면 이동 낭비가 생긴다. 계산해 기록만 하고 막지는 못한다.
    낭비 이후 실제 위치(가던 중)는 모델링하지 않고, 출발 전 위치에서 바로 다음으로 간다고 본다.
  - 재배정은 탐욕적이다. 들어갈 수 있는 첫 자리를 찾으면 넣는다. 최적 재배정이 아니다.
  - retime 은 남은 일정의 도착 시각을 바꾼다. 실제 운영에서는 대상자 변경 통보가 필요하다.
    몇 건이 바뀌었는지 기록한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from planner.baseline import _State, _occupancy, _travel_map, _try_assign

# 재배정 결과 분류
REFILLED = "refilled"            # 빈 슬롯을 다른 요청으로 채웠다
LEFT_EMPTY = "left_empty"        # 채우지 못했다(후보 없음·시간 안 맞음)
NOT_ASSIGNED = "not_assigned"    # 처음부터 배정이 없었으므로 비울 슬롯도 없었다

# 미배정 사유
CANCELLED = "CANCELLED"
DISPLACED = "DISPLACED"          # 다른 취소의 여파로 남은 일정에서 밀려났다
UNASSIGNED = "UNASSIGNED"


@dataclass
class Cancellation:
    """취소 통보 한 건."""
    request_id: str
    notice_min: int              # 통보 시각(서비스일 자정 기준 분)
    service_start_min: int       # 원래 서비스 시작 예정 시각


@dataclass
class CancellationOutcome:
    request_id: str
    notice_min: int
    service_start_min: int
    had_assignment: bool
    worker_id: str | None
    already_departed: bool       # 통보 시점에 인력이 이미 이동을 시작했는가
    wasted_travel_min: int       # 낭비된 이동시간
    outcome: str                 # REFILLED / LEFT_EMPTY / NOT_ASSIGNED
    refilled_by: str | None = None
    retimed: list[str] = field(default_factory=list)     # 이 취소로 도착 시각이 바뀐 요청
    displaced: list[str] = field(default_factory=list)   # 이 취소로 밀려난 요청

    def as_dict(self) -> dict:
        return {
            "요청": self.request_id,
            "통보(분)": self.notice_min,
            "서비스 시작(분)": self.service_start_min,
            "배정 있었나": self.had_assignment,
            "인력": self.worker_id or "-",
            "이미 출발": self.already_departed,
            "낭비 이동(분)": self.wasted_travel_min,
            "결과": {REFILLED: "채움", LEFT_EMPTY: "빈 슬롯", NOT_ASSIGNED: "원래 미배정"}[self.outcome],
            "채운 요청": self.refilled_by or "-",
            "시각 변경": ", ".join(self.retimed) or "-",
            "밀려남": ", ".join(self.displaced) or "-",
        }


@dataclass
class ReplayResult:
    algorithm: str
    service_date: str = ""
    initial_assignments: list[dict] = field(default_factory=list)
    assignments: list[dict] = field(default_factory=list)
    unassigned: list[dict] = field(default_factory=list)
    cancellations: list[CancellationOutcome] = field(default_factory=list)
    violations: list[dict] = field(default_factory=list)
    refill_strategy: str = ""

    @property
    def ok(self) -> bool:
        return not self.violations

    def summary(self) -> dict:
        return {
            "algorithm": self.algorithm,
            "service_date": self.service_date,
            "initial_assigned": len(self.initial_assignments),
            "final_assigned": len(self.assignments),
            "final_unassigned": len(self.unassigned),
            "cancellations": len(self.cancellations),
            "cancelled_with_assignment": sum(1 for c in self.cancellations if c.had_assignment),
            "refilled": sum(1 for c in self.cancellations if c.outcome == REFILLED),
            "left_empty": sum(1 for c in self.cancellations if c.outcome == LEFT_EMPTY),
            "wasted_travel_min": sum(c.wasted_travel_min for c in self.cancellations),
            "retimed": sum(len(c.retimed) for c in self.cancellations),
            "displaced": sum(len(c.displaced) for c in self.cancellations),
            "violations": len(self.violations),
        }


# ── 초기 배정 ─────────────────────────────────────────────────────────────

def _initial(instance: dict, algorithm: str) -> list[dict]:
    from planner.baseline import nearest, request_order
    if algorithm == "request_order":
        return request_order(instance).assignments
    if algorithm == "nearest":
        return nearest(instance).assignments
    raise ValueError(f"재생은 기준선 방식만 지원한다: {algorithm}")


# ── 취소 이벤트 ───────────────────────────────────────────────────────────

def collect_cancellations(instance: dict, assignments: list[dict]) -> list[Cancellation]:
    """취소 통보 목록. 배정된 요청은 실제 도착 시각, 미배정 요청은 시간창 시작 기준.

    통보 시각 = 서비스 시작 − 통보 여유. 이 시각이 되어야만 우리가 취소를 안다.
    """
    by_id = {r["request_id"]: r for r in instance["requests"]}
    start_of = {a["request_id"]: int(a["arrive_min"]) for a in assignments}

    out: list[Cancellation] = []
    for rid, req in by_id.items():
        if not req.get("cancelled_same_day"):
            continue
        notice_before = req.get("cancel_notice_min_before")
        if notice_before is None:
            continue
        start = start_of.get(rid, int(req["earliest_start_min"]))
        out.append(Cancellation(request_id=rid,
                                notice_min=start - int(notice_before),
                                service_start_min=start))
    out.sort(key=lambda c: (c.notice_min, c.request_id))
    return out


# ── 일정 재확인 ───────────────────────────────────────────────────────────

def _fits_at(st: _State, req: dict, travel: dict, t: int) -> bool:
    """이 인력 상태에서 요청을 정확히 도착 t 로 수행할 수 있는가.

    검증기와 같은 규칙으로 본다: 출발 = 도착 − 이동, 이동·서비스 구간이 휴게와 겹치지 않음,
    시간창·근무시간·근무상한·예약 도착.
    """
    w = st.worker
    leg = travel.get((st.cursor, req["home_node"]))
    if leg is None:
        return False
    leg = int(round(leg))
    depart = t - leg
    shift_start, shift_end = int(w["shift_start_min"]), int(w["shift_end_min"])
    brk_s = int(w["break_start_min"])
    brk_e = brk_s + int(w["break_minutes"])
    cap = int(w.get("max_work_minutes") or 0)

    if depart < st.free or depart < shift_start:
        return False
    if not (int(req["earliest_start_min"]) <= t <= int(req["latest_start_min"])):
        return False

    end, span = _occupancy(req, travel, t)
    if end is None or end > shift_end:
        return False
    if depart < brk_e and brk_s < t:        # 이동이 휴게와 겹침
        return False
    if t < brk_e and brk_s < end:           # 서비스가 휴게와 겹침
        return False
    if cap and st.work_min + leg + span > cap:
        return False

    if req["service_type"] == "hospital_escort":
        to_h = travel.get((req["home_node"], req["hospital_node"]))
        if to_h is None:
            return False
        deadline = int(req["appointment_min"]) - int(req["arrival_buffer_min"])
        if t + int(round(to_h)) > deadline:
            return False
    return True


def _retime(worker: dict, jobs: list[dict], travel: dict, by_id: dict
            ) -> tuple[list[dict], list[str], list[str]]:
    """한 인력의 일정을 처음부터 다시 따라가며 실행 가능하게 맞춘다.

    → (유지된 일정, 도착 시각이 바뀐 요청, 밀려난 요청)
    """
    st = _State(worker=worker, cursor=worker["base_node"], free=int(worker["shift_start_min"]))
    kept: list[dict] = []
    retimed: list[str] = []
    displaced: list[str] = []

    for job in sorted(jobs, key=lambda j: (int(j["arrive_min"]), j["request_id"])):
        req = by_id[job["request_id"]]
        orig = int(job["arrive_min"])

        if _fits_at(st, req, travel, orig):
            t = orig
        else:
            t, _reason = _try_assign(st, req, travel)
            if t is None:
                displaced.append(job["request_id"])
                continue
            if t != orig:
                retimed.append(job["request_id"])

        end, span = _occupancy(req, travel, t)
        leg = travel.get((st.cursor, req["home_node"]))
        leg = int(round(leg)) if leg is not None else 0
        st.work_min += leg + span
        st.cursor = req["home_node"]
        st.free = int(end)
        kept.append({"request_id": job["request_id"], "worker_id": worker["worker_id"],
                     "arrive_min": int(t), "end_min": int(end)})
    return kept, retimed, displaced


def _jobs_of(worker_id: str, assignments: list[dict]) -> list[dict]:
    return sorted((a for a in assignments if a["worker_id"] == worker_id),
                  key=lambda a: (int(a["arrive_min"]), a["request_id"]))


# ── 빈자리 채우기 ─────────────────────────────────────────────────────────

def _attempt_insert(worker: dict, jobs: list[dict], candidate: dict, travel: dict,
                    by_id: dict) -> tuple[list[dict], list[str], list[str]] | None:
    """후보를 일정에 끼워 넣어 본다. 밀려나는 일정이 생기면 None(넣지 않는다).

    → (새 일정, 시각 변경된 요청, 밀려난 요청). 넣을 수 없으면 None.
    """
    trial = [{"request_id": j["request_id"], "worker_id": worker["worker_id"],
              "arrive_min": int(j["arrive_min"])} for j in jobs]

    # 기존 일정 사이의 각 지점에 후보를 넣어 보고, 넣을 수 있는 자리를 찾는다
    for pos in range(len(trial) + 1):
        if pos == 0:
            st = _State(worker=worker, cursor=worker["base_node"],
                        free=int(worker["shift_start_min"]))
        else:
            prev = by_id[trial[pos - 1]["request_id"]]
            end, _span = _occupancy(prev, travel, int(trial[pos - 1]["arrive_min"]))
            if end is None:
                continue
            st = _State(worker=worker, cursor=prev["home_node"], free=int(end))

        t, _reason = _try_assign(st, candidate, travel)
        if t is None:
            continue

        merged = (trial[:pos]
                  + [{"request_id": candidate["request_id"],
                      "worker_id": worker["worker_id"], "arrive_min": int(t)}]
                  + trial[pos:])
        kept, retimed, displaced = _retime(worker, merged, travel, by_id)
        # 후보가 밀려나거나 다른 일정이 밀려나면 이 자리는 쓰지 않는다
        if displaced:
            continue
        if not any(k["request_id"] == candidate["request_id"] for k in kept):
            continue
        return kept, retimed, displaced
    return None


def _candidate_order(candidates: list[dict], strategy: str) -> list[dict]:
    if strategy == "request_order":
        return sorted(candidates, key=lambda r: (r["requested_at"], r["request_id"]))
    if strategy == "nearest":
        return sorted(candidates, key=lambda r: (int(r["latest_start_min"]), r["request_id"]))
    raise ValueError(f"알 수 없는 재배정 전략: {strategy}")


# ── 재생 ─────────────────────────────────────────────────────────────────

def replay(instance: dict, algorithm: str = "request_order",
           refill_strategy: str = "request_order") -> ReplayResult:
    """하루를 재생한다. 초기 배정 → 취소 통보 순 처리 → 재배정."""
    from verifier import verify

    travel = _travel_map(instance["travel"])
    by_id = {r["request_id"]: r for r in instance["requests"]}
    workers = {w["worker_id"]: w for w in instance["workers"]}

    initial = _initial(instance, algorithm)
    result = ReplayResult(algorithm=algorithm,
                          service_date=str(instance.get("service_date", "")),
                          initial_assignments=[dict(a) for a in initial],
                          refill_strategy=refill_strategy)

    assignments = [dict(a) for a in initial]
    for a in assignments:
        end, _span = _occupancy(by_id[a["request_id"]], travel, int(a["arrive_min"]))
        a["end_min"] = int(end) if end is not None else int(a["arrive_min"])

    # 밀려난 요청은 이후 후보에서 제외한다(이미 다른 여파로 빠졌다)
    displaced_ids: set[str] = set()
    cancelled_so_far: set[str] = set()

    for ev in collect_cancellations(instance, assignments):
        cancelled_so_far.add(ev.request_id)

        idx = next((i for i, a in enumerate(assignments)
                    if a["request_id"] == ev.request_id), None)
        had = idx is not None
        worker_id = assignments[idx]["worker_id"] if had else None

        already_departed = False
        wasted = 0
        retimed: list[str] = []
        displaced: list[str] = []

        if had:
            a = assignments[idx]
            req = by_id[ev.request_id]
            jobs = _jobs_of(worker_id, assignments)
            pos = next(i for i, j in enumerate(jobs) if j["request_id"] == ev.request_id)
            prev_node = (workers[worker_id]["base_node"] if pos == 0
                         else by_id[jobs[pos - 1]["request_id"]]["home_node"])
            leg = travel.get((prev_node, req["home_node"]))
            depart = (int(a["arrive_min"]) - int(round(leg))
                      if leg is not None else int(a["arrive_min"]))
            already_departed = ev.notice_min >= depart
            if already_departed:
                wasted = int(round(leg)) if leg is not None else 0

            assignments = [x for x in assignments if x["request_id"] != ev.request_id]

            # 남은 일정을 다시 확인한다 — 앞뒤 경로가 바뀌었을 수 있다
            kept, retimed, displaced = _retime(workers[worker_id],
                                               _jobs_of(worker_id, assignments),
                                               travel, by_id)
            assignments = [x for x in assignments if x["worker_id"] != worker_id] + kept
            displaced_ids.update(displaced)

        outcome = CancellationOutcome(
            request_id=ev.request_id, notice_min=ev.notice_min,
            service_start_min=ev.service_start_min,
            had_assignment=had, worker_id=worker_id,
            already_departed=already_departed, wasted_travel_min=wasted,
            outcome=NOT_ASSIGNED if not had else LEFT_EMPTY,
            retimed=retimed, displaced=displaced,
        )

        if had and worker_id:
            assigned_ids = {a["request_id"] for a in assignments}
            candidates = [
                req for rid, req in by_id.items()
                if rid not in assigned_ids
                and rid not in cancelled_so_far
                and rid not in displaced_ids
            ]
            for cand in _candidate_order(candidates, refill_strategy):
                need = set(cand.get("required_skills") or [])
                if not need.issubset(set(workers[worker_id].get("skills") or [])):
                    continue
                trial = _attempt_insert(workers[worker_id],
                                        _jobs_of(worker_id, assignments),
                                        cand, travel, by_id)
                if trial is None:
                    continue
                kept, more_retimed, _none = trial
                assignments = [x for x in assignments
                               if x["worker_id"] != worker_id] + kept
                outcome.outcome = REFILLED
                outcome.refilled_by = cand["request_id"]
                outcome.retimed.extend(r for r in more_retimed if r not in outcome.retimed)
                break

        result.cancellations.append(outcome)

    # 최종 정리 — 밀려난 일정이 취소 뒤에 남아 있으면 안 된다
    assigned_ids = {a["request_id"] for a in assignments}
    result.assignments = sorted(
        ({"request_id": a["request_id"], "worker_id": a["worker_id"],
          "arrive_min": int(a["arrive_min"])} for a in assignments),
        key=lambda a: a["request_id"],
    )
    result.unassigned = [
        {"request_id": rid,
         "reason": (CANCELLED if rid in cancelled_so_far
                    else DISPLACED if rid in displaced_ids
                    else UNASSIGNED),
         "certainty": "verified"}
        for rid in by_id if rid not in assigned_ids
    ]

    rep = verify(instance, result.assignments)
    result.violations = [v.as_dict() for v in rep.violations]
    return result
