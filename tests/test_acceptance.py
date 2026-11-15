"""PRD 13장 인수 시험 중 다른 파일에 번호 없이 흩어져 있던 것과 빠져 있던 것을 모았다.

번호별로 어느 테스트가 검증하는지는 docs/acceptance_tests.md 에 정리한다.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from planner.baseline import NO_SKILL_MATCH, TIME_WINDOW_CONFLICT, nearest, request_order
from verifier import verify
from verifier import OVERLAP

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "verify_set.json"
DATA = ROOT / "data" / "processed"


@pytest.fixture
def inst():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _reasons(res) -> dict[str, str]:
    return {(u["request_id"] if isinstance(u, dict) else u.request_id):
            (u["reason"] if isinstance(u, dict) else u.reason) for u in res.unassigned}


# ── AT-03: 동행 중 병원 체류와 가사 요청 중첩 → 동행 인력에 가사 배정 금지 ─────────────
def test_at03_household_inside_escort_hospital_stay_is_overlap(inst):
    """W01 이 R01 동행(600 픽업, 병원 체류 120분) 중일 때 가사 R05 를 넣으면 검증기가 막는다."""
    inst["travel"].append({"from_node": "N_B01", "to_node": "N_B05", "minutes": 10, "source": "estimate"})
    a = [{"request_id": "R01", "worker_id": "W01", "arrive_min": 600},
         {"request_id": "R05", "worker_id": "W01", "arrive_min": 700}]   # 병원 체류 한가운데
    rep = verify(inst, a)
    assert not rep.ok and OVERLAP in {v.code for v in rep.violations}


@pytest.mark.parametrize("algo", [request_order, nearest])
def test_at03_engines_never_put_household_inside_escort(inst, algo):
    """기준선이 낸 배정안에서 동행 인력의 동행 점유 구간 안에 다른 요청이 없다(검증기 통과)."""
    res = algo(inst)
    assert verify(inst, res.assignments).ok


# ── AT-04: 예약 시각에 맞춰 도착할 수 없으면 적기 배정 불가 + 시간 충돌 표시 ──────────
@pytest.mark.parametrize("algo", [request_order, nearest])
def test_at04_unreachable_appointment_is_time_conflict(inst, algo):
    inst = copy.deepcopy(inst)
    for r in inst["requests"]:
        if r["request_id"] == "R04":
            # 근무 시작 540. 예약 550·여유 15 → 병원 535 까지 도착해야 하므로 누구도 못 맞춘다.
            r.update(appointment_min=550, earliest_start_min=540, latest_start_min=550)
    res = algo(inst)
    assert "R04" not in {a["request_id"] for a in res.assignments}
    assert _reasons(res)["R04"] == TIME_WINDOW_CONFLICT
    assert verify(inst, res.assignments).ok


def test_at04_optimizer_also_leaves_it_unassigned(inst):
    from planner.optimize import optimize
    inst = copy.deepcopy(inst)
    for r in inst["requests"]:
        if r["request_id"] == "R04":
            r.update(appointment_min=550, earliest_start_min=540, latest_start_min=550)
    res = optimize(inst, time_limit_sec=5)
    assert "R04" not in {a["request_id"] for a in res.assignments}
    assert "R04" in _reasons(res)                         # 최적화는 원인을 증명하지 못하면 미확정 사유로 남긴다
    assert verify(inst, res.assignments).ok


# ── AT-05: 자격 만료 → 후보 제외 + 이유 표시 ───────────────────────────────
def test_at05_expired_skill_gives_no_skill_reason(tmp_path):
    if not (DATA / "requests.csv").exists():
        pytest.skip("생성 데이터 없음")
    from planner.data import load_day
    from store import Store
    from tests.test_workers import _profile
    s = Store(tmp_path / "s.db")
    date = "2026-11-03"
    base = load_day(DATA, date)
    for w in base.workers:
        if "hospital_escort" in w["skills"]:
            s.save_worker_profile(w["worker_id"], _profile(skills=[{"code": "hospital_escort", "valid_to": "2026-11-01"},
                                                                   {"code": "household_support", "valid_to": None}]),
                                  actor="A")
    inst = load_day(DATA, date, **s.day_inputs(date)).as_instance()
    escort_ids = {r["request_id"] for r in inst["requests"] if r["service_type"] == "hospital_escort"}
    assert escort_ids
    res = request_order(inst)
    reasons = _reasons(res)
    assert escort_ids <= set(reasons) and {reasons[i] for i in escort_ids} == {NO_SKILL_MATCH}


# ── AT-14: 같은 사람 동행·가사 2건 → 요청 2건·고유 인원 1명 ───────────────────────
def test_at14_same_person_escort_and_household_counts_once(inst):
    from simulator.experiment import run_day
    inst = copy.deepcopy(inst)
    for r in inst["requests"]:
        if r["request_id"] == "R04":
            r["beneficiary_id"] = "B02"                     # R02(가사)와 같은 사람의 동행
    res = run_day(inst, "request_order", seed=1)
    got = {a["request_id"] for a in request_order(inst).assignments}
    assert {"R02", "R04"} <= got
    assert res.assigned == len(got) and res.unique_beneficiaries == len(got) - 1


# ── AT-18: 계획 시점에 아직 통보되지 않은 취소를 쓰지 않는다 ─────────────────────
@pytest.mark.parametrize("algorithm", ["request_order", "nearest"])
def test_at18_initial_plan_ignores_future_cancellations(algorithm):
    """취소 표시를 붙여도 하루 시작 계획은 표시가 없을 때와 똑같다. 나중에 취소될 요청도 처음엔 배정된다."""
    from planner.reschedule import replay
    from tests.test_reschedule import _instance
    clean = _instance()
    marked = _instance()
    for r in marked["requests"]:
        r["cancelled_same_day"] = True
        r["cancel_notice_min_before"] = 0
    a = replay(clean, algorithm).initial_assignments
    b = replay(marked, algorithm).initial_assignments
    assert a == b and len(b) > 0


def test_at18_optimizer_does_not_read_cancel_flags():
    """최적화 엔진 코드에 취소 결과 필드 이름이 나오지 않는다(기준선은 test_baselines 가 같은 검사를 한다)."""
    src = (ROOT / "planner" / "optimize.py").read_text(encoding="utf-8")
    assert "cancelled_same_day" not in src and "cancel_notice" not in src


# ── AT-16: 솔버 시간 초과·유효해 없음 → 기존 일정 유지, 원인 미확정 ─────────────────
@pytest.mark.parametrize("status, assignments, ok", [
    ("timeout", [], False), ("infeasible", [], False), ("error", [], False),
    ("timeout", [{"request_id": "R1"}], True),         # 시간 안에 해를 찾았으면 검증을 거쳐 쓸 수 있다
    ("optimal", [], True), ("feasible", [{"request_id": "R1"}], True)])
def test_at16_failed_solve_is_not_a_plan(status, assignments, ok):
    from planner.optimize import plan_usable
    assert plan_usable(status, assignments) is ok


def test_at16_failed_run_keeps_confirmed_schedule(tmp_path):
    """해를 못 찾은 실행을 기록해도 확정 일정과 일정 버전은 그대로다."""
    from store import Store
    from store.db import ACCEPT, Decision
    from tests.test_store import DATE, _instance, _proposal
    s = Store(tmp_path / "s.db")
    s.approve(DATE, _instance(), _proposal(), [Decision("R01", ACCEPT)], expected_version=0)
    before, ver = s.confirmed(DATE), s.schedule_version(DATE)
    s.record_run(DATE, "최적화", _instance(), config={"time_limit_sec": 1}, status="timeout", solve_seconds=1.0,
                 metrics={"assigned": 0, "unassigned": 0, "violations": 0}, assignments=None, unassigned=None)
    assert s.confirmed(DATE) == before and s.schedule_version(DATE) == ver


def test_at16_timeout_leaves_unassigned_as_unresolved(inst):
    """시간 초과로 버린 요청의 사유는 '계산 중 미해결'(원인 미확정)이지 확정 사유가 아니다."""
    from planner.optimize import SOLVER_UNRESOLVED, optimize
    res = optimize(inst, time_limit_sec=5)
    for u in res.unassigned:
        r = u if isinstance(u, dict) else u.__dict__
        if r["reason"] == SOLVER_UNRESOLVED:
            assert r.get("certainty") != "verified"


# ── AT-02: 날짜·시간이 빠진 추출 → 확인 필요, 배정 대상 제외 ──────────────────────
@pytest.mark.parametrize("missing", ["service_date", "earliest_start"])
def test_at02_card_without_date_or_time_never_reaches_assignment(tmp_path, missing):
    import pandas as pd
    from intake.to_request import card_to_request
    from store import Store
    pd.DataFrame({"beneficiary_id": ["B0001"], "lat": [37.2], "lon": [127.0]}).to_csv(
        tmp_path / "beneficiaries.csv", index=False)
    pd.DataFrame({"hospital_id": ["H1"], "name": ["가상종합병원"], "lat": [37.3], "lon": [127.1]}).to_csv(
        tmp_path / "hospitals.csv", index=False)
    final = {"beneficiary_id": "B0001", "service_type": "household_support", "service_date": "2026-11-20",
             "earliest_start": "10:00", "latest_start": "12:00", "service_minutes": 120, missing: None}
    req, probs = card_to_request(final, tmp_path, card_id="V01", confirmed_at="2026-11-19T10:00")
    assert req is None and probs                                   # 배정 요청으로 바꾸지 않는다
    s = Store(tmp_path / "s.db")
    with pytest.raises(ValueError):                                # 요청 없이 '확인 완료' 불가
        s.save_card_review("k1", card_id="V01", text_hash="h", extracted=final, final=final, action="확인 완료",
                           reason="", reviewer="담당자", request=None)
    s.save_card_review("k1", card_id="V01", text_hash="h", extracted=final, final=final, action="보류",
                       reason="날짜 확인 필요", reviewer="담당자", request=None)
    assert s.ready_requests() == []                                # 배정 계산에 들어가지 않는다


# ── AT-29: 성공·실패 점검 기록에 키·원문·개인 위치 없음 ──────────────────────────
def test_at29_transit_check_file_has_no_key_raw_body_or_coordinates(tmp_path):
    import datetime as dt
    from adapters import transit
    from f0.apiclient import PlatformClient
    from tests.test_apiclient_errors import Resp, Session
    key = "SECRET-KEY-7f3a9"
    cfg = {"key_header": "api_user_key_id", "retries": 0, "retry_backoff_sec": 0,
           "transit": {"url": "https://example.test/t", "params": {"SX": "{sx}", "SY": "{sy}", "EX": "{ex}",
                                                                   "EY": "{ey}"}}}
    now = dt.datetime(2026, 11, 16, 9, tzinfo=transit.KST)
    frm, to = (37.201234, 127.071234), (37.211234, 127.081234)
    rows = []
    for body in ('{"error": {"code": "-98", "message": "출, 도착지가 700m이내입니다."}}',
                 '{"result": {"path": [{"pathType": 2, "info": {"totalTime": 25, "totalDistance": 7000, '
                 '"totalWalk": 400}}]}}', "<html>Bad Gateway " + key + "</html>"):
        c = PlatformClient(api_key=key, config=cfg)
        c.session = Session(Resp(200, body, "application/json"))
        rows.append(transit.estimate(c, "BENEFICIARY:B1", frm, "HOSP:H1", to, now))
    assert [r["status"] for r in rows] == ["provider_error", "ok", "invalid_response"]
    text = transit.save_cache(rows, tmp_path).read_text(encoding="utf-8-sig")
    assert key not in text                                         # 키
    assert '"error"' not in text and "<html>" not in text and '"result"' not in text   # 응답 원문
    for v in (*frm, *to):
        assert f"{v}" not in text                                  # 집·병원 좌표(노드 ID만 남김)


# ── AT-15: 고정급 인력의 이동 감소 → 수행 여력 개선, 고정 인건비 절감 없음 ─────────────
def test_at15_travel_saving_is_capacity_not_labor_cost():
    from simulator.report import travel_saving, travel_saving_text
    cost = {"hourly_krw": 12000, "monthly_salary_krw": 2_500_000, "transport_krw_per_min": None}
    s = travel_saving(95, cost)
    assert s == {"freed_minutes": 95.0, "labor_saving_krw": 0, "transport_saving_krw": None}
    t = travel_saving_text(95, cost, "신청순")
    assert "1시간 35분" in t and "인건비는 줄지 않습니다" in t and "계산하지 않았습니다" in t
    assert travel_saving(30, dict(cost, transport_krw_per_min=100))["transport_saving_krw"] == 3000.0
    assert travel_saving_text(0, cost, "신청순") is None and travel_saving_text(-20, cost, "신청순") is None


# ── AT-20: 같은 입력·버전·시드 → 저장된 스냅샷으로 재현 ─────────────────────────
def test_at20_saved_snapshot_reproduces_baseline_run(tmp_path, inst):
    from store import Store
    s = Store(tmp_path / "s.db")
    res = request_order(inst)
    rid = s.record_run(inst["service_date"], "신청순", inst, config={}, status="feasible", solve_seconds=None,
                       metrics={"assigned": len(res.assignments)}, assignments=res.assignments,
                       unassigned=[u.as_dict() for u in res.unassigned])
    snap = Store(tmp_path / "s.db").run_snapshot(rid)              # 다시 연 저장소에서
    again = request_order(snap)
    assert again.assignments == s.run(rid)["assignments"]
    assert s.run(rid)["snapshot_hash"] and "snapshot_gz" not in s.run(rid)


def test_at20_tampered_snapshot_is_refused(tmp_path, inst):
    import sqlite3
    import zlib
    from store import Store
    s = Store(tmp_path / "s.db")
    rid = s.record_run(inst["service_date"], "신청순", inst, config={}, status="feasible", solve_seconds=None,
                       metrics={})
    con = sqlite3.connect(tmp_path / "s.db")
    body = json.loads(zlib.decompress(con.execute("SELECT snapshot_gz FROM planning_runs").fetchone()[0]))
    body["requests"][0]["service_minutes"] += 30
    con.execute("UPDATE planning_runs SET snapshot_gz=?", (zlib.compress(json.dumps(body).encode()),))
    con.commit()
    con.close()
    with pytest.raises(ValueError):
        s.run_snapshot(rid)
