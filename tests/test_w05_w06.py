"""배정·승인 보강(W05·W06): 확정 잠금 화면 반영, 담당자 수정 후 재검증, 기존 대비 변경, 요청 집합 선택, 기간 반복 실행."""
from __future__ import annotations

from pathlib import Path

import pytest

from app._approval_rows import parse_decisions
from planner.approval import ACCEPT, REJECT, STATUS_CONFLICT
from store import Store
from tests.test_store import DATE as SDATE, _instance, _proposal

ROOT = Path(__file__).resolve().parent.parent
HAS_DATA = (ROOT / "data" / "processed" / "requests.csv").exists()
DATE = "2026-11-02"


def _btn(at, label):
    return next(b for b in at.button if b.label == label)


# ── 담당자 수정(FR-26) ────────────────────────────────────────────────────
def _rows(**over):
    base = [{"요청": "R01", "결정": "수락", "거부 사유": "", "바꿀 인력": "", "바꿀 도착": ""},
            {"요청": "R02", "결정": "보류", "거부 사유": "", "바꿀 인력": None, "바꿀 도착": float("nan")}]
    for i, kv in over.items():
        base[int(i[1:])].update(kv)
    return base


def test_parse_plain_decisions():
    final, dec, err = parse_decisions(_rows(), _proposal(), set())
    assert err == [] and [(d.request_id, d.action) for d in dec] == [("R01", ACCEPT)] and final == _proposal()


def test_parse_edit_marks_modified():
    final, dec, err = parse_decisions(_rows(r0={"바꿀 도착": "10:20"}), _proposal(), set())
    assert err == [] and final[0] == {"request_id": "R01", "worker_id": "W01", "arrive_min": 620, "modified": True}


@pytest.mark.parametrize("over, word", [({"r1": {"바꿀 인력": "W01"}}, "수락"),
                                        ({"r0": {"바꿀 도착": "10시"}}, "형식"),
                                        ({"r0": {"결정": "거부"}}, "거부 사유")])
def test_parse_errors(over, word):
    _, _, err = parse_decisions(_rows(**over), _proposal(), set())
    assert err and word in err[0]


def test_parse_skips_locked_rows():
    _, dec, _ = parse_decisions(_rows(), _proposal(), {"R01"})
    assert dec == []


def test_edit_that_breaks_rules_is_rejected_by_reverification(tmp_path):
    s = Store(tmp_path / "s.db")
    final, dec, _ = parse_decisions(_rows(r0={"바꿀 도착": "12:50"}), _proposal(), set())   # 12:50+120분 → 휴게 침범
    res = s.approve(SDATE, _instance(), final, dec, expected_version=0)
    assert res.status == STATUS_CONFLICT and res.violations and s.confirmed(SDATE) == []


def test_edit_that_fits_is_confirmed_and_audited(tmp_path):
    s = Store(tmp_path / "s.db")
    final, dec, _ = parse_decisions(_rows(r0={"바꿀 도착": "10:20"}), _proposal(), set())
    assert s.approve(SDATE, _instance(), final, dec, expected_version=0).ok
    assert s.confirmed(SDATE)[0]["arrive_min"] == 620
    assert any(a["detail"].get("modified_by_staff") for a in s.audit())


def test_previous_run_for_change_column(tmp_path):
    s = Store(tmp_path / "s.db")
    inst = _instance()
    r1 = s.record_run(SDATE, "AI 최적화", inst, config={}, status="feasible", solve_seconds=1, metrics={},
                      assignments=_proposal(), unassigned=[])
    inst2 = _instance()
    inst2["requests"][1]["latest_start_min"] = 790
    r2 = s.record_run(SDATE, "AI 최적화", inst2, config={}, status="feasible", solve_seconds=1, metrics={},
                      assignments=_proposal(), unassigned=[])
    assert s.previous_run(SDATE, "AI 최적화", r2)["run_id"] == r1
    assert s.previous_run(SDATE, "AI 최적화", r1)["run_id"] == r2


# ── 화면(확정 잠금·요청 집합·기간 실행) ─────────────────────────────────────
@pytest.mark.skipif(not HAS_DATA, reason="생성 데이터 없음")
def test_day_screen_locks_confirmed_after_approval(_temp_store):
    from streamlit.testing.v1 import AppTest
    day = AppTest.from_file(str(ROOT / "app" / "day_compare.py"), default_timeout=180)
    day.run()
    day.sidebar.selectbox[0].set_value(DATE).run()
    _btn(day, "모두 수락으로 채우기").click().run()
    _btn(day, "승인 적용").click().run()
    conf = {c["request_id"]: (c["worker_id"], c["arrive_min"]) for c in Store(_temp_store).confirmed(DATE)}
    assert conf
    day.run()   # 다시 계산: 확정이 잠금으로 들어간다
    assert not day.exception, [e.message for e in day.exception]
    assert any("잠금" in m.value for m in day.info)
    from planner.data import load_day
    from planner.locked import plan_with_locks
    s = Store(_temp_store)
    inst = load_day(ROOT / "data" / "processed", DATE, **s.day_inputs(DATE)).as_instance()
    locked = [{"request_id": k, "worker_id": v[0], "arrive_min": v[1]} for k, v in conf.items()]
    for strat in ("request_order", "nearest"):
        got = {a["request_id"]: (a["worker_id"], a["arrive_min"]) for a in plan_with_locks(inst, locked, strat).assignments}
        assert all(got[k] == v for k, v in conf.items())


@pytest.mark.skipif(not HAS_DATA, reason="생성 데이터 없음")
def test_request_scope_and_period_run(_temp_store):
    from streamlit.testing.v1 import AppTest
    day = AppTest.from_file(str(ROOT / "app" / "day_compare.py"), default_timeout=300)
    day.run()
    day.sidebar.selectbox[0].set_value(DATE).run()
    next(r for r in day.sidebar.radio if r.label == "서비스").set_value("가사지원만").run()
    assert not day.exception, [e.message for e in day.exception]
    assert any("이번 계산에서 뺐습니다" in m.value for m in day.info)
    next(m for m in day.multiselect if m.label == "날짜(최대 5일)").set_value(["2026-11-03", "2026-11-04"]).run()
    next(s for s in day.selectbox if s.label == "방식" and s.key == "period_algo").set_value("근거리").run()
    _btn(day, "기간 계산").click().run()
    assert not day.exception, [e.message for e in day.exception]
    table = next(df.value for df in day.dataframe if "실행 ID" in getattr(df.value, "columns", []))
    assert list(table["날짜"]) == ["2026-11-03", "2026-11-04"] and set(table["규칙 위반(건)"]) == {0}
    st = Store(_temp_store)
    from store.status import day_status
    assert day_status(st, ROOT / "data" / "processed", "2026-11-03", algorithm="근거리")["run"] is not None
