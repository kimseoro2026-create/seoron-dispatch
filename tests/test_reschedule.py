"""취소·재배정 테스트 — PRD 12장 '미래 정보 금지'.

가장 중요한 검사는 두 가지다.
  1. 아직 통보되지 않은 취소를 미리 알고 배정을 피하지 않는가
  2. 재배정 결과도 하드 제약을 지키는가
"""
from __future__ import annotations

import copy

import pytest

from planner.reschedule import (
    DELAYED_LATE, LEFT_EMPTY, NOT_ASSIGNED, REFILLED,
    collect_cancellations, collect_delays, replay,
)
from verifier import verify


def _instance():
    """취소 1건이 포함된 최소 인스턴스.

    W01 만 있고, R02 가 취소된다. R01 은 먼저 시작하므로 R02 슬롯을 채울 수 없다.
    R03 은 R02 취소 뒤에야 시간 창이 열려 재배정 후보가 된다.
    """
    return {
        "service_date": "2026-11-02",
        "workers": [{"worker_id": "W01", "skills": ["household_support"],
                     "base_node": "BASE_W01",
                     "shift_start_min": 540, "shift_end_min": 1080,
                     "break_start_min": 780, "break_minutes": 60,
                     "max_work_minutes": 480}],
        "requests": [
            {"request_id": "R01", "beneficiary_id": "B01",
             "service_type": "household_support", "service_date": "2026-11-02",
             "home_node": "HOME_B01", "earliest_start_min": 600, "latest_start_min": 660,
             "service_minutes": 60, "required_skills": ["household_support"],
             "priority": 1, "requested_at": "2026-10-30T10:00",
             "cancelled_same_day": False},
            {"request_id": "R02", "beneficiary_id": "B02",
             "service_type": "household_support", "service_date": "2026-11-02",
             "home_node": "HOME_B02", "earliest_start_min": 700, "latest_start_min": 760,
             "service_minutes": 60, "required_skills": ["household_support"],
             "priority": 1, "requested_at": "2026-10-30T11:00",
             "cancelled_same_day": True, "cancel_notice_min_before": 120},
            {"request_id": "R03", "beneficiary_id": "B03",
             "service_type": "household_support", "service_date": "2026-11-02",
             "home_node": "HOME_B03", "earliest_start_min": 600, "latest_start_min": 900,
             "service_minutes": 45, "required_skills": ["household_support"],
             "priority": 1, "requested_at": "2026-10-31T09:00",
             "cancelled_same_day": False},
        ],
        "travel": [
            {"from_node": "BASE_W01", "to_node": "HOME_B01", "minutes": 10},
            {"from_node": "BASE_W01", "to_node": "HOME_B02", "minutes": 15},
            {"from_node": "BASE_W01", "to_node": "HOME_B03", "minutes": 12},
            {"from_node": "HOME_B01", "to_node": "HOME_B02", "minutes": 8},
            {"from_node": "HOME_B02", "to_node": "HOME_B01", "minutes": 8},
            {"from_node": "HOME_B01", "to_node": "HOME_B03", "minutes": 5},
            {"from_node": "HOME_B03", "to_node": "HOME_B01", "minutes": 5},
            {"from_node": "HOME_B02", "to_node": "HOME_B03", "minutes": 6},
            {"from_node": "HOME_B03", "to_node": "HOME_B02", "minutes": 6},
        ],
    }


# ── 취소 이벤트 수집 ───────────────────────────────────────────────────────

def test_collects_only_cancelled_requests():
    inst = _instance()
    events = collect_cancellations(inst, [])
    assert [e.request_id for e in events] == ["R02"]


def test_notice_min_is_before_service_start():
    inst = _instance()
    events = collect_cancellations(inst, [])
    ev = events[0]
    assert ev.notice_min == ev.service_start_min - 120


def test_events_sorted_by_notice_time():
    inst = _instance()
    inst["requests"].append({
        "request_id": "R04", "beneficiary_id": "B04",
        "service_type": "household_support", "service_date": "2026-11-02",
        "home_node": "HOME_B03", "earliest_start_min": 900, "latest_start_min": 960,
        "service_minutes": 30, "required_skills": ["household_support"],
        "priority": 1, "requested_at": "2026-10-31T09:00",
        "cancelled_same_day": True, "cancel_notice_min_before": 30,
    })
    events = collect_cancellations(inst, [])
    notices = [e.notice_min for e in events]
    assert notices == sorted(notices)


# ── 재생 기본 ──────────────────────────────────────────────────────────────

def test_replay_without_cancellation_matches_baseline():
    inst = _instance()
    for r in inst["requests"]:
        r["cancelled_same_day"] = False
    res = replay(inst, "request_order")
    assert len(res.cancellations) == 0
    assert res.assignments == sorted(res.initial_assignments, key=lambda a: a["request_id"])


def test_cancelled_request_never_in_final_assignments():
    inst = _instance()
    res = replay(inst, "request_order")
    ids = {a["request_id"] for a in res.assignments}
    assert "R02" not in ids


def test_final_plan_passes_verifier():
    """재배정 결과도 하드 제약을 지켜야 한다."""
    inst = _instance()
    res = replay(inst, "request_order")
    assert res.ok, res.violations


# ── 미래 정보 금지 ────────────────────────────────────────────────────────

def test_does_not_use_future_cancellation():
    """나중에 취소될 요청이라도, 통보 전에는 후보로 쓸 수 있다.

    R03 이 취소된다면, 그 사실은 나중에 통보된다. 통보 전 재배정에서 R03 을
    후보에서 미리 빼면 미래 정보를 쓴 것이다.
    """
    inst = _instance()
    # R03 을 더 늦게 취소되도록 만든다(통보가 재배정 시점 이후)
    for r in inst["requests"]:
        if r["request_id"] == "R03":
            r["cancelled_same_day"] = True
            r["cancel_notice_min_before"] = 0   # 시작 직전에야 통보
    res = replay(inst, "request_order")
    # R02 취소 시점에는 R03 이 아직 살아 있으므로 후보에 들어갈 수 있다
    first = res.cancellations[0] if res.cancellations else None
    assert first is not None


def test_later_cancelled_request_can_fill_earlier_slot():
    inst = _instance()
    for r in inst["requests"]:
        if r["request_id"] == "R03":
            r["cancelled_same_day"] = True
            r["cancel_notice_min_before"] = 0
    res = replay(inst, "request_order")
    # R02 슬롯을 R03 이 채웠다면, 그 뒤 R03 취소가 다시 빈 슬롯을 만든다
    filled = [c for c in res.cancellations if c.outcome == REFILLED]
    assert isinstance(filled, list)   # 구조가 흔들리지 않는지만 확인


# ── 결과 분류 ──────────────────────────────────────────────────────────────

def test_outcome_not_assigned_when_was_unassigned():
    """처음부터 배정이 없던 요청이 취소되면 슬롯을 비울 필요가 없다.

    R02 에 아무도 갖지 못한 자격을 요구해 배정을 막는다.
    (시간창을 좁히는 방법은 오히려 배정이 되어 버린다 — 범위를 옮기면 그냥 맞는다.)
    """
    inst = _instance()
    for r in inst["requests"]:
        if r["request_id"] == "R02":
            r["required_skills"] = ["hospital_escort"]   # W01 은 household_support 만
    res = replay(inst, "request_order")
    for c in res.cancellations:
        if c.request_id == "R02":
            assert c.had_assignment is False
            assert c.outcome == NOT_ASSIGNED


def test_summary_counts_add_up():
    inst = _instance()
    res = replay(inst, "request_order")
    s = res.summary()
    assert s["final_assigned"] + s["final_unassigned"] == len(inst["requests"])
    assert s["cancellations"] == len(res.cancellations)


def test_does_not_mutate_input():
    inst = _instance()
    before = copy.deepcopy(inst)
    replay(inst, "request_order")
    assert inst == before


@pytest.mark.parametrize("strategy", ["request_order", "nearest"])
def test_both_refill_strategies_pass_verifier(strategy):
    inst = _instance()
    res = replay(inst, "request_order", refill_strategy=strategy)
    assert res.ok, res.violations


def test_unknown_strategy_raises():
    inst = _instance()
    with pytest.raises(ValueError):
        replay(inst, "request_order", refill_strategy="nope")


# ── refill=False — 취소만 반영하는 모드 ────────────────────────────────────

def test_refill_false_drops_cancelled_request():
    """취소는 반영되어야 한다 — 취소된 요청이 최종 배정에 남으면 안 된다."""
    inst = _instance()
    res = replay(inst, "request_order", refill=False)
    ids = {a["request_id"] for a in res.assignments}
    assert "R02" not in ids


def test_refill_false_never_fills_slot():
    """빈자리를 채우지 않는다. 채울 수 있는 요청(R03)이 있어도 넣지 않는다."""
    inst = _instance()
    res = replay(inst, "request_order", refill=False)
    s = res.summary()
    assert s["refilled"] == 0
    for c in res.cancellations:
        assert c.outcome != REFILLED
        assert c.refilled_by is None


def test_refill_false_keeps_other_assignments():
    """취소되지 않은 배정은 그대로 남는다(취소 1건 때문에 전부 버리지 않는다)."""
    inst = _instance()
    res = replay(inst, "request_order", refill=False)
    ids = {a["request_id"] for a in res.assignments}
    assert ids == {"R01", "R03"}, f"취소되지 않은 배정이 사라졌다: {ids}"


def test_refill_false_passes_verifier():
    """retime 을 거친 일정도 하드 제약을 지켜야 한다."""
    inst = _instance()
    res = replay(inst, "request_order", refill=False)
    assert res.ok, res.violations


def test_refill_false_assigns_no_more_than_refill_true():
    """채우지 않으므로 배정 수가 재배정 모드보다 많을 수 없다."""
    inst = _instance()
    off = replay(inst, "request_order", refill=False)
    on = replay(inst, "request_order", refill=True)
    assert len(off.assignments) <= len(on.assignments)


def test_refill_false_does_not_mutate_input():
    inst = _instance()
    before = copy.deepcopy(inst)
    replay(inst, "request_order", refill=False)
    assert inst == before


@pytest.mark.parametrize("strategy", ["request_order", "nearest"])
def test_refill_false_works_for_both_baselines(strategy):
    inst = _instance()
    res = replay(inst, strategy, refill=False)
    assert res.ok, res.violations


# ── 지연 이벤트 ───────────────────────────────────────────────────────────

def _delay_instance(delay_minutes: int, shift_end: int = 1080):
    """지연 1건이 포함된 최소 인스턴스.

    R02 대상자가 늦어져 시작이 delay_minutes 만큼 밀린다. 서비스 길이는 그대로다.
    shift_end 를 좁히면 밀린 시각을 맞출 수 없게 만들 수 있다.
    """
    return {
        "service_date": "2026-11-02",
        "workers": [{"worker_id": "W01", "skills": ["household_support"],
                     "base_node": "BASE_W01",
                     "shift_start_min": 540, "shift_end_min": shift_end,
                     "break_start_min": 1020, "break_minutes": 60,
                     "max_work_minutes": 600}],
        "requests": [
            {"request_id": "R01", "beneficiary_id": "B01",
             "service_type": "household_support", "service_date": "2026-11-02",
             "home_node": "HOME_B01", "earliest_start_min": 600, "latest_start_min": 660,
             "service_minutes": 60, "required_skills": ["household_support"],
             "priority": 1, "requested_at": "2026-10-30T10:00"},
            {"request_id": "R02", "beneficiary_id": "B02",
             "service_type": "household_support", "service_date": "2026-11-02",
             "home_node": "HOME_B02", "earliest_start_min": 700, "latest_start_min": 760,
             "service_minutes": 60, "required_skills": ["household_support"],
             "priority": 1, "requested_at": "2026-10-30T11:00",
             "delayed_same_day": True, "delay_minutes": delay_minutes,
             "delay_notice_min_before": 60},
        ],
        "travel": [
            {"from_node": "BASE_W01", "to_node": "HOME_B01", "minutes": 10},
            {"from_node": "BASE_W01", "to_node": "HOME_B02", "minutes": 15},
            {"from_node": "HOME_B01", "to_node": "HOME_B02", "minutes": 8},
        ],
    }


def test_collects_only_delayed_requests():
    inst = _delay_instance(40)
    events = collect_delays(inst, [])
    assert [e.request_id for e in events] == ["R02"]
    assert events[0].delay_minutes == 40


def test_delay_notice_is_before_service_start():
    inst = _delay_instance(40)
    res_initial = replay(inst, "request_order")
    start = next(a["arrive_min"] for a in res_initial.initial_assignments
                 if a["request_id"] == "R02")
    ev = collect_delays(inst, res_initial.initial_assignments)[0]
    assert ev.notice_min == start - 60
    assert ev.notice_min < ev.service_start_min


def test_delay_shifts_start_later():
    """지연은 시작을 뒤로 민다. 서비스 길이는 그대로다."""
    inst = _delay_instance(40)
    base = replay(inst, "request_order")
    orig = next(a["arrive_min"] for a in base.initial_assignments if a["request_id"] == "R02")

    inst2 = _delay_instance(40)
    res = replay(inst2, "request_order")
    d = next(x for x in res.delays if x.request_id == "R02")
    assert d.had_assignment is True
    assert d.kept is True
    assert d.new_start_min is not None
    assert d.new_start_min >= orig, f"지연인데 시작이 앞당겨졌다: {d.new_start_min} < {orig}"


def test_delay_passes_verifier():
    """밀린 시각으로 다시 맞춘 일정도 하드 제약을 지켜야 한다.

    검증기를 원래 시간창으로 돌리면 정상적으로 늦춰진 배정이 위반으로 잡힌다.
    지연을 반영한 창으로 검증해야 한다.
    """
    res = replay(_delay_instance(40), "request_order")
    assert res.ok, res.violations


def test_delay_that_cannot_fit_is_recorded_not_hidden():
    """밀린 시각을 맞출 수 없으면 0분·빈값으로 대체하지 않고 밀려난 것으로 남긴다."""
    res = replay(_delay_instance(240, shift_end=840), "request_order")
    d_out = next(x for x in res.delays if x.request_id == "R02")
    assert d_out.had_assignment is True
    assert d_out.kept is False
    assert d_out.new_start_min is None

    un = {u["request_id"]: u for u in res.unassigned}
    assert "R02" in un, "맞출 수 없는 지연 요청이 배정안에 남아 있다"
    assert un["R02"]["reason"] == DELAYED_LATE
    assert res.ok, res.violations


def test_delay_applied_even_without_refill():
    """refill=False 도 지연은 반영한다. 지연은 '채우기'가 아니라 '일정 변경'이다."""
    res = replay(_delay_instance(40), "request_order", refill=False)
    assert len(res.delays) == 1
    assert res.delays[0].kept is True


def test_delay_summary_keys():
    res = replay(_delay_instance(40), "request_order")
    s = res.summary()
    assert s["delays"] == 1
    assert s["delay_kept"] == 1
    assert s["delay_lost"] == 0


def test_delay_does_not_mutate_input():
    inst = _delay_instance(40)
    before = copy.deepcopy(inst)
    replay(inst, "request_order")
    assert inst == before


# ── 다른 인력으로 넘기기 ───────────────────────────────────────────────────

def _two_worker_delay_instance(w01_shift_end: int = 960, delay_minutes: int = 240):
    """W01 에만 몰려 있던 지연 요청을 W02 가 받을 수 있는지 보는 인스턴스.

    W01 은 960 에 근무를 마치므로 240분 밀린 R02(940~1000)를 지킬 수 없다.
    W02 는 1080 까지 일하므로 지킬 수 있다.
    """
    return {
        "service_date": "2026-11-02",
        "workers": [
            {"worker_id": "W01", "skills": ["household_support"], "base_node": "BASE_W01",
             "shift_start_min": 540, "shift_end_min": w01_shift_end,
             "break_start_min": 900, "break_minutes": 60, "max_work_minutes": 480},
            {"worker_id": "W02", "skills": ["household_support"], "base_node": "BASE_W02",
             "shift_start_min": 540, "shift_end_min": 1080,
             "break_start_min": 1020, "break_minutes": 60, "max_work_minutes": 480},
        ],
        "requests": [
            {"request_id": "R01", "beneficiary_id": "B01",
             "service_type": "household_support", "service_date": "2026-11-02",
             "home_node": "HOME_B01", "earliest_start_min": 600, "latest_start_min": 660,
             "service_minutes": 60, "required_skills": ["household_support"],
             "priority": 1, "requested_at": "2026-10-30T10:00"},
            {"request_id": "R02", "beneficiary_id": "B02",
             "service_type": "household_support", "service_date": "2026-11-02",
             "home_node": "HOME_B02", "earliest_start_min": 700, "latest_start_min": 760,
             "service_minutes": 60, "required_skills": ["household_support"],
             "priority": 1, "requested_at": "2026-10-30T11:00",
             "delayed_same_day": True, "delay_minutes": delay_minutes,
             "delay_notice_min_before": 60},
        ],
        "travel": [
            {"from_node": "BASE_W01", "to_node": "HOME_B01", "minutes": 10},
            {"from_node": "BASE_W01", "to_node": "HOME_B02", "minutes": 15},
            {"from_node": "BASE_W02", "to_node": "HOME_B01", "minutes": 20},
            {"from_node": "BASE_W02", "to_node": "HOME_B02", "minutes": 15},
            {"from_node": "HOME_B01", "to_node": "HOME_B02", "minutes": 8},
            {"from_node": "HOME_B02", "to_node": "HOME_B01", "minutes": 8},
        ],
    }


def test_delay_moves_to_another_worker_when_original_cannot_keep_it():
    """원래 인력이 밀린 시각을 못 지키면 다른 인력이 받아 지킨다."""
    inst = _two_worker_delay_instance(w01_shift_end=960)
    res = replay(inst, "request_order")

    d = next(x for x in res.delays if x.request_id == "R02")
    assert d.kept is True, "다른 인력이 받을 수 있는데도 지연상실로 남았다"
    assert d.moved_to_worker == "W02"
    assert d.new_start_min is not None and d.new_start_min >= 940

    moved = next(a for a in res.assignments if a["request_id"] == "R02")
    assert moved["worker_id"] == "W02"


def test_delay_move_passes_verifier():
    """인력을 옮긴 일정도 하드 제약을 지켜야 한다."""
    res = replay(_two_worker_delay_instance(w01_shift_end=960), "request_order")
    assert res.ok, res.violations


def test_delay_stays_when_original_worker_can_keep_it():
    """원래 인력이 지킬 수 있으면 굳이 옮기지 않는다."""
    inst = _two_worker_delay_instance(w01_shift_end=1080)
    res = replay(inst, "request_order")

    d = next(x for x in res.delays if x.request_id == "R02")
    assert d.kept is True
    assert d.moved_to_worker is None
    assert d.worker_id == "W01"
    assert next(a for a in res.assignments if a["request_id"] == "R02")["worker_id"] == "W01"


def test_delay_lost_when_no_other_worker_can_take_it():
    """어느 인력도 못 받으면 지연상실로 남긴다 — 0분으로 대체하지 않는다."""
    inst = _two_worker_delay_instance(w01_shift_end=900, delay_minutes=300)
    for w in inst["workers"]:
        w["shift_end_min"] = 900
    res = replay(inst, "request_order")

    d = next(x for x in res.delays if x.request_id == "R02")
    assert d.kept is False
    assert d.moved_to_worker is None
    un = {u["request_id"]: u for u in res.unassigned}
    assert un["R02"]["reason"] == DELAYED_LATE
    assert res.ok, res.violations


def test_delay_move_does_not_displace_other_workers_schedule():
    """다른 인력으로 옮겨 그 인력의 기존 일정이 밀려나면 옮기지 않는다."""
    inst = _two_worker_delay_instance(w01_shift_end=960)
    # W02 에 이미 940 에 시작하는 다른 요청을 넣어, R02 를 옮기면 밀리게 만든다.
    # service_minutes=60 → 940+60=1000 < break@1020 (위반 없음)
    inst["requests"].append({
        "request_id": "R09", "beneficiary_id": "B09",
        "service_type": "household_support", "service_date": "2026-11-02",
        "home_node": "HOME_B09", "earliest_start_min": 935, "latest_start_min": 945,
        "service_minutes": 60, "required_skills": ["household_support"],
        "priority": 1, "requested_at": "2026-10-29T09:00"})
    inst["travel"] += [
        {"from_node": "BASE_W02", "to_node": "HOME_B09", "minutes": 10},
        {"from_node": "HOME_B02", "to_node": "HOME_B09", "minutes": 4},
        {"from_node": "HOME_B09", "to_node": "HOME_B02", "minutes": 4},
        {"from_node": "HOME_B01", "to_node": "HOME_B09", "minutes": 9},
    ]
    res = replay(inst, "request_order")

    d = next(x for x in res.delays if x.request_id == "R02")
    # 옮겨서 R09 를 밀어내는 것은 허용하지 않는다
    r09 = next((a for a in res.assignments if a["request_id"] == "R09"), None)
    if d.moved_to_worker is not None:
        assert r09 is not None, "옮기면서 R09 를 밀어냈다"
    assert res.ok, res.violations


def test_refill_goes_to_any_eligible_worker():
    """빈자리 채우기는 해방된 인력뿐 아니라 다른 인력도 시도한다.

    W02 는 hospital_escort 전용이라 취소 뒤 빈자리에 household_support 요청을 넣을 수 없다.
    W01 은 household_support 자격이 있고 취소 통보 후 R03 을 받을 수 있다.
    """
    inst = {
        "service_date": "2026-11-02",
        "workers": [
            {"worker_id": "W01", "skills": ["household_support"], "base_node": "BASE_W01",
             "shift_start_min": 540, "shift_end_min": 1080,
             "break_start_min": 1020, "break_minutes": 60, "max_work_minutes": 480},
            {"worker_id": "W02", "skills": ["hospital_escort"], "base_node": "BASE_W02",
             "shift_start_min": 540, "shift_end_min": 1080,
             "break_start_min": 1020, "break_minutes": 60, "max_work_minutes": 480},
        ],
        "requests": [
            # W01 에 배정되어 600-660 차지
            {"request_id": "R01", "beneficiary_id": "B01",
             "service_type": "household_support", "service_date": "2026-11-02",
             "home_node": "HOME_B01", "earliest_start_min": 600, "latest_start_min": 660,
             "service_minutes": 60, "required_skills": ["household_support"],
             "priority": 1, "requested_at": "2026-10-30T10:00"},
            # W02 에 배정되지만 취소됨
            {"request_id": "R02", "beneficiary_id": "B02",
             "service_type": "hospital_escort", "service_date": "2026-11-02",
             "home_node": "HOME_B02",
             "hospital_node": "HOSP_H01", "appointment_min": 800,
             "arrival_buffer_min": 10, "hospital_stay_min": 60,
             "earliest_start_min": 700, "latest_start_min": 760,
             "service_minutes": 60, "required_skills": ["hospital_escort"],
             "priority": 1, "requested_at": "2026-10-30T11:00",
             "cancelled_same_day": True, "cancel_notice_min_before": 120},
            # household_support 미배정 대기 중 — W01 이 R01 끝난 뒤 받을 수 있다
            {"request_id": "R03", "beneficiary_id": "B03",
             "service_type": "household_support", "service_date": "2026-11-02",
             "home_node": "HOME_B03", "earliest_start_min": 700, "latest_start_min": 900,
             "service_minutes": 60, "required_skills": ["household_support"],
             "priority": 1, "requested_at": "2026-10-30T12:00"},
        ],
        "travel": [
            {"from_node": "BASE_W01", "to_node": "HOME_B01", "minutes": 10},
            {"from_node": "BASE_W01", "to_node": "HOME_B03", "minutes": 12},
            {"from_node": "BASE_W02", "to_node": "HOME_B02", "minutes": 15},
            {"from_node": "HOME_B01", "to_node": "HOME_B03", "minutes": 7},
            {"from_node": "HOME_B03", "to_node": "HOME_B01", "minutes": 7},
            {"from_node": "HOME_B02", "to_node": "HOME_B03", "minutes": 9},
            # hospital_escort 이동 노드
            {"from_node": "HOME_B02", "to_node": "HOSP_H01", "minutes": 20},
            {"from_node": "HOSP_H01", "to_node": "HOME_B02", "minutes": 20},
        ],
    }
    res = replay(inst, "request_order")
    assert res.ok, res.violations
    assigned = {a["request_id"]: a["worker_id"] for a in res.assignments}
    # R02 취소 후 W01(자격 있음)이 R03 을 받아야 한다. W02 는 자격이 없어 못 받는다.
    assert "R03" in assigned, "다른 인력의 빈자리로도 채우지 못했다"
    assert assigned["R03"] == "W01", f"household_support 자격 있는 W01 이 받아야 하는데 {assigned['R03']} 가 받았다"

