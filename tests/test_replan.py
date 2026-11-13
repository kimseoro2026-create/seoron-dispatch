"""운영 중 취소·지연 대응(W07) — 확정 일정에 이벤트 한 건을 넣어 바뀔 일정을 '제안'으로 계산한다.

PRD FR-29(발생·접수 시각), FR-30(진행 중 보호, 해제된 시간만 재계산, 예측으로 해제 금지),
FR-31(영향 표시), FR-32(대체 요청 없음/자료 부족/경로 불명/시간 충돌 구분), AT-10(진행 중 지연 → 현재 보호·후속 재검토).
"""
from __future__ import annotations

import itertools

import pytest

from planner.replan import (KIND_CANCEL, KIND_DELAY, KIND_EXTEND, NO_CANDIDATE, ROUTE_UNKNOWN_R,
                            TIME_CONFLICT, event_id, plan_event)
from verifier import verify

NODES = ["BASE_W01", "BASE_W02", "H1", "H2", "H3", "H4"]


def _w(wid, brk=780):
    return {"worker_id": wid, "skills": ["household_support"], "base_node": f"BASE_{wid}",
            "shift_start_min": 540, "shift_end_min": 1080, "break_start_min": brk, "break_minutes": 60,
            "max_work_minutes": 480}


def _r(rid, home, lo, hi, svc):
    return {"request_id": rid, "beneficiary_id": "B" + rid, "service_type": "household_support",
            "service_date": "2026-11-02", "home_node": home, "earliest_start_min": lo, "latest_start_min": hi,
            "service_minutes": svc, "required_skills": ["household_support"], "priority": 1,
            "requested_at": "2026-10-30T10:00"}


def _inst(extra=(), w02_break=780):
    reqs = [_r("R1", "H1", 600, 620, 60), _r("R2", "H2", 700, 760, 60), _r("R3", "H3", 700, 900, 45)]
    reqs += list(extra)
    travel = [{"from_node": a, "to_node": b, "minutes": 10} for a, b in itertools.permutations(NODES, 2)]
    return {"workers": [_w("W01"), _w("W02", w02_break)], "requests": reqs, "travel": travel}


BASE = [{"request_id": "R1", "worker_id": "W01", "arrive_min": 610},
        {"request_id": "R2", "worker_id": "W01", "arrive_min": 700}]


def _busy_w02():
    """W02 를 하루 종일 바쁘게(다른 인력으로 넘기기 불가)."""
    return [_r("R4", "H4", 560, 560, 210), _r("R5", "H4", 850, 850, 200)], \
           [{"request_id": "R4", "worker_id": "W02", "arrive_min": 560},
            {"request_id": "R5", "worker_id": "W02", "arrive_min": 850}]


def _ev(kind, rid, received, minutes=0, occurred=None):
    return {"kind": kind, "request_id": rid, "occurred_min": occurred if occurred is not None else received,
            "received_min": received, "minutes": minutes, "reason_code": "대상자 요청"}


# ── 취소 ──────────────────────────────────────────────────────────────────
def test_cancel_future_job_frees_slot_and_suggests_refill():
    inst = _inst()
    p = plan_event(inst, BASE, _ev(KIND_CANCEL, "R2", 650))
    assert p.ok, p.message
    after = {a["request_id"]: a for a in p.after}
    assert "R2" not in after and after["R3"]["worker_id"] in ("W01", "W02")
    assert after["R1"]["arrive_min"] == 610                     # 이미 진행 중인 R1 은 그대로
    kinds = {c["request_id"]: c["kind"] for c in p.changes}
    assert kinds["R2"] == "취소" and kinds["R3"] == "빈자리 채움"
    assert not verify(p.instance_after, p.after).violations


def test_refill_never_departs_before_event_received():
    inst = _inst()
    p = plan_event(inst, BASE, _ev(KIND_CANCEL, "R2", 695))
    r3 = next(a for a in p.after if a["request_id"] == "R3")
    assert r3["arrive_min"] - 10 >= 695


def test_cancel_in_progress_is_blocked_not_auto_released():
    p = plan_event(_inst(), BASE, _ev(KIND_CANCEL, "R1", 615))
    assert not p.ok and "진행 중" in p.message and p.after == BASE


def test_cancel_without_refill_option():
    p = plan_event(_inst(), BASE, _ev(KIND_CANCEL, "R2", 650), refill=False)
    assert p.ok and {a["request_id"] for a in p.after} == {"R1"}


def test_refill_reasons_are_distinguished():
    inst = _inst()
    inst["requests"] = [r for r in inst["requests"] if r["request_id"] != "R3"]
    p = plan_event(inst, BASE, _ev(KIND_CANCEL, "R2", 650))
    assert p.ok and p.refill_reason == NO_CANDIDATE
    late = _inst()
    late["requests"][2] = _r("R3", "H3", 600, 640, 45)           # 이미 시간창이 지난 후보
    p = plan_event(late, BASE, _ev(KIND_CANCEL, "R2", 650))
    assert p.refill_reason == TIME_CONFLICT
    noroute = _inst()
    noroute["travel"] = [t for t in noroute["travel"] if t["to_node"] != "H3"]
    p = plan_event(noroute, BASE, _ev(KIND_CANCEL, "R2", 650))
    assert p.refill_reason == ROUTE_UNKNOWN_R


def test_cancel_unconfirmed_request_changes_no_schedule():
    p = plan_event(_inst(), BASE, _ev(KIND_CANCEL, "R3", 650))
    assert p.ok and p.after == BASE and p.cancelled == ["R3"]


# ── 시작 지연 ─────────────────────────────────────────────────────────────
def test_small_delay_is_retimed():
    p = plan_event(_inst(), BASE, _ev(KIND_DELAY, "R2", 650, minutes=10))
    assert p.ok
    r2 = next(a for a in p.after if a["request_id"] == "R2")
    assert r2["arrive_min"] == 710
    assert next(c for c in p.changes if c["request_id"] == "R2")["kind"] == "시각 변경"
    assert not verify(p.instance_after, p.after).violations


def test_delay_that_cannot_fit_is_lost_and_shown():
    extra, w02 = _busy_w02()
    p = plan_event(_inst(extra), BASE + w02, _ev(KIND_DELAY, "R2", 650, minutes=30))
    assert p.ok
    assert "R2" not in {a["request_id"] for a in p.after}
    assert next(c for c in p.changes if c["request_id"] == "R2")["kind"] == "맞출 수 없음(다시 배정 필요)"


def test_delay_moves_to_other_worker_when_possible():
    p = plan_event(_inst(w02_break=840), BASE, _ev(KIND_DELAY, "R2", 650, minutes=30))
    r2 = next(a for a in p.after if a["request_id"] == "R2")
    assert r2["worker_id"] == "W02"
    assert next(c for c in p.changes if c["request_id"] == "R2")["kind"] == "다른 인력으로"


def test_delay_of_started_job_points_to_extension():
    p = plan_event(_inst(), BASE, _ev(KIND_DELAY, "R1", 615, minutes=10))
    assert not p.ok and "연장" in p.message


# ── 진행 중 연장 (AT-10) ───────────────────────────────────────────────────
def test_extension_protects_current_and_reviews_followups():
    extra, w02 = _busy_w02()
    p = plan_event(_inst(extra), BASE + w02, _ev(KIND_EXTEND, "R1", 640, minutes=60))
    assert p.ok, p.message
    after = {a["request_id"]: a for a in p.after}
    assert after["R1"]["arrive_min"] == 610 and after["R1"]["worker_id"] == "W01"   # 현재 어르신 보호
    assert "R2" not in after                                                        # 후속 일정은 위험 표시
    assert next(c for c in p.changes if c["request_id"] == "R2")["kind"] == "밀려남(다시 배정 필요)"
    assert not verify(p.instance_after, p.after).violations


def test_extension_followup_moves_if_other_worker_free():
    p = plan_event(_inst(), BASE, _ev(KIND_EXTEND, "R1", 640, minutes=60))
    after = {a["request_id"]: a for a in p.after}
    assert after["R2"]["worker_id"] == "W02"


def test_extension_needs_started_job():
    p = plan_event(_inst(), BASE, _ev(KIND_EXTEND, "R2", 640, minutes=10))
    assert not p.ok and "시작 지연" in p.message


def test_extension_into_break_is_blocked_for_staff_decision():
    p = plan_event(_inst(), BASE, _ev(KIND_EXTEND, "R1", 640, minutes=150))
    assert not p.ok and "휴게" in p.message


# ── 기타 ──────────────────────────────────────────────────────────────────
def test_event_id_is_stable_for_resend():                         # AT-09
    e = _ev(KIND_CANCEL, "R2", 650)
    assert event_id("2026-11-02", e) == event_id("2026-11-02", dict(e))
    assert event_id("2026-11-02", e) != event_id("2026-11-02", dict(e, received_min=651))


def test_unknown_request_is_blocked():
    p = plan_event(_inst(), BASE, _ev(KIND_CANCEL, "R9", 650))
    assert not p.ok


@pytest.mark.parametrize("kind", [KIND_DELAY, KIND_EXTEND])
def test_minutes_must_be_positive(kind):
    p = plan_event(_inst(), BASE, _ev(kind, "R2" if kind == KIND_DELAY else "R1", 650 if kind == KIND_DELAY else 640,
                                      minutes=0))
    assert not p.ok
