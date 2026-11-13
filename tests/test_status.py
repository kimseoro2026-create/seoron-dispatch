"""오늘의 현황(W01) 집계. PRD FR-01~04, 검수: 필터된 표와 카드 집계 일치, 같은 대상자 2건 = 고유 1명."""
from __future__ import annotations

from pathlib import Path

import pytest

from planner.approval import ACCEPT, Decision
from store import Store
from store.status import (STATUS_CANCELLED, STATUS_CONFIRMED, STATUS_NEEDS_REVIEW, STATUS_NOT_RUN,
                          STATUS_PROPOSED, STATUS_UNASSIGNED, day_status, filter_rows, summarize)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "processed"
DATE = "2026-11-03"
pytestmark = pytest.mark.skipif(not (DATA / "requests.csv").exists(), reason="생성 데이터 없음")


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "s.db")


def _run_and_record(store, algorithm="신청순"):
    from planner.baseline import request_order
    from planner.data import load_day
    from verifier import verify
    inst = load_day(DATA, DATE, extra_requests=store.ready_requests(DATE))
    r = request_order(inst.as_instance())
    assert not verify(inst.as_instance(), r.assignments).violations
    store.record_run(DATE, algorithm, inst.as_instance(), config={}, status="feasible", solve_seconds=None,
                     metrics={}, assignments=r.assignments, unassigned=[u.as_dict() for u in r.unassigned])
    return inst, r


def test_before_any_run_all_valid_requests_are_not_run(store):
    st = day_status(store, DATA, DATE, algorithm="신청순")
    assert st["run"] is None
    assert st["rows"] and all(r["status"] == STATUS_NOT_RUN for r in st["rows"])


def test_run_gives_proposed_and_unassigned_then_confirmed(store):
    inst, r = _run_and_record(store)
    st = day_status(store, DATA, DATE, algorithm="신청순")
    counts = summarize(st["rows"])
    assert counts[STATUS_PROPOSED] == len(r.assignments)
    assert counts[STATUS_UNASSIGNED] == len(r.unassigned)
    first = r.assignments[0]
    store.approve(DATE, inst.as_instance(), r.assignments, [Decision(first["request_id"], ACCEPT)],
                  expected_version=0)
    st = day_status(store, DATA, DATE, algorithm="신청순")
    row = next(x for x in st["rows"] if x["request_id"] == first["request_id"])
    assert row["status"] == STATUS_CONFIRMED and row["worker_id"] == first["worker_id"]
    assert summarize(st["rows"])[STATUS_PROPOSED] == len(r.assignments) - 1


def test_changed_input_does_not_reuse_old_run(store):
    """계산 뒤 요청이 바뀌면 예전 제안을 지금 것처럼 보이지 않는다."""
    _run_and_record(store)
    from planner.data import load_day
    base = load_day(DATA, DATE).requests[0]
    card = dict(base, request_id="카드-T1", earliest_start_min=base["earliest_start_min"], source="card",
                latest_start_min=base["latest_start_min"] + 10)
    store.save_card_review("k", card_id="T1", text_hash="h", extracted={}, final={}, action="확인 완료",
                           reason="", reviewer="A", request=card)
    st = day_status(store, DATA, DATE, algorithm="신청순")
    assert st["run"] is None and all(r["status"] == STATUS_NOT_RUN for r in st["rows"])


def test_draft_and_cancelled_store_requests_are_separate_states(store):
    req = {"request_id": "가져옴-Z1", "external_id": "Z1", "beneficiary_id": "B0001",
           "service_type": "household_support", "service_date": DATE, "home_node": "BENEFICIARY:B0001",
           "earliest_start_min": 600, "latest_start_min": 660, "service_minutes": 60, "priority": 1,
           "priority_reason": "가져온 자료", "requested_at": "t", "required_skills": ["household_support"],
           "source": "import"}
    req2 = dict(req, request_id="가져옴-Z2", external_id="Z2")
    store.import_requests(file_hash="h", file_name="a.csv", kind="csv", actor="A", mapping={},
                          rows=[{"line": 2, "external_id": "Z1", "request": req, "errors": [], "warnings": []},
                                {"line": 3, "external_id": "Z2", "request": req2, "errors": [], "warnings": []}])
    store.review_requests(["가져옴-Z2"], "취소", actor="A", reason="중복")
    st = day_status(store, DATA, DATE, algorithm="신청순")
    by = {r["request_id"]: r for r in st["rows"]}
    assert by["가져옴-Z1"]["status"] == STATUS_NEEDS_REVIEW
    assert by["가져옴-Z2"]["status"] == STATUS_CANCELLED and by["가져옴-Z2"]["reason"] == "중복"


def test_unique_people_counts_one_person_with_two_requests():
    rows = [{"request_id": "A", "beneficiary_id": "B1", "status": STATUS_CONFIRMED},
            {"request_id": "B", "beneficiary_id": "B1", "status": STATUS_CONFIRMED},
            {"request_id": "C", "beneficiary_id": "B2", "status": STATUS_PROPOSED}]
    c = summarize(rows)
    assert c[STATUS_CONFIRMED] == 2 and c["confirmed_people"] == 1 and c["planned_people"] == 2


def test_filters_and_counts_agree(store):
    _run_and_record(store)
    st = day_status(store, DATA, DATE, algorithm="신청순")
    rows = filter_rows(st["rows"], service="hospital_escort", priority=None, statuses=None)
    assert rows and all(r["service_type"] == "hospital_escort" for r in rows)
    c = summarize(rows)
    assert sum(c[s] for s in (STATUS_PROPOSED, STATUS_UNASSIGNED, STATUS_CONFIRMED, STATUS_NOT_RUN,
                              STATUS_NEEDS_REVIEW, STATUS_CANCELLED)) == len(rows)


def test_urgent_flags_use_reference_time_not_future_cancellations(store):
    st = day_status(store, DATA, DATE, algorithm="신청순", ref_min=600, urgent_window=60)
    for r in st["rows"]:
        if r["urgent"] == "임박":
            assert 600 <= r["latest_start_min"] <= 660
        if r["urgent"] == "시간 지남":
            assert r["latest_start_min"] < 600
    # 당일 취소 표시는 미래 정보라 현황에 쓰지 않는다
    assert not any(r["status"] == STATUS_CANCELLED for r in st["rows"])
