"""저장소: 가져오기 기록·draft 요청·검토. PRD FR-08(중복)·FR-09(draft)·AT-01(같은 CSV 두 번)."""
from __future__ import annotations

import pytest

from planner.approval import ACCEPT, Decision
from store import Store

DATE = "2026-11-20"


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "s.db")


def _req(ext, earliest=600, ben="B0001"):
    return {"request_id": f"가져옴-{ext}", "external_id": ext, "beneficiary_id": ben,
            "service_type": "household_support", "service_date": DATE, "home_node": f"BENEFICIARY:{ben}",
            "earliest_start_min": earliest, "latest_start_min": earliest + 60, "service_minutes": 90,
            "priority": 1, "priority_reason": "가져온 자료", "requested_at": "2026-11-14T09:00",
            "required_skills": ["household_support"], "source": "import"}


def _rows(*reqs, errors=()):
    rows = [{"line": i + 2, "external_id": r["external_id"], "request": r, "errors": [], "warnings": []}
            for i, r in enumerate(reqs)]
    for line, msg in errors:
        rows.append({"line": line, "external_id": "", "request": None, "errors": [msg], "warnings": []})
    return rows


def _import(store, rows, fhash="h1"):
    return store.import_requests(file_hash=fhash, file_name="a.csv", kind="csv", actor="담당자",
                                 mapping={"external_id": "external_id"}, rows=rows)


def test_import_creates_drafts_not_ready(store):
    s = _import(store, _rows(_req("X1"), _req("X2"), errors=[(4, "서비스일: 형식 오류")]))
    assert (s["n_new"], s["n_error"], s["status"]) == (2, 1, "partial")
    assert store.ready_requests(DATE) == []                      # 검토 전에는 배정 계산에 들어가지 않는다
    drafts = store.requests(status="draft")
    assert [d["request_id"] for d in drafts] == ["가져옴-X1", "가져옴-X2"]
    imp = store.imports()[0]
    assert imp["errors"] == [{"line": 4, "external_id": "", "errors": ["서비스일: 형식 오류"]}]


def test_same_file_twice_makes_no_new_requests(store):            # AT-01
    _import(store, _rows(_req("X1")))
    assert store.find_import_by_hash("h1") is not None
    s = _import(store, _rows(_req("X1")))
    assert (s["n_new"], s["n_unchanged"]) == (0, 1)
    assert len(store.requests()) == 1


def test_changed_row_waits_for_staff_confirmation(store):        # FR-08 덮어쓰기는 확인 후에만
    _import(store, _rows(_req("X1", earliest=600)))
    s = _import(store, _rows(_req("X1", earliest=720)), fhash="h2")
    assert s["n_changed"] == 1 and s["changed"][0]["request_id"] == "가져옴-X1"
    assert store.requests()[0]["payload"]["earliest_start_min"] == 600     # 아직 그대로
    store.apply_import_changes(s["import_id"], ["가져옴-X1"], actor="담당자")
    r = store.requests()[0]
    assert r["payload"]["earliest_start_min"] == 720 and r["status"] == "draft" and r["version"] == 2


def test_all_rows_bad_is_failed(store):
    s = _import(store, _rows(errors=[(2, "a"), (3, "b")]))
    assert s["status"] == "failed" and store.requests() == []


def test_review_moves_draft_to_ready_and_back(store):
    _import(store, _rows(_req("X1"), _req("X2")))
    store.review_requests(["가져옴-X1"], "확인 완료", actor="A")
    assert [r["request_id"] for r in store.ready_requests(DATE)] == ["가져옴-X1"]
    with pytest.raises(ValueError):
        store.review_requests(["가져옴-X2"], "보류", actor="A", reason="")
    store.review_requests(["가져옴-X2"], "취소", actor="A", reason="중복 신청")
    assert store.request_status("가져옴-X2") == "cancelled"
    assert any(a["action"] == "request:확인 완료" for a in store.audit())


def test_applying_change_to_confirmed_request_supersedes_it(store):
    inst = {"workers": [{"worker_id": "W01", "skills": [], "base_node": "BASE", "shift_start_min": 540,
                         "shift_end_min": 1080, "break_start_min": 780, "break_minutes": 60,
                         "max_work_minutes": 480}],
            "requests": [dict(_req("X1"), required_skills=[], home_node="HOME")],
            "travel": [{"from_node": "BASE", "to_node": "HOME", "minutes": 10},
                       {"from_node": "HOME", "to_node": "BASE", "minutes": 10}]}
    _import(store, _rows(_req("X1")))
    store.review_requests(["가져옴-X1"], "확인 완료", actor="A")
    store.approve(DATE, inst, [{"request_id": "가져옴-X1", "worker_id": "W01", "arrive_min": 610}],
                  [Decision("가져옴-X1", ACCEPT)], expected_version=0)
    assert store.confirmed(DATE)
    s = _import(store, _rows(_req("X1", earliest=720)), fhash="h2")
    store.apply_import_changes(s["import_id"], ["가져옴-X1"], actor="A")
    assert store.confirmed(DATE) == [] and store.request_status("가져옴-X1") == "draft"


def test_import_cannot_overwrite_card_request(store):
    """다른 출처(요청 카드)의 같은 ID는 가져오기로 덮어쓰지 않는다."""
    card = dict(_req("X1"), source="card")
    store.save_card_review("k", card_id="X1", text_hash="h", extracted={}, final={}, action="확인 완료",
                           reason="", reviewer="A", request=card)
    s = _import(store, _rows(_req("X1", earliest=720)))
    assert s["n_error"] == 1 and s["n_new"] == 0
