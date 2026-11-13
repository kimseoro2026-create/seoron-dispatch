"""store — 운영 데이터 저장소(SQLite) 테스트. PRD 7장(상태·승인 트랜잭션)·8장(데이터 모델).

새로고침·다른 화면에서도 같은 데이터를 보도록, 화면 세션 대신 파일 DB 에 남긴다.
"""
from __future__ import annotations

import sqlite3

import pytest

from planner.approval import (ACCEPT, PENDING, REJECT, STATUS_COMPLETE, STATUS_CONFLICT,
                              STATUS_STALE, ApprovalError, Decision)
from store import Store

DATE = "2026-11-02"


@pytest.fixture
def db(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def store(db):
    return Store(db)


def _instance():
    """test_approval.py 와 같은 최소 인스턴스: 인력 1명, 요청 2건(R01 610~730, R02 735~765)."""
    return {
        "workers": [{"worker_id": "W01", "skills": [], "base_node": "BASE_W01",
                     "shift_start_min": 540, "shift_end_min": 1080,
                     "break_start_min": 780, "break_minutes": 60, "max_work_minutes": 480}],
        "requests": [{"request_id": "R01", "beneficiary_id": "B01", "service_type": "household_support",
                      "service_date": DATE, "earliest_start_min": 600, "latest_start_min": 720,
                      "service_minutes": 120, "required_skills": [], "home_node": "HOME_B01", "priority": 1},
                     {"request_id": "R02", "beneficiary_id": "B02", "service_type": "household_support",
                      "service_date": DATE, "earliest_start_min": 600, "latest_start_min": 780,
                      "service_minutes": 30, "required_skills": [], "home_node": "HOME_B02", "priority": 1}],
        "travel": [{"from_node": "BASE_W01", "to_node": "HOME_B01", "minutes": 10},
                   {"from_node": "HOME_B01", "to_node": "HOME_B02", "minutes": 5},
                   {"from_node": "HOME_B02", "to_node": "HOME_B01", "minutes": 5},
                   {"from_node": "BASE_W01", "to_node": "HOME_B02", "minutes": 12},
                   {"from_node": "HOME_B01", "to_node": "BASE_W01", "minutes": 10},
                   {"from_node": "HOME_B02", "to_node": "BASE_W01", "minutes": 12}],
    }


def _proposal():
    return [{"request_id": "R01", "worker_id": "W01", "arrive_min": 610},
            {"request_id": "R02", "worker_id": "W01", "arrive_min": 735}]


def _card_request(rid="카드-E01", date=DATE, earliest=600):
    return {"request_id": rid, "beneficiary_id": "B0001", "service_type": "household_support",
            "service_date": date, "home_node": "BENEFICIARY:B0001", "earliest_start_min": earliest,
            "latest_start_min": earliest + 60, "service_minutes": 90, "priority": 1,
            "priority_reason": "정기 서비스(카드)", "requested_at": "2026-11-01T09:00",
            "required_skills": ["household_support"], "source": "card"}


# ── 요청 카드 ──────────────────────────────────────────────────────────────
def test_card_review_survives_new_connection(db):
    Store(db).save_card_review("k1", card_id="E01", text_hash="abc", extracted={"service_date": "11/2"},
                               final={"service_date": DATE}, action="확인 완료", reason="",
                               reviewer="담당자A", request=_card_request())
    s2 = Store(db)                                   # 새로고침·다른 화면 = 새 연결
    rev = s2.card_review("k1")
    assert rev["action"] == "확인 완료" and rev["reviewer"] == "담당자A"
    assert rev["extracted"] == {"service_date": "11/2"} and rev["final"] == {"service_date": DATE}
    assert [r["request_id"] for r in s2.ready_requests(DATE)] == ["카드-E01"]
    assert s2.ready_request_dates() == [DATE]


def test_card_hold_or_cancel_removes_from_ready_but_keeps_history(store):
    store.save_card_review("k1", card_id="E01", text_hash="abc", extracted={}, final={},
                           action="확인 완료", reason="", reviewer="A", request=_card_request())
    store.save_card_review("k1", card_id="E01", text_hash="abc", extracted={}, final={},
                           action="보류", reason="보호자 확인 중", reviewer="B", request=None)
    assert store.ready_requests(DATE) == []
    hist = store.card_history("k1")
    assert [h["action"] for h in hist] == ["확인 완료", "보류"]
    assert hist[-1]["reason"] == "보호자 확인 중"
    assert store.request_status("카드-E01") == "needs_review"
    store.save_card_review("k1", card_id="E01", text_hash="abc", extracted={}, final={},
                           action="취소", reason="이용 취소", reviewer="B", request=None)
    assert store.request_status("카드-E01") == "cancelled"


def test_raw_text_is_never_stored(db):
    raw = "어르신 B0001 연락처 010-0000-0000 원문 메모"
    Store(db).save_card_review("k1", card_id="직접-1", text_hash="h", extracted={}, final={},
                               action="보류", reason="확인", reviewer="A", request=None)
    con = sqlite3.connect(db)
    dump = "\n".join(con.iterdump())
    assert raw not in dump and "010-0000-0000" not in dump


def test_hold_requires_reason(store):
    with pytest.raises(ValueError):
        store.save_card_review("k1", card_id="E01", text_hash="h", extracted={}, final={},
                               action="보류", reason=" ", reviewer="A", request=None)


def test_confirm_requires_request(store):
    with pytest.raises(ValueError):
        store.save_card_review("k1", card_id="E01", text_hash="h", extracted={}, final={},
                               action="확인 완료", reason="", reviewer="A", request=None)


# ── 실행 기록 (FR-23) ──────────────────────────────────────────────────────
def test_record_run_is_reproducible(store):
    a = store.record_run(DATE, "AI 최적화", _instance(), config={"time_limit_sec": 8},
                         status="feasible", solve_seconds=1.2, metrics={"assigned": 2})
    b = store.record_run(DATE, "AI 최적화", _instance(), config={"time_limit_sec": 8},
                         status="feasible", solve_seconds=1.5, metrics={"assigned": 2})
    assert a == b                                    # 같은 입력·설정 = 같은 실행 ID
    run = store.run(a)
    assert run["snapshot_hash"] and run["engine_version"] and run["config"] == {"time_limit_sec": 8}
    inst = _instance()
    inst["requests"][0]["latest_start_min"] = 700
    c = store.record_run(DATE, "AI 최적화", inst, config={"time_limit_sec": 8},
                         status="feasible", solve_seconds=1.0, metrics={})
    assert c != a


# ── 승인 트랜잭션 (PRD 7장) ────────────────────────────────────────────────
def test_approve_persists_and_bumps_date_version(db):
    s = Store(db)
    res = s.approve(DATE, _instance(), _proposal(),
                    [Decision("R01", ACCEPT), Decision("R02", ACCEPT)], expected_version=0, actor="A")
    assert res.status == STATUS_COMPLETE and res.schedule_version == 1
    s2 = Store(db)
    assert {c["request_id"] for c in s2.confirmed(DATE)} == {"R01", "R02"}
    assert s2.schedule_version(DATE) == 1
    assert s2.schedule_version("2026-11-03") == 0     # 날짜마다 따로


def test_pending_is_not_confirmed(store):
    res = store.approve(DATE, _instance(), _proposal(),
                        [Decision("R01", ACCEPT), Decision("R02", PENDING)], expected_version=0)
    assert {c["request_id"] for c in store.confirmed(DATE)} == {"R01"}
    assert [p["request_id"] for p in res.pending] == ["R02"]


def test_stale_version_changes_nothing(store):
    store.approve(DATE, _instance(), _proposal(), [Decision("R01", ACCEPT)], expected_version=0)
    res = store.approve(DATE, _instance(), _proposal(), [Decision("R02", ACCEPT)], expected_version=0)
    assert res.status == STATUS_STALE
    assert {c["request_id"] for c in store.confirmed(DATE)} == {"R01"}


def test_same_idempotency_key_returns_same_result(db):   # AT-08 승인 버튼 중복 클릭
    s = Store(db)
    r1 = s.approve(DATE, _instance(), _proposal(), [Decision("R01", ACCEPT)],
                   expected_version=0, idempotency_key="click-1")
    r2 = Store(db).approve(DATE, _instance(), _proposal(), [Decision("R01", ACCEPT)],
                           expected_version=0, idempotency_key="click-1")
    assert r2.status == r1.status and r2.schedule_version == r1.schedule_version == 1
    assert len(s.confirmed(DATE)) == 1 and s.schedule_version(DATE) == 1


def test_two_approvals_same_worker_overlap_only_one_wins(db):   # AT-07
    inst = _instance()
    s_a, s_b = Store(db), Store(db)
    ok = s_a.approve(DATE, inst, [{"request_id": "R01", "worker_id": "W01", "arrive_min": 610}],
                     [Decision("R01", ACCEPT)], expected_version=0, actor="담당자A")
    assert ok.ok
    # 담당자 B 는 최신 version 을 보고도, A 가 확정한 시간대(610~730)와 겹치는 R02 를 같은 인력에 넣으려 한다
    bad = s_b.approve(DATE, inst, [{"request_id": "R02", "worker_id": "W01", "arrive_min": 650}],
                      [Decision("R02", ACCEPT)], expected_version=1, actor="담당자B")
    assert bad.status == STATUS_CONFLICT and bad.violations
    assert {c["request_id"] for c in s_a.confirmed(DATE)} == {"R01"}


def test_confirmed_request_is_locked(store):
    store.approve(DATE, _instance(), _proposal(), [Decision("R01", ACCEPT)], expected_version=0)
    res = store.approve(DATE, _instance(), [{"request_id": "R01", "worker_id": "W01", "arrive_min": 615}],
                        [Decision("R01", ACCEPT)], expected_version=1)
    assert res.status == STATUS_CONFLICT
    assert store.confirmed(DATE)[0]["arrive_min"] == 610


def test_reject_needs_reason_and_is_recorded(store):
    with pytest.raises(ApprovalError):
        store.approve(DATE, _instance(), _proposal(), [Decision("R01", REJECT, "")], expected_version=0)
    store.approve(DATE, _instance(), _proposal(), [Decision("R01", REJECT, "보호자 거절")], expected_version=0)
    assert store.confirmed(DATE) == []
    acts = [a for a in store.audit() if a["action"] == "reject"]
    assert acts and acts[0]["detail"]["reason"] == "보호자 거절"


def test_only_one_active_confirmed_per_request_at_db_level(store, db):
    store.approve(DATE, _instance(), _proposal(), [Decision("R01", ACCEPT)], expected_version=0)
    con = sqlite3.connect(db)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO assignments(assignment_id, request_id, worker_id, service_date, arrive_min,"
                    " status, created_at, updated_at) VALUES ('x','R01','W02',?,700,'confirmed','t','t')", (DATE,))


def test_release_needs_reason_and_frees_slot(store):
    store.approve(DATE, _instance(), _proposal(), [Decision("R01", ACCEPT)], expected_version=0)
    with pytest.raises(ApprovalError):
        store.release(DATE, "R01", reason="")
    store.release(DATE, "R01", reason="보호자 요청으로 변경")
    assert store.confirmed(DATE) == [] and store.schedule_version(DATE) == 2


def test_changed_ready_card_supersedes_confirmed_assignment(store):
    """PRD 7장: ready 요청의 배정 조건이 바뀌면 관련 확정을 무효화하고 version 을 올린다."""
    inst = _instance()
    req = _card_request(rid="R01")
    store.save_card_review("k1", card_id="E01", text_hash="h", extracted={}, final={},
                           action="확인 완료", reason="", reviewer="A", request=req)
    store.approve(DATE, inst, _proposal(), [Decision("R01", ACCEPT)], expected_version=0)
    v = store.schedule_version(DATE)
    store.save_card_review("k1", card_id="E01", text_hash="h", extracted={}, final={},
                           action="확인 완료", reason="", reviewer="A",
                           request=_card_request(rid="R01", earliest=660))
    assert store.confirmed(DATE) == []
    assert store.schedule_version(DATE) == v + 1
    assert any(a["action"] == "supersede" for a in store.audit())


def test_cancelled_card_cancels_its_confirmed_assignment(store):
    store.save_card_review("k1", card_id="E01", text_hash="h", extracted={}, final={},
                           action="확인 완료", reason="", reviewer="A", request=_card_request(rid="R01"))
    store.approve(DATE, _instance(), _proposal(), [Decision("R01", ACCEPT)], expected_version=0)
    store.save_card_review("k1", card_id="E01", text_hash="h", extracted={}, final={},
                           action="취소", reason="이용 취소", reviewer="A", request=None)
    assert store.confirmed(DATE) == []


def test_audit_has_no_secrets_or_raw(store):
    store.approve(DATE, _instance(), _proposal(), [Decision("R01", ACCEPT)], expected_version=0, actor="A")
    a = store.audit()[0]
    assert {"actor", "action", "entity_id", "before_version", "after_version", "at"} <= set(a)


def test_reset_date_is_audited(store):
    store.approve(DATE, _instance(), _proposal(), [Decision("R01", ACCEPT)], expected_version=0)
    store.reset_date(DATE, actor="A")
    assert store.confirmed(DATE) == []
    assert any(a["action"] == "reset" for a in store.audit())
