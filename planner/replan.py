"""운영 중 취소·지연 대응(W07) — 확정된 하루 일정에 이벤트 한 건이 들어왔을 때 바뀔 일정을 '제안'으로 계산한다.

planner.reschedule 는 30일 실험용 재생 엔진이고, 이 모듈은 담당자가 실제로 등록한 이벤트 한 건을 다룬다.
일정 다시 맞추기·끼워 넣기 규칙은 같은 도구(_State·_fits_at·_try_assign·_occupancy)를 쓴다.

지키는 것 (PRD FR-29~32, AT-10)
  - 이벤트는 '접수 시각(received_min)'에 처음 알게 된 것으로 본다. 그 전으로 거슬러 바꾸지 않고,
    접수 시각보다 이른 출발은 만들지 않는다(미래 정보·과거 변경 금지).
  - 이미 시작했거나 인력이 이미 출발한 돌봄은 고정한다(진행 중 보호).
  - 진행 중인 돌봄의 취소는 자동으로 슬롯을 비우지 않는다. 현장 확인 뒤 담당자가 처리한다.
  - 시작 지연: 시간창을 뒤로 민다(서비스 길이·병원 예약 시각은 그대로). 맞출 수 없으면 다른 인력을 찾고, 그래도 안 되면
    '다시 배정 필요'로 보여준다. 0분으로 때우지 않는다.
  - 진행 중 연장(병원 체류·서비스가 길어짐): 지금 어르신 돌봄을 먼저 지키고, 뒤 일정만 다시 본다.
    연장이 휴게·근무 끝과 겹치면 자동으로 정하지 않고 담당자 판단으로 넘긴다.
  - 빈자리 채우기 제안은 한 건만(탐욕적, 최적 아님). 못 채우면 이유를 구분한다:
    대체 요청 없음 / 경로 불명 / 시간 충돌. (자료 확인 전 요청 수는 화면이 따로 보여준다.)
  - 결과는 검증기로 다시 확인한다. 위반이 있으면 적용할 수 없다.
  - 이 함수는 아무것도 저장하지 않는다. 적용은 담당자가 화면에서 누를 때 store 가 한다(자동 파견·메시지 없음).
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field

from planner.baseline import _State, _occupancy, _travel_map, _try_assign
from planner.reschedule import _fits_at

KIND_CANCEL = "cancel"
KIND_DELAY = "delay"
KIND_EXTEND = "extend"
KIND_KO = {KIND_CANCEL: "취소", KIND_DELAY: "시작 지연", KIND_EXTEND: "진행 중 연장"}

NO_CANDIDATE = "NO_CANDIDATE"
TIME_CONFLICT = "TIME_CONFLICT"
ROUTE_UNKNOWN_R = "ROUTE_UNKNOWN"
REFILL_REASON_KO = {NO_CANDIDATE: "대체할 요청 없음", TIME_CONFLICT: "남은 요청과 시간이 맞지 않음",
                    ROUTE_UNKNOWN_R: "이동 경로를 알 수 없음"}


@dataclass
class ReplanProposal:
    ok: bool
    message: str
    before: list[dict]
    after: list[dict]
    changes: list[dict] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    updated_requests: list[dict] = field(default_factory=list)   # 지연·연장으로 바뀐 요청 값
    refill_reason: str | None = None
    instance_after: dict | None = None
    violations: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def event_id(service_date: str, ev: dict) -> str:
    """같은 이벤트를 다시 보내면 같은 ID(AT-09 재전송 1회 반영)."""
    key = [service_date, ev["kind"], ev["request_id"], int(ev["occurred_min"]), int(ev["received_min"]),
           int(ev.get("minutes") or 0)]
    return "ev-" + hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:16]


def _hhmm(m: int) -> str:
    return f"{int(m) // 60:02d}:{int(m) % 60:02d}"


def _strip(a: dict) -> dict:
    return {"request_id": a["request_id"], "worker_id": a["worker_id"], "arrive_min": int(a["arrive_min"])}


def _split(worker: dict, jobs: list[dict], by_id: dict, travel: dict, t0: int,
           force_flex: set[str] = frozenset()) -> tuple[list[dict], list[dict]]:
    """시간순 일정 → (고정: 이미 시작했거나 출발한 일, 바꿀 수 있는 일)."""
    fixed, flex = [], []
    prev = worker["base_node"]
    for j in sorted(jobs, key=lambda a: (int(a["arrive_min"]), a["request_id"])):
        req = by_id[j["request_id"]]
        leg = travel.get((prev, req["home_node"]))
        depart = int(j["arrive_min"]) - (int(round(leg)) if leg is not None else 0)
        if not flex and j["request_id"] not in force_flex and (int(j["arrive_min"]) <= t0 or depart <= t0):
            fixed.append(j)
        else:
            flex.append(j)
        prev = req["home_node"]
    return fixed, flex


def _resequence(worker: dict, fixed: list[dict], flex: list[dict], by_id: dict, travel: dict, t0: int
                ) -> tuple[list[dict], list[str], list[str]]:
    """고정 일정은 그대로 두고, 나머지를 주어진 순서대로 다시 맞춘다. 바꿀 일은 접수 시각 이후에만 출발한다.

    → (유지된 일정, 시각이 바뀐 요청, 밀려난 요청). flex 항목의 arrive_min 이 None 이면 새로 끼워 넣는 요청.
    """
    st = _State(worker=worker, cursor=worker["base_node"], free=int(worker["shift_start_min"]))
    kept: list[dict] = []
    for j in fixed:
        req = by_id[j["request_id"]]
        t = int(j["arrive_min"])
        end, span = _occupancy(req, travel, t)
        leg = travel.get((st.cursor, req["home_node"]))
        st.work_min += (int(round(leg)) if leg is not None else 0) + span
        st.cursor, st.free = req["home_node"], int(end if end is not None else t)
        kept.append({"request_id": j["request_id"], "worker_id": worker["worker_id"], "arrive_min": t})
    st.free = max(st.free, t0)
    retimed, displaced = [], []
    for j in flex:
        req = by_id[j["request_id"]]
        orig = j.get("arrive_min")
        if orig is not None and _fits_at(st, req, travel, int(orig)):
            t = int(orig)
        else:
            t, _reason = _try_assign(st, req, travel)
            if t is None:
                displaced.append(j["request_id"])
                continue
            if orig is not None and t != int(orig):
                retimed.append(j["request_id"])
        end, span = _occupancy(req, travel, t)
        leg = travel.get((st.cursor, req["home_node"]))
        st.work_min += (int(round(leg)) if leg is not None else 0) + span
        st.cursor, st.free = req["home_node"], int(end)
        kept.append({"request_id": j["request_id"], "worker_id": worker["worker_id"], "arrive_min": int(t)})
    return kept, retimed, displaced


def _insert(worker: dict, jobs: list[dict], cand: dict, by_id: dict, travel: dict, t0: int
            ) -> list[dict] | None:
    """후보를 이 인력 일정의 바꿀 수 있는 부분에 끼워 본다. 다른 일정이 밀려나면 넣지 않는다."""
    if not set(cand.get("required_skills") or []).issubset(set(worker.get("skills") or [])):
        return None
    fixed, flex = _split(worker, jobs, by_id, travel, t0)
    for pos in range(len(flex) + 1):
        order = flex[:pos] + [{"request_id": cand["request_id"], "arrive_min": None}] + flex[pos:]
        kept, _ret, disp = _resequence(worker, fixed, order, by_id, travel, t0)
        if not disp and any(k["request_id"] == cand["request_id"] for k in kept):
            return kept
    return None


def plan_event(instance: dict, confirmed: list[dict], ev: dict, *, refill: bool = True) -> ReplanProposal:
    """확정 일정 + 이벤트 → 바뀐 일정 제안.

    ev: {kind: cancel|delay|extend, request_id, occurred_min, received_min, minutes, reason_code}
    """
    from verifier import verify

    travel = _travel_map(instance["travel"])
    by_id = {r["request_id"]: copy.deepcopy(r) for r in instance["requests"]}
    workers = {w["worker_id"]: w for w in instance["workers"]}
    before = sorted((_strip(a) for a in confirmed), key=lambda a: (a["worker_id"], a["arrive_min"], a["request_id"]))
    sched = [dict(a) for a in before]
    rid, kind = ev["request_id"], ev["kind"]
    t0, minutes = int(ev["received_min"]), int(ev.get("minutes") or 0)

    def blocked(msg: str) -> ReplanProposal:
        return ReplanProposal(ok=False, message=msg, before=before, after=before)

    if rid not in by_id:
        return blocked(f"{rid}: 이 날짜 요청 목록에 없는 요청입니다.")
    if int(ev["occurred_min"]) > t0:
        return blocked("실제 발생 시각이 접수 시각보다 늦을 수 없습니다.")
    if kind in (KIND_DELAY, KIND_EXTEND) and minutes <= 0:
        return blocked("늦어지는 시간(분)을 1분 이상 적어 주세요.")

    mine = next((a for a in sched if a["request_id"] == rid), None)
    wid = mine["worker_id"] if mine else None
    started = mine is not None and int(mine["arrive_min"]) <= t0
    affected: set[str] = set()
    cancelled: list[str] = []
    updated: list[str] = []
    notes: list[str] = []
    should_refill = False

    if kind == KIND_CANCEL:
        if started:
            return blocked(f"{rid}는 {_hhmm(mine['arrive_min'])}에 이미 시작한 진행 중인 돌봄이라 자동으로 비우지 않습니다. "
                           "현장을 확인한 뒤 담당자가 중단 처리해 주세요.")
        cancelled = [rid]
        if mine:
            fixed, _ = _split(workers[wid], [a for a in sched if a["worker_id"] == wid], by_id, travel, t0)
            if any(a["request_id"] == rid for a in fixed):
                notes.append("인력이 이미 출발해 헛걸음 이동이 생깁니다(기록만 하고 막지 못합니다).")
            sched.remove(mine)
            affected.add(wid)
            should_refill = True
    elif kind == KIND_DELAY:
        if started:
            return blocked(f"{rid}는 이미 시작한 돌봄입니다. 길어지는 경우는 '진행 중 연장'으로 등록해 주세요.")
        r = by_id[rid]
        r["earliest_start_min"] = int(r["earliest_start_min"]) + minutes
        r["latest_start_min"] = int(r["latest_start_min"]) + minutes
        updated = [rid]
        if mine:
            affected.add(wid)
    elif kind == KIND_EXTEND:
        if not started:
            return blocked(f"{rid}는 아직 시작하지 않은 돌봄입니다. '시작 지연'으로 등록해 주세요.")
        r = by_id[rid]
        if r["service_type"] == "hospital_escort":
            r["hospital_stay_min"] = int(r["hospital_stay_min"]) + minutes
        else:
            r["service_minutes"] = int(r["service_minutes"]) + minutes
        updated = [rid]
        w = workers[wid]
        end, _span = _occupancy(r, travel, int(mine["arrive_min"]))
        brk_s, brk_e = int(w["break_start_min"]), int(w["break_start_min"]) + int(w["break_minutes"])
        if end is not None and int(mine["arrive_min"]) < brk_e and brk_s < end:
            return blocked(f"연장하면 {rid} 돌봄이 {_hhmm(end)}에 끝나 {wid}의 휴게({_hhmm(brk_s)}~{_hhmm(brk_e)})와 겹칩니다. "
                           "지금 어르신 돌봄을 먼저 지키되, 휴게를 어떻게 옮길지는 담당자가 정해 주세요.")
        if end is not None and end > int(w["shift_end_min"]):
            return blocked(f"연장하면 {rid} 돌봄이 {_hhmm(end)}에 끝나 {wid}의 근무 끝({_hhmm(w['shift_end_min'])})을 넘깁니다. "
                           "초과근무 여부는 담당자가 정해 주세요.")
        affected.add(wid)
    else:
        return blocked(f"알 수 없는 이벤트 종류: {kind}")

    # 영향받는 인력의 일정을 다시 맞춘다(고정 일정은 그대로)
    lost: list[str] = []
    for w_id in sorted(affected):
        jobs = [a for a in sched if a["worker_id"] == w_id]
        fixed, flex = _split(workers[w_id], jobs, by_id, travel, t0,
                             force_flex={rid} if kind == KIND_DELAY else frozenset())
        kept, _ret, disp = _resequence(workers[w_id], fixed, flex, by_id, travel, t0)
        sched = [a for a in sched if a["worker_id"] != w_id] + kept
        lost += disp

    # 밀려난 요청은 다른 인력에게 넘길 수 있는지 본다(원래 인력 제외)
    for lr in list(lost):
        orig_w = next(a["worker_id"] for a in before if a["request_id"] == lr)
        for w_id in sorted(w for w in workers if w != orig_w):
            kept = _insert(workers[w_id], [a for a in sched if a["worker_id"] == w_id], by_id[lr], by_id, travel, t0)
            if kept is not None:
                sched = [a for a in sched if a["worker_id"] != w_id] + kept
                lost.remove(lr)
                if lr == rid:
                    should_refill = True     # 지연된 요청이 다른 인력으로 가서 원래 인력에 자리가 났다
                break
    if kind == KIND_DELAY and rid in lost:
        should_refill = True                 # 맞출 수 없어 빠진 자리

    # 빈자리 채우기 제안(한 건)
    refill_reason = None
    if refill and should_refill:
        assigned = {a["request_id"] for a in sched}
        cands = [r for r in by_id.values()
                 if r["request_id"] not in assigned and r["request_id"] not in cancelled
                 and r["request_id"] not in lost and r["request_id"] != rid]
        cands.sort(key=lambda r: (-int(r.get("priority") or 1), int(r["latest_start_min"]), r["request_id"]))
        order = ([wid] if wid else []) + sorted(w for w in workers if w != wid)
        done = False
        for c in cands:
            for w_id in order:
                kept = _insert(workers[w_id], [a for a in sched if a["worker_id"] == w_id], c, by_id, travel, t0)
                if kept is not None:
                    sched = [a for a in sched if a["worker_id"] != w_id] + kept
                    done = True
                    break
            if done:
                break
        if not done:
            if not cands:
                refill_reason = NO_CANDIDATE
            elif not any(any(k[1] == c["home_node"] for k in travel) for c in cands):
                refill_reason = ROUTE_UNKNOWN_R
            else:
                refill_reason = TIME_CONFLICT

    after = sorted((_strip(a) for a in sched), key=lambda a: (a["worker_id"], a["arrive_min"], a["request_id"]))
    instance_after = {**instance, "requests": [r for r in by_id.values() if r["request_id"] not in cancelled]}
    rep = verify(instance_after, after) if after else None
    violations = [v.as_dict() for v in rep.violations] if rep is not None else []

    # 바뀌는 점(FR-31)
    b_map = {a["request_id"]: a for a in before}
    a_map = {a["request_id"]: a for a in after}
    changes = []
    for r_id in sorted(set(b_map) | set(a_map) | set(cancelled)):
        b, a = b_map.get(r_id), a_map.get(r_id)
        if r_id in cancelled:
            k = "취소"
        elif b and not a:
            k = "맞출 수 없음(다시 배정 필요)" if r_id == rid else "밀려남(다시 배정 필요)"
        elif a and not b:
            k = "빈자리 채움"
        elif b["worker_id"] != a["worker_id"]:
            k = "다른 인력으로"
        elif b["arrive_min"] != a["arrive_min"]:
            k = "시각 변경"
        elif r_id == rid and kind == KIND_EXTEND:
            k = "연장(지금 돌봄 보호)"
        else:
            continue
        changes.append({"request_id": r_id, "beneficiary_id": by_id[r_id]["beneficiary_id"], "kind": k,
                        "worker_before": b["worker_id"] if b else None,
                        "arrive_before": b["arrive_min"] if b else None,
                        "worker_after": a["worker_id"] if a else None,
                        "arrive_after": a["arrive_min"] if a else None})
    ok = not violations
    msg = (f"{KIND_KO[kind]} 반영 시 바뀌는 일정 {len(changes)}건을 계산했습니다. 담당자가 확인하고 적용해야 바뀝니다."
           if ok else "다시 맞춘 일정이 규칙 검사를 통과하지 못해 적용할 수 없습니다.")
    return ReplanProposal(ok=ok, message=msg, before=before, after=after, changes=changes, cancelled=cancelled,
                          updated_requests=[by_id[u] for u in updated], refill_reason=refill_reason,
                          instance_after=instance_after, violations=violations, notes=notes)
