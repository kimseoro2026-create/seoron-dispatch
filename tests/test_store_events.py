"""저장소: 취소·지연 대응 반영. AT-09(같은 이벤트 재전송 1회 반영), 오래된 계산 막기, 요청 값 남기기."""
from __future__ import annotations

import pytest

from planner.approval import ACCEPT, Decision
from planner.replan import KIND_CANCEL, KIND_DELAY, plan_event
from store import Store
from tests.test_replan import BASE, _ev, _inst

DATE = "2026-11-02"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "s.db")
    s.approve(DATE, _inst(), BASE, [Decision("R1", ACCEPT), Decision("R2", ACCEPT)], expected_version=0)
    return s


def _orig():
    return {r["request_id"]: r for r in _inst()["requests"]}


def test_apply_cancel_with_refill(store):
    ev = _ev(KIND_CANCEL, "R2", 650)
    p = plan_event(_inst(), store.confirmed(DATE), ev)
    res = store.apply_event(DATE, ev, p, original_requests=_orig(), expected_version=1, actor="담당자")
    assert res["status"] == "applied"
    conf = {c["request_id"]: c for c in store.confirmed(DATE)}
    assert "R2" not in conf and "R3" in conf and conf["R1"]["arrive_min"] == 610
    assert store.cancelled_ids(DATE) == ["R2"]
    assert store.schedule_version(DATE) == 2
    e = store.events(DATE)[0]
    assert (e["kind"], e["occurred_min"], e["received_min"]) == ("cancel", 650, 650) and e["received_at"]


def test_resend_same_event_is_applied_once(store):              # AT-09
    ev = _ev(KIND_CANCEL, "R2", 650)
    p = plan_event(_inst(), store.confirmed(DATE), ev)
    store.apply_event(DATE, ev, p, original_requests=_orig(), expected_version=1, actor="A")
    again = store.apply_event(DATE, ev, p, original_requests=_orig(), expected_version=2, actor="A")
    assert again["status"] == "duplicate" and len(store.events(DATE)) == 1
    assert store.schedule_version(DATE) == 2


def test_stale_proposal_is_not_applied(store):
    ev = _ev(KIND_CANCEL, "R2", 650)
    p = plan_event(_inst(), store.confirmed(DATE), ev)
    store.release(DATE, "R1", reason="담당자 변경")              # 계산 뒤에 일정이 바뀜
    res = store.apply_event(DATE, ev, p, original_requests=_orig(), expected_version=1, actor="A")
    assert res["status"] == "stale" and store.events(DATE) == []


def test_delay_saves_new_window_for_later_calculations(store):
    ev = _ev(KIND_DELAY, "R2", 650, minutes=10)
    p = plan_event(_inst(), store.confirmed(DATE), ev)
    store.apply_event(DATE, ev, p, original_requests=_orig(), expected_version=1, actor="A")
    r2 = next(r for r in store.ready_requests(DATE) if r["request_id"] == "R2")
    assert (r2["earliest_start_min"], r2["latest_start_min"]) == (710, 770)
    assert next(c for c in store.confirmed(DATE) if c["request_id"] == "R2")["arrive_min"] == 710


def test_blocked_proposal_cannot_be_applied(store):
    ev = _ev(KIND_CANCEL, "R1", 615)
    p = plan_event(_inst(), store.confirmed(DATE), ev)
    res = store.apply_event(DATE, ev, p, original_requests=_orig(), expected_version=1, actor="A")
    assert res["status"] == "blocked" and len(store.confirmed(DATE)) == 2


def test_merge_replaces_exact_request_id():
    from intake.to_request import merge_card_requests
    base = [{"request_id": "R1", "beneficiary_id": "B1", "service_type": "household_support"},
            {"request_id": "R2", "beneficiary_id": "B1", "service_type": "household_support"}]
    new = {"request_id": "R2", "beneficiary_id": "B1", "service_type": "household_support", "x": 1}
    merged, rep = merge_card_requests(base, [new])
    assert [r["request_id"] for r in merged] == ["R1", "R2"] and merged[1]["x"] == 1 and rep == {"R2": "R2"}
