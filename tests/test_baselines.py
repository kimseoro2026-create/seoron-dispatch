"""기준선(신청순·근거리) 테스트.

가장 중요한 검사는 하나다. **기준선 결과도 verifier 를 통과해야 한다.**
기준선이 하드 제약을 어긴 배정안을 내놓으면 비교 자체가 성립하지 않는다.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from planner.baseline import (NO_CAPACITY, NO_SKILL_MATCH, ROUTE_UNKNOWN,
                              TIME_WINDOW_CONFLICT, both_orders_are_ok, nearest,
                              request_order)
from verifier import verify

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "verify_set.json"


@pytest.fixture
def inst():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def _by_request(result):
    return {a["request_id"]: (a["worker_id"], a["arrive_min"]) for a in result.assignments}


# ── 신청순 ────────────────────────────────────────────────────────────────

def test_request_order_picks_earliest(inst):
    r = request_order(inst)
    got = _by_request(r)
    # 접수시각 순서: R02 → R01 → R05 → R03 → R04
    # R02: W02 가 550 에 시작할 수 있어 W01(560)보다 빠르다.
    assert got["R02"] == ("W02", 550)
    # R01: W01 만 동행 자격이 있다.
    assert got["R01"][0] == "W01"
    assert got["R01"][1] == 550


def test_request_order_break_pushes_arrival(inst):
    """휴게를 피하려면 도착을 뒤로 미뤄야 한다. 미루는 계산이 맞는지 확인."""
    got = _by_request(request_order(inst))
    # R03 은 W02 가 맡고, 이동 출발이 휴게종료(840) 이후여야 한다 → 855 도착.
    assert got["R03"] == ("W02", 855)


def test_request_order_assignment_is_tight(inst):
    """R04 는 손으로 풀면 840 이지만, 출발 815 가 휴게와 겹쳐 865 로 밀린다.

    이동 구간까지 휴게 검사에 넣었는지 확인하는 사례다.
    """
    got = _by_request(request_order(inst))
    assert got["R04"] == ("W01", 865)
    rep = verify(inst, request_order(inst).assignments)
    r04 = next(t for t in rep.timelines if t.request_id == "R04")
    assert r04.depart_min == 840          # 휴게 종료 직후 출발
    assert r04.hospital_arrive_min == 885  # 예약 900 - 여유 15, 여유 0분으로 통과


def test_request_order_reports_capacity_shortage(inst):
    """근무 상한을 조이면 미배정이 생기고, 사유가 기록되어야 한다."""
    tight = copy.deepcopy(inst)
    for w in tight["workers"]:
        if w["worker_id"] == "W01":
            w["max_work_minutes"] = 300   # R01+R04 는 400분 필요
    tight["travel"] = {(t["from_node"], t["to_node"]): t["minutes"] for t in tight["travel"]}
    r = request_order(tight)
    reasons = {u.request_id: u for u in r.unassigned}
    assert "R04" in reasons
    assert reasons["R04"].reason == NO_CAPACITY
    # 탐욕적 배정이 원인일 수 있으므로 '증명됨'으로 단정하지 않는다.
    assert reasons["R04"].certainty == "diagnostic"


# ── 근거리 ────────────────────────────────────────────────────────────────

def test_nearest_prioritises_urgent_first(inst):
    """근거리는 우선순위가 높은 요청부터 처리한다. R04(우선순위 3)가 먼저 간다."""
    r = nearest(inst)
    got = _by_request(r)
    # R04 를 먼저 잡으면 W01 이 휴게 뒤 855 로 들어간다(집에서 출발, 이동 15분).
    assert got["R04"] == ("W01", 855)
    assert next(a for a in r.assignments if a["request_id"] == "R04")


def test_nearest_starves_later_requests(inst):
    """우선순위 우선 탐욕 배정의 부작용을 그대로 드러낸다.

    R04 를 먼저 넣으면 W01 이 오후까지 묶여 R01(동행)을 맡을 수 없다.
    이 열세가 비교의 핵심이므로, 결과가 나빠도 그대로 보고한다.
    """
    near = nearest(inst)
    order = request_order(inst)
    assert near.assigned_count() < order.assigned_count()
    assert {u.request_id for u in near.unassigned} == {"R01", "R02", "R05"}


def test_nearest_still_produces_valid_plan(inst):
    r = nearest(inst)
    assert both_orders_are_ok(inst, r)


# ── 공통 원칙 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("algo", [request_order, nearest])
def test_baselines_pass_verifier(inst, algo):
    """기준선 결과는 하드 제약 위반 0건이어야 배정안으로 쓸 수 있다."""
    r = algo(inst)
    rep = verify(inst, r.assignments)
    assert rep.ok, [v.as_dict() for v in rep.violations]


@pytest.mark.parametrize("algo", [request_order, nearest])
def test_no_request_is_assigned_twice(inst, algo):
    r = algo(inst)
    ids = [a["request_id"] for a in r.assignments]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("algo", [request_order, nearest])
def test_every_request_is_accounted_for(inst, algo):
    """모든 요청은 배정되거나, 사유와 함께 미배정으로 남아야 한다. 조용히 사라지면 안 된다."""
    r = algo(inst)
    handled = {a["request_id"] for a in r.assignments} | {u.request_id for u in r.unassigned}
    assert handled == {req["request_id"] for req in inst["requests"]}


def test_missing_travel_is_not_zeroed(inst):
    """이동시간이 없으면 0분으로 대체하지 않는다(절대원칙 3).

    N_W02→N_B02 구간만 지운다. 신청순은 R02를 가장 먼저 처리하므로 이 결손을 곧바로 만난다.
    """
    broken = copy.deepcopy(inst)
    broken["travel"] = {(t["from_node"], t["to_node"]): t["minutes"]
                        for t in broken["travel"]
                        if not (t["from_node"] == "N_W02" and t["to_node"] == "N_B02")}
    assert ("N_W02", "N_B02") not in broken["travel"]

    r = request_order(broken)
    reasons = {u.request_id: u for u in r.unassigned}
    assert "R02" in reasons, "이동시간이 없는데 배정해 버렸다"
    assert reasons["R02"].reason == ROUTE_UNKNOWN
    assert reasons["R02"].certainty == "verified"      # 후보 필터로 확인된 사유


def test_missing_travel_never_fabricates_route(inst):
    """결손 구간을 쓰는 배정은 시간 계산을 하지 않고 후보에서 빠져야 한다.

    근거리는 우선순위(R04)를 먼저 잡아 그 뒤 요청들이 시간창에서 먼저 막히므로
    ROUTE_UNKNOWN이 드러나지 않는다. 그래도 결손 구간을 0분으로 계산해
    배정해 버리는 일은 없어야 한다.
    """
    broken = copy.deepcopy(inst)
    broken["travel"] = {(t["from_node"], t["to_node"]): t["minutes"]
                        for t in broken["travel"]
                        if not (t["from_node"] == "N_W02" and t["to_node"] == "N_B02")}
    for algo in (request_order, nearest):
        r = algo(broken)
        # W02 는 기지에서 바로 R02 에 갈 수 없다. 그런 배정이 나오면 0분으로 채운 것이다.
        assert not any(a["request_id"] == "R02" and a["worker_id"] == "W02"
                       and a["arrive_min"] == 570 for a in r.assignments)
        # 검증기로도 확인: 결손 구간을 지나는 배정안은 통과할 수 없다
        verify(broken, r.assignments)


def test_no_skill_reported_when_nobody_qualified(inst):
    """동행 인력이 아무도 없으면 사유는 NO_SKILL_MATCH 여야 한다."""
    broken = copy.deepcopy(inst)
    for w in broken["workers"]:
        if w["worker_id"] == "W01":
            w["skills"] = ["household_support"]
    broken["travel"] = {(t["from_node"], t["to_node"]): t["minutes"] for t in broken["travel"]}
    r = request_order(broken)
    reasons = {u.request_id: u.reason for u in r.unassigned}
    assert reasons.get("R01") == NO_SKILL_MATCH


def test_time_window_reported(inst):
    """아무도 시간창을 지킬 수 없으면 TIME_WINDOW_CONFLICT 로 남는다."""
    broken = copy.deepcopy(inst)
    for req in broken["requests"]:
        if req["request_id"] == "R03":
            req["latest_start_min"] = 781      # 휴게 780~840 을 피할 수 없는 창
    broken["travel"] = {(t["from_node"], t["to_node"]): t["minutes"] for t in broken["travel"]}
    r = request_order(broken)
    reasons = {u.request_id: u.reason for u in r.unassigned}
    assert reasons.get("R03") in (TIME_WINDOW_CONFLICT, NO_CAPACITY)


def test_does_not_use_cancelled_flag():
    """CLAUDE.md 7: 계획 시점에 일어나지 않은 취소를 쓰지 않는다.

    기준선 코드가 cancelled_same_day 를 참조하면 미래 정보를 쓰는 것이다.
    """
    src = (ROOT / "planner" / "baseline.py").read_text(encoding="utf-8")
    body = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "cancelled" not in body
    assert "days_since_discharge" not in body
