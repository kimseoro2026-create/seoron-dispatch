"""인력·근무 관리(W04). PRD FR-16(서비스·자격 유효기간·근무·휴게), FR-17(근무표 가져오기·변경분, 동행→가사 자동 확장 금지),
검수: 자격 만료·근무시간 밖 요청은 후보에서 제외."""
from __future__ import annotations

from pathlib import Path

import pytest

from intake.import_shifts import convert_shift_rows, parse_shifts_csv
from planner.data import apply_worker_settings
from store import Store

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "processed"

W = {"worker_id": "W01", "skills": ["hospital_escort", "household_support"], "base_node": "WORKER:W01",
     "shift_start_min": 540, "shift_end_min": 1080, "break_start_min": 720, "break_minutes": 60,
     "max_work_minutes": 480}


def _profile(**kw):
    p = {"skills": [{"code": "hospital_escort", "valid_to": None}, {"code": "household_support", "valid_to": None}],
         "shift_start_min": 540, "shift_end_min": 1080, "break_start_min": 720, "break_minutes": 60,
         "max_work_minutes": 480, "active": True, "transport_modes": ["자동차"], "region": ""}
    p.update(kw)
    return p


# ── 적용 규칙 ─────────────────────────────────────────────────────────────
def test_expired_skill_is_dropped_on_later_dates():
    prof = {"W01": _profile(skills=[{"code": "hospital_escort", "valid_to": "2026-11-20"},
                                    {"code": "household_support", "valid_to": None}])}
    assert apply_worker_settings([W], "2026-11-20", prof, {})[0]["skills"] == ["hospital_escort", "household_support"]
    assert apply_worker_settings([W], "2026-11-21", prof, {})[0]["skills"] == ["household_support"]


def test_escort_only_is_not_expanded_to_household():
    prof = {"W01": _profile(skills=[{"code": "hospital_escort", "valid_to": None}])}
    assert apply_worker_settings([W], "2026-11-21", prof, {})[0]["skills"] == ["hospital_escort"]


def test_day_shift_overrides_and_off_day_removes():
    shifts = {"W01": {"off": False, "start_min": 600, "end_min": 900, "break_start_min": 750, "break_minutes": 30}}
    w = apply_worker_settings([W], "2026-11-21", {}, shifts)[0]
    assert (w["shift_start_min"], w["shift_end_min"], w["break_start_min"], w["break_minutes"]) == (600, 900, 750, 30)
    assert apply_worker_settings([W], "2026-11-21", {}, {"W01": {"off": True}}) == []


def test_inactive_worker_is_removed_and_base_node_never_changes():
    assert apply_worker_settings([W], "2026-11-21", {"W01": _profile(active=False)}, {}) == []
    w = apply_worker_settings([W], "2026-11-21", {"W01": _profile(shift_start_min=600)}, {})[0]
    assert w["base_node"] == "WORKER:W01" and w["shift_start_min"] == 600


@pytest.mark.skipif(not (DATA / "requests.csv").exists(), reason="생성 데이터 없음")
def test_engine_skips_worker_with_expired_skill(tmp_path):
    from planner.baseline import request_order
    from planner.data import load_day
    s = Store(tmp_path / "s.db")
    date = "2026-11-03"
    base = load_day(DATA, date)
    escort_workers = [w["worker_id"] for w in base.workers if "hospital_escort" in w["skills"]]
    for wid in escort_workers:
        s.save_worker_profile(wid, _profile(skills=[{"code": "hospital_escort", "valid_to": "2026-11-01"},
                                                    {"code": "household_support", "valid_to": None}]), actor="A")
    inst = load_day(DATA, date, **s.day_inputs(date))
    r = request_order(inst.as_instance())
    by_id = {q["request_id"]: q for q in inst.requests}
    assert not any(by_id[a["request_id"]]["service_type"] == "hospital_escort" for a in r.assignments)


# ── 저장소 ────────────────────────────────────────────────────────────────
def test_profile_and_shifts_are_saved_and_audited(tmp_path):
    s = Store(tmp_path / "s.db")
    s.save_worker_profile("W01", _profile(shift_end_min=1020), actor="담당자")
    assert Store(tmp_path / "s.db").worker_profiles()["W01"]["shift_end_min"] == 1020
    sm = s.upsert_shifts([{"worker_id": "W01", "service_date": "2026-11-21", "off": True}], actor="A", source="수동")
    assert sm == {"new": 1, "changed": 0, "unchanged": 0}
    sm = s.upsert_shifts([{"worker_id": "W01", "service_date": "2026-11-21", "off": True},
                          {"worker_id": "W02", "service_date": "2026-11-21", "off": False, "start_min": 600,
                           "end_min": 900, "break_start_min": 720, "break_minutes": 60}], actor="A", source="CSV")
    assert sm == {"new": 1, "changed": 0, "unchanged": 1}
    assert s.day_shifts("2026-11-21")["W01"]["off"] is True
    s.delete_shift("W01", "2026-11-21", actor="A")
    assert "W01" not in s.day_shifts("2026-11-21")
    acts = [a["action"] for a in s.audit()]
    assert "worker:profile" in acts and "worker:shift" in acts and "worker:shift_delete" in acts


def test_profile_validation(tmp_path):
    s = Store(tmp_path / "s.db")
    with pytest.raises(ValueError):
        s.save_worker_profile("W01", _profile(break_start_min=500), actor="A")      # 휴게가 근무 밖
    with pytest.raises(ValueError):
        s.save_worker_profile("W01", _profile(shift_start_min=1100), actor="A")     # 시작이 끝보다 늦음
    with pytest.raises(ValueError):
        s.save_worker_profile("W01", _profile(skills=[]), actor="A")                # 활동 중인데 서비스 없음


# ── 근무표 CSV ────────────────────────────────────────────────────────────
@pytest.mark.skipif(not (DATA / "workers.csv").exists(), reason="생성 데이터 없음")
def test_shift_csv_rows():
    text = ("worker_external_id,date,start_time,end_time,break_start,break_end\n"
            "W01,2026-11-21,10:00,16:00,12:30,13:00\n"
            "W02,2026-11-21,,,,\n"
            "W99,2026-11-21,09:00,18:00,12:00,13:00\n"
            "W03,2026-11-21,09:00,18:00,19:00,19:30\n"
            "W04,2026-11-32,09:00,18:00,12:00,13:00\n")
    parsed = parse_shifts_csv(text.encode("utf-8"), "shifts.csv")
    assert parsed.error is None
    res = convert_shift_rows(parsed.rows, DATA)
    ok = [r for r in res if r.row]
    assert [r.row["worker_id"] for r in ok] == ["W01", "W02"]
    assert ok[0].row == {"worker_id": "W01", "service_date": "2026-11-21", "off": False, "start_min": 600,
                         "end_min": 960, "break_start_min": 750, "break_minutes": 30}
    assert ok[1].row["off"] is True
    errs = {r.line: " ".join(r.errors) for r in res if r.errors}
    assert "인력" in errs[4] and "휴게" in errs[5] and "날짜" in errs[6]
