"""하드 제약 검증기 — 엔진과 독립.

중요: 이 패키지는 `planner`(배정 엔진)를 import하지 않는다.
엔진이 스스로 만든 가정을 그대로 답습하면 검증이 아니라 자기 확인이 되기 때문이다.
입력은 순수 dict(또는 dataclass)이고, 여기서 다시 계산한 값만 신뢰한다.

검사 범위는 PRD 6.3 하드 제약이다.
  C1 요청당 최대 1명 배정
  C2 인력 일정 겹침 없음(이동 포함)
  C3 필요 자격 충족
  C4 시간창·예약 도착(도착 여유 포함)
  C5 근무시간 안
  C6 휴게 보장
  C7 초과근무 기본 금지

원칙: 확실하지 않은 것을 0분이나 빈 값으로 대체하지 않는다.
이동시간이 없으면 ROUTE_UNKNOWN 위반으로 기록하고 계산을 멈춘다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

# ── 위반 코드 ─────────────────────────────────────────────────────────────
ONE_WORKER_PER_REQUEST = "ONE_WORKER_PER_REQUEST"
UNKNOWN_REQUEST = "UNKNOWN_REQUEST"
UNKNOWN_WORKER = "UNKNOWN_WORKER"
NO_SKILL_MATCH = "NO_SKILL_MATCH"
TIME_WINDOW_CONFLICT = "TIME_WINDOW_CONFLICT"
APPOINTMENT_ARRIVAL = "APPOINTMENT_ARRIVAL"
ROUTE_UNKNOWN = "ROUTE_UNKNOWN"
SHIFT_VIOLATION = "SHIFT_VIOLATION"
BREAK_VIOLATION = "BREAK_VIOLATION"
OVERLAP = "OVERLAP"
OVERTIME = "OVERTIME"
MISSING_REQUIRED = "MISSING_REQUIRED"
DATA_INCONSISTENT = "DATA_INCONSISTENT"

# PRD 6.3 의 항목 번호와 검사 코드를 잇는다. 보고서에 그대로 쓴다.
CONSTRAINT_OF = {
    ONE_WORKER_PER_REQUEST: "6.3-1",
    UNKNOWN_REQUEST: "6.3-1",
    UNKNOWN_WORKER: "6.3-1",
    OVERLAP: "6.3-2",
    NO_SKILL_MATCH: "6.3-3",
    TIME_WINDOW_CONFLICT: "6.3-4",
    APPOINTMENT_ARRIVAL: "6.3-4",
    ROUTE_UNKNOWN: "6.3-4",
    SHIFT_VIOLATION: "6.3-5",
    OVERTIME: "6.3-5",
    BREAK_VIOLATION: "6.3-2",
    MISSING_REQUIRED: "6.3-7",
    DATA_INCONSISTENT: "6.3-7",
}


@dataclass(frozen=True)
class Violation:
    code: str
    request_id: str | None
    worker_id: str | None
    detail: str

    @property
    def constraint(self) -> str:
        return CONSTRAINT_OF.get(self.code, "?")

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "constraint": self.constraint,
            "request_id": self.request_id,
            "worker_id": self.worker_id,
            "detail": self.detail,
        }


@dataclass
class TimelineEntry:
    """한 요청을 인력이 수행하는 구간. 모두 서비스일 자정 기준 분."""
    request_id: str
    worker_id: str
    depart_min: int
    arrive_min: int
    end_min: int
    travel_min: int
    hospital_arrive_min: int | None
    service_type: str


@dataclass
class Report:
    violations: list[Violation] = field(default_factory=list)
    timelines: list[TimelineEntry] = field(default_factory=list)
    unassigned: list[str] = field(default_factory=list)
    work_minutes: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.violations

    def codes(self) -> set[str]:
        return {v.code for v in self.violations}

    def summary(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "violation_count": len(self.violations),
            "violations": [v.as_dict() for v in self.violations],
            "unassigned": sorted(self.unassigned),
            "work_minutes": dict(sorted(self.work_minutes.items())),
        }


class _Travel:
    """방향별 이동시간. 없는 구간은 None을 돌려주고 0으로 대체하지 않는다.

    행 목록(list)과 {(from, to): 분} 딕셔너리를 모두 받는다.
    """

    def __init__(self, rows):
        self._m: dict[tuple[str, str], float] = {}
        if isinstance(rows, dict):
            self._m = {(a, b): float(m) for (a, b), m in rows.items()}
        else:
            for r in rows:
                self._m[(r["from_node"], r["to_node"])] = float(r["minutes"])

    def minutes(self, a: str, b: str) -> float | None:
        if a == b:
            return 0.0
        return self._m.get((a, b))


def _require(item: dict, keys: list[str], who: str, out: list[Violation]) -> bool:
    missing = [k for k in keys if item.get(k) is None]
    if missing:
        out.append(Violation(MISSING_REQUIRED, item.get("request_id"), None,
                             f"{who} 필수값 누락: {', '.join(missing)}"))
        return False
    return True


def _service_model(req: dict, travel: _Travel, arrive: int
                   ) -> tuple[int, int, int | None, str | None]:
    """도착 시각 기준 → (점유 끝, 점유 길이, 병원 도착, 오류 코드).

    병원동행은 '집 픽업 → 병원 → 집 귀가'를 하나의 노드로 본다(CLAUDE.md 5절).
    점유시간 = 이동(집→병원) + 병원 체류 + 이동(병원→집).
    """
    if req["service_type"] != "hospital_escort":
        svc = int(req["service_minutes"])
        return arrive + svc, svc, None, None

    to_h = travel.minutes(req["home_node"], req["hospital_node"])
    back = travel.minutes(req["hospital_node"], req["home_node"])
    if to_h is None or back is None:
        return arrive, 0, None, ROUTE_UNKNOWN

    stay = int(req["hospital_stay_min"])
    hospital_arrive = arrive + int(round(to_h))
    end = hospital_arrive + stay + int(round(back))
    return end, int(round(to_h)) + stay + int(round(back)), hospital_arrive, None


def verify(instance: dict, assignments: Iterable[dict]) -> Report:
    """배정안을 하드 제약으로 검증한다. 엔진 출력이든 기준선 출력이든 같은 함수로 본다.

    instance: {workers, requests, travel, (nodes)}
    assignments: [{request_id, worker_id, arrive_min}]
    """
    rep = Report()

    workers = {w["worker_id"]: w for w in instance["workers"]}
    requests = {r["request_id"]: r for r in instance["requests"]}
    travel = _Travel(instance["travel"])

    # ── 입력 자체의 일관성 (PRD 6.3-7: 확인되지 않은 값 임의 보완 금지) ──
    # 필수값이 빠진 요청은 여기서 기록하고, 배정 루프에서는 계산하지 않고 건너뛴다.
    invalid_requests: set[str] = set()
    for req in requests.values():
        if not _require(req, ["request_id", "beneficiary_id", "service_type", "service_date",
                              "home_node", "earliest_start_min", "latest_start_min",
                              "service_minutes", "required_skills"],
                        "요청", rep.violations):
            invalid_requests.add(req["request_id"])
            continue
        if int(req["earliest_start_min"]) > int(req["latest_start_min"]):
            rep.violations.append(Violation(
                DATA_INCONSISTENT, req["request_id"], None,
                f"시간창 역전: earliest {req['earliest_start_min']} > latest {req['latest_start_min']}"))
        if req["service_type"] == "hospital_escort" and not _require(
                req, ["hospital_node", "appointment_min", "arrival_buffer_min", "hospital_stay_min"],
                "병원동행 요청", rep.violations):
            invalid_requests.add(req["request_id"])

    for w in workers.values():
        _require(w, ["worker_id", "skills", "base_node", "shift_start_min",
                     "shift_end_min", "break_start_min", "break_minutes"], "인력", rep.violations)

    # ── C1: 요청당 최대 1명 ───────────────────────────────────────────────
    assigned_to: dict[str, str] = {}
    rows_by_worker: dict[str, list[tuple[int, dict]]] = {}
    for a in assignments:
        rid, wid = a.get("request_id"), a.get("worker_id")
        if rid not in requests:
            rep.violations.append(Violation(UNKNOWN_REQUEST, rid, wid,
                                            "배정에 요청에 없는 request_id"))
            continue
        if rid in invalid_requests:
            # 필수값이 빠진 요청은 시간축 계산을 할 수 없다. 위반은 이미 기록됐으므로 건너뛴다.
            continue
        if wid not in workers:
            rep.violations.append(Violation(UNKNOWN_WORKER, rid, wid,
                                            "배정에 인력 목록에 없는 worker_id"))
            continue
        if rid in assigned_to:
            rep.violations.append(Violation(
                ONE_WORKER_PER_REQUEST, rid, wid,
                f"이미 {assigned_to[rid]}에게 배정된 요청을 중복 배정"))
            continue
        if a.get("arrive_min") is None:
            rep.violations.append(Violation(MISSING_REQUIRED, rid, wid, "도착 시각(arrive_min) 누락"))
            continue
        assigned_to[rid] = wid
        rows_by_worker.setdefault(wid, []).append((int(a["arrive_min"]), requests[rid]))

    rep.unassigned = sorted(set(requests) - set(assigned_to))

    # ── 인력별 시간축 검사 (C2·C3·C4·C5·C6·C7) ────────────────────────────
    for wid, items in rows_by_worker.items():
        w = workers[wid]
        base = w["base_node"]
        shift_start, shift_end = int(w["shift_start_min"]), int(w["shift_end_min"])
        brk_start = int(w["break_start_min"])
        brk_end = brk_start + int(w["break_minutes"])
        cap = int(w.get("max_work_minutes") or 0)

        items.sort(key=lambda t: t[0])
        cursor = base              # 인력이 마지막으로 있던 노드
        cursor_free = shift_start  # 그 노드에서 다음 일을 시작할 수 있는 시각
        work_min = 0
        busy: list[tuple[int, int, str]] = []   # (시작, 끝, 설명) — 겹침 판정용

        for arrive, req in items:
            rid = req["request_id"]

            # C3 자격
            need = set(req.get("required_skills") or [])
            have = set(w.get("skills") or [])
            if not need.issubset(have):
                rep.violations.append(Violation(
                    NO_SKILL_MATCH, rid, wid,
                    f"필요 자격 {sorted(need)} 중 부족: {sorted(need - have)}"))

            # 이동: 직전 위치 → 이번 요청 집
            leg = travel.minutes(cursor, req["home_node"])
            if leg is None:
                rep.violations.append(Violation(
                    ROUTE_UNKNOWN, rid, wid,
                    f"이동시간 없음: {cursor} → {req['home_node']} (0분으로 대체하지 않음)"))
                break   # 이후 구간은 계산할 수 없다. 추정으로 채우지 않는다.
            leg = int(round(leg))
            depart = arrive - leg

            # C5 출발이 근무 시작 전이면 위반. 도착이 근무 시작 전이어도 위반.
            if depart < shift_start or arrive < shift_start:
                rep.violations.append(Violation(
                    SHIFT_VIOLATION, rid, wid,
                    f"출발 {depart}·도착 {arrive} 이 근무시작 {shift_start} 이전"))

            # 직전 일과 겹치는지 (이동 포함)
            if depart < cursor_free:
                rep.violations.append(Violation(
                    OVERLAP, rid, wid,
                    f"출발 {depart} 이 직전 구간 종료 {cursor_free} 이전 (이동 포함 겹침)"))

            end, span, hospital_arrive, err = _service_model(req, travel, arrive)
            if err == ROUTE_UNKNOWN:
                rep.violations.append(Violation(
                    ROUTE_UNKNOWN, rid, wid,
                    f"병원동행 이동시간 없음: {req['home_node']} ↔ {req['hospital_node']}"))
                break

            # C4 시간창 (픽업 도착 기준) · 예약 도착
            lo, hi = int(req["earliest_start_min"]), int(req["latest_start_min"])
            if not (lo <= arrive <= hi):
                rep.violations.append(Violation(
                    TIME_WINDOW_CONFLICT, rid, wid,
                    f"도착 {arrive} 이 시간창 {lo}~{hi} 밖"))
            if req["service_type"] == "hospital_escort" and hospital_arrive is not None:
                deadline = int(req["appointment_min"]) - int(req["arrival_buffer_min"])
                if hospital_arrive > deadline:
                    rep.violations.append(Violation(
                        APPOINTMENT_ARRIVAL, rid, wid,
                        f"병원 도착 {hospital_arrive} 이 예약 {req['appointment_min']} "
                        f"- 여유 {req['arrival_buffer_min']} = {deadline} 이후"))

            # C5 근무 종료
            if end > shift_end:
                rep.violations.append(Violation(
                    SHIFT_VIOLATION, rid, wid,
                    f"점유 종료 {end} 이 근무종료 {shift_end} 이후"))

            # C6 휴게: 이동 구간과 점유 구간이 휴게와 겹치면 위반
            for seg_start, seg_end, label in ((depart, arrive, "이동"), (arrive, end, "서비스")):
                if seg_start < brk_end and brk_start < seg_end:
                    rep.violations.append(Violation(
                        BREAK_VIOLATION, rid, wid,
                        f"{label} {seg_start}~{seg_end} 가 휴게 {brk_start}~{brk_end} 와 겹침"))

            busy.append((depart, end, rid))
            work_min += leg + span
            cursor, cursor_free = req["home_node"], end
            rep.timelines.append(TimelineEntry(
                request_id=rid, worker_id=wid, depart_min=depart, arrive_min=arrive,
                end_min=end, travel_min=leg, hospital_arrive_min=hospital_arrive,
                service_type=req["service_type"]))

        # 같은 인력 안에서 시간 구간이 서로 겹치는지 한 번 더 확인
        busy.sort()
        for (s1, e1, r1), (s2, e2, r2) in zip(busy, busy[1:]):
            if s2 < e1:
                rep.violations.append(Violation(
                    OVERLAP, r2, wid, f"{r1} 구간 {s1}~{e1} 과 {r2} 구간 시작 {s2} 가 겹침"))

        # C7 초과근무
        if cap and work_min > cap:
            rep.violations.append(Violation(
                OVERTIME, None, wid, f"근무 {work_min}분 이 상한 {cap}분 초과"))
        rep.work_minutes[wid] = work_min

    rep.timelines.sort(key=lambda t: t.request_id)
    return rep


def load_instance(path: str) -> dict:
    import json
    with open(path, encoding="utf-8") as f:
        return json.load(f)
