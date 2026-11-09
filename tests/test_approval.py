"""planner.approval 테스트 — PRD 9장 승인 트랜잭션."""
from __future__ import annotations

import pytest
from planner.approval import (
    ACCEPT, REJECT, PENDING,
    STATUS_APPROVED, STATUS_COMPLETE, STATUS_REJECTED_ALL,
    STATUS_CONFLICT, STATUS_STALE,
    ApprovalGate, ApprovalError, Decision,
)


@pytest.fixture
def gate():
    return ApprovalGate()


# ── 직접 만든 최소 인스턴스 ────────────────────────────────────────────────
def _mini_instance():
    """검증기를 통과하는 최소 인스턴스.

    주의: R02 는 R01 이 730 에 끝난 뒤 이동 5분이면 735 에 도착한다.
    시간창 상한을 780 으로 두어야 검증기에서 시간창 위반이 나지 않는다.
    """
    return {
        "workers": [{"worker_id": "W01", "skills": [], "base_node": "BASE_W01",
                     "shift_start_min": 540, "shift_end_min": 1080,
                     "break_start_min": 780, "break_minutes": 60,
                     "max_work_minutes": 480}],
        "requests": [{"request_id": "R01", "beneficiary_id": "B01",
                      "service_type": "household_support", "service_date": "2026-11-02",
                      "earliest_start_min": 600, "latest_start_min": 720,
                      "service_minutes": 120, "required_skills": [],
                      "home_node": "HOME_B01", "priority": 1},
                     {"request_id": "R02", "beneficiary_id": "B02",
                      "service_type": "household_support", "service_date": "2026-11-02",
                      "earliest_start_min": 600, "latest_start_min": 780,
                      "service_minutes": 30, "required_skills": [],
                      "home_node": "HOME_B02", "priority": 1}],
        "travel": [{"from_node": "BASE_W01", "to_node": "HOME_B01", "minutes": 10},
                   {"from_node": "HOME_B01", "to_node": "HOME_B02", "minutes": 5},
                   {"from_node": "HOME_B02", "to_node": "HOME_B01", "minutes": 5},
                   {"from_node": "BASE_W01", "to_node": "HOME_B02", "minutes": 12},
                   {"from_node": "HOME_B01", "to_node": "BASE_W01", "minutes": 10},
                   {"from_node": "HOME_B02", "to_node": "BASE_W01", "minutes": 12}],
    }


def _proposal():
    # R01: 610 도착 → 730 종료 (서비스 120분)
    # R02: 735 도착 → 765 종료 (서비스 30분) → 휴게 780~840 전에 끝난다
    # 두 건 모두 수락하면 휴게·근무시간·자격을 모두 지킨다.
    return [{"request_id": "R01", "worker_id": "W01", "arrive_min": 610},
            {"request_id": "R02", "worker_id": "W01", "arrive_min": 735}]


# ── 기본 흐름 ──────────────────────────────────────────────────────────────
def test_accept_all_returns_complete(gate):
    inst = _mini_instance()
    prop = _proposal()
    decisions = [Decision("R01", ACCEPT), Decision("R02", ACCEPT)]
    r = gate.approve(inst, prop, decisions, schedule_version=0, service_date="2026-11-02")
    assert r.status == STATUS_COMPLETE
    assert len(r.confirmed) == 2
    assert r.schedule_version == 1


def test_reject_all_returns_rejected_all(gate):
    inst = _mini_instance()
    prop = _proposal()
    decisions = [Decision("R01", REJECT, reason="인력 부족"),
                 Decision("R02", REJECT, reason="시간 불일치")]
    r = gate.approve(inst, prop, decisions, schedule_version=0)
    assert r.status == STATUS_REJECTED_ALL
    assert len(r.confirmed) == 0
    assert len(r.rejected) == 2


def test_partial_accept_returns_approved_with_pending(gate):
    inst = _mini_instance()
    prop = _proposal()
    decisions = [Decision("R01", ACCEPT)]  # R02 는 결정 없음 → PENDING
    r = gate.approve(inst, prop, decisions, schedule_version=0)
    assert r.status == STATUS_APPROVED
    assert len(r.confirmed) == 1
    assert len(r.pending) == 1


# ── schedule_version 충돌 ──────────────────────────────────────────────────
def test_stale_version_rejected(gate):
    inst = _mini_instance()
    prop = _proposal()
    gate.approve(inst, prop, [Decision("R01", ACCEPT)], schedule_version=0)
    # 버전이 이미 1인데 0으로 재시도
    r = gate.approve(inst, prop, [Decision("R02", ACCEPT)], schedule_version=0)
    assert r.status == STATUS_STALE
    assert r.schedule_version == 1  # 현재 버전을 알려준다


# ── 잠금 ──────────────────────────────────────────────────────────────────
def test_locked_entry_cannot_be_overwritten(gate):
    inst = _mini_instance()
    prop = _proposal()
    gate.approve(inst, prop, [Decision("R01", ACCEPT)], schedule_version=0)
    # 같은 요청을 다시 수락 시도
    r2 = gate.approve(inst, prop, [Decision("R01", ACCEPT)], schedule_version=1)
    assert r2.status == STATUS_CONFLICT


def test_release_allows_reassignment(gate):
    inst = _mini_instance()
    prop = _proposal()
    gate.approve(inst, prop, [Decision("R01", ACCEPT)], schedule_version=0)
    gate.release("R01", reason="담당자 요청으로 취소")
    assert all(c["request_id"] != "R01" for c in gate.confirmed)
    assert gate.schedule_version == 2


def test_release_without_reason_raises(gate):
    inst = _mini_instance()
    prop = _proposal()
    gate.approve(inst, prop, [Decision("R01", ACCEPT)], schedule_version=0)
    with pytest.raises(ApprovalError, match="사유"):
        gate.release("R01", reason="")


# ── idempotency ───────────────────────────────────────────────────────────
def test_idempotency_key_prevents_duplicate(gate):
    inst = _mini_instance()
    prop = _proposal()
    r1 = gate.approve(inst, prop, [Decision("R01", ACCEPT)],
                      schedule_version=0, idempotency_key="key-001")
    r2 = gate.approve(inst, prop, [Decision("R01", ACCEPT)],
                      schedule_version=1, idempotency_key="key-001")
    assert r1.status == r2.status == STATUS_APPROVED
    assert gate.schedule_version == 1  # 두 번째 호출에서 버전이 증가하지 않았다


# ── 거부 사유 필수 ─────────────────────────────────────────────────────────
def test_reject_without_reason_raises(gate):
    inst = _mini_instance()
    prop = _proposal()
    with pytest.raises(ApprovalError, match="사유"):
        gate.approve(inst, prop, [Decision("R01", REJECT, reason="")],
                     schedule_version=0)


# ── 감사 이력 ──────────────────────────────────────────────────────────────
def test_audit_records_every_decision(gate):
    inst = _mini_instance()
    prop = _proposal()
    gate.approve(inst, prop,
                 [Decision("R01", ACCEPT), Decision("R02", REJECT, reason="사유")],
                 schedule_version=0, actor="테스터")
    assert len(gate.audit) == 2
    assert gate.audit[0]["actor"] == "테스터"
    assert gate.audit[0]["action"] == ACCEPT
    assert gate.audit[1]["action"] == REJECT
