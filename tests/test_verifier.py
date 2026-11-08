"""검증기 테스트.

두 종류를 확인한다.
1) 손으로 푼 정답과 계산값이 같은가 (검증기가 맞게 계산하는가)
2) 일부러 틀린 배정을 넣으면 각 제약 위반을 잡는가 (검증기가 실제로 검사하는가)
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from verifier import (
    APPOINTMENT_ARRIVAL, BREAK_VIOLATION, DATA_INCONSISTENT, MISSING_REQUIRED,
    NO_SKILL_MATCH, ONE_WORKER_PER_REQUEST, OVERLAP, OVERTIME, ROUTE_UNKNOWN,
    SHIFT_VIOLATION, TIME_WINDOW_CONFLICT, verify,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "verify_set.json"
ANSWER = ROOT / "fixtures" / "verify_set_answer.json"


@pytest.fixture
def base():
    with open(FIXTURE, encoding="utf-8") as f:
        inst = json.load(f)
    return inst, copy.deepcopy(inst["assignments"])


@pytest.fixture
def expected():
    with open(ANSWER, encoding="utf-8") as f:
        return json.load(f)["expected"]


def _codes(inst, assigns):
    return verify(inst, assigns).codes()


# ── 1. 손 계산 정답과 일치 ────────────────────────────────────────────────

def test_fixture_is_feasible(base, expected):
    inst, assigns = base
    rep = verify(inst, assigns)
    assert rep.violations == [], [v.as_dict() for v in rep.violations]
    assert rep.ok is True
    assert rep.violations == [] and rep.unassigned == []


def test_timeline_matches_hand_calculation(base, expected):
    inst, assigns = base
    rep = verify(inst, assigns)
    got = {t.request_id: t for t in rep.timelines}
    assert sorted(got) == sorted(e["request_id"] for e in expected["timelines"])
    for e in expected["timelines"]:
        t = got[e["request_id"]]
        assert t.worker_id == e["worker_id"], e["request_id"]
        assert t.depart_min == e["depart_min"], e["request_id"]
        assert t.arrive_min == e["arrive_min"], e["request_id"]
        assert t.end_min == e["end_min"], e["request_id"]
        assert t.travel_min == e["travel_min"], e["request_id"]
        assert t.hospital_arrive_min == e["hospital_arrive_min"], e["request_id"]


def test_work_minutes_match(base, expected):
    inst, assigns = base
    rep = verify(inst, assigns)
    assert rep.work_minutes == expected["work_minutes"]


# ── 2. 제약 위반을 실제로 잡는가 ──────────────────────────────────────────

def test_detects_duplicate_assignment(base):
    inst, assigns = base
    assigns.append({"request_id": "R01", "worker_id": "W02", "arrive_min": 600})
    rep = verify(inst, assigns)
    assert ONE_WORKER_PER_REQUEST in rep.codes()


def test_detects_skill_mismatch(base):
    inst, _ = base
    # W02 는 household_support 만 가진다. hospital_escort 요청 R01 만 W02 에게 배정.
    assigns = [{"request_id": "R01", "worker_id": "W02", "arrive_min": 600}]
    codes = _codes(inst, assigns)
    assert NO_SKILL_MATCH in codes


def test_detects_overlap(base):
    inst, assigns = base
    # R05 를 700 이 아니라 650 에 도착시킨다. 직전 R02 가 690 에 끝나 이동이 겹친다.
    for a in assigns:
        if a["request_id"] == "R05":
            a["arrive_min"] = 650
    assert OVERLAP in _codes(inst, assigns)


def test_detects_time_window_conflict(base):
    inst, assigns = base
    # R03 시간창은 780~900. 950 도착은 늦다(휴게·근무종료는 건드리지 않는다).
    for a in assigns:
        if a["request_id"] == "R03":
            a["arrive_min"] = 950
    codes = _codes(inst, assigns)
    assert TIME_WINDOW_CONFLICT in codes
    assert BREAK_VIOLATION not in codes
    assert SHIFT_VIOLATION not in codes


def test_detects_appointment_arrival(base):
    inst, assigns = base
    # R04: 예약 900, 여유 15 → 병원 도착 885 이하여야 한다.
    # 픽업 870 이면 병원 890 으로 늦다.
    for a in assigns:
        if a["request_id"] == "R04":
            a["arrive_min"] = 870
    codes = _codes(inst, assigns)
    assert APPOINTMENT_ARRIVAL in codes
    assert OVERLAP not in codes


def test_detects_early_shift_violation(base):
    inst, assigns = base
    for a in assigns:
        if a["request_id"] == "R02":
            a["arrive_min"] = 530      # 출발 520 < 근무시작 540
    assert SHIFT_VIOLATION in _codes(inst, assigns)


def test_detects_late_shift_violation(base):
    inst, assigns = base
    # 점유가 근무종료 1080 을 넘는다. 시간창도 함께 깨지므로 SHIFT 만 확인한다.
    for a in assigns:
        if a["request_id"] == "R03":
            a["arrive_min"] = 1000
    assert SHIFT_VIOLATION in _codes(inst, assigns)


def test_detects_break_violation(base):
    inst, assigns = base
    # R03 800~890 은 휴게 780~840 과 겹친다. 시간창·근무시간은 지킨다.
    for a in assigns:
        if a["request_id"] == "R03":
            a["arrive_min"] = 800
    codes = _codes(inst, assigns)
    assert BREAK_VIOLATION in codes
    assert TIME_WINDOW_CONFLICT not in codes
    assert SHIFT_VIOLATION not in codes


def test_detects_overtime(base):
    inst, assigns = base
    for w in inst["workers"]:
        if w["worker_id"] == "W01":
            w["max_work_minutes"] = 300     # 실제 385분
    rep = verify(inst, assigns)
    assert OVERTIME in rep.codes()
    assert rep.work_minutes["W01"] == 385


def test_missing_travel_is_not_zeroed(base):
    inst, assigns = base
    # R03 이동 구간(N_B05 → N_B03)을 지운다. 0분으로 대체하지 않고 위반으로 남겨야 한다.
    inst["travel"] = [t for t in inst["travel"]
                      if not (t["from_node"] == "N_B05" and t["to_node"] == "N_B03")]
    rep = verify(inst, assigns)
    assert ROUTE_UNKNOWN in rep.codes()
    assert rep.ok is False           # 추정으로 채워 통과시키지 않는다


def test_missing_required_field(base):
    inst, assigns = base
    del inst["requests"][0]["appointment_min"]      # R01 병원동행 필수값
    assert MISSING_REQUIRED in _codes(inst, assigns)


def test_detects_inconsistent_window(base):
    inst, assigns = base
    for r in inst["requests"]:
        if r["request_id"] == "R02":
            r["earliest_start_min"] = 900       # latest 720 보다 뒤
    assert DATA_INCONSISTENT in _codes(inst, assigns)


def test_unassigned_is_reported_not_a_violation(base):
    inst, assigns = base
    assigns = [a for a in assigns if a["request_id"] != "R03"]
    rep = verify(inst, assigns)
    assert rep.unassigned == ["R03"]
    assert rep.ok is True                     # 미배정 자체는 하드 제약 위반이 아니다


def test_unknown_ids_are_flagged(base):
    inst, assigns = base
    assigns.append({"request_id": "R99", "worker_id": "W01", "arrive_min": 600})
    assigns.append({"request_id": "R02", "worker_id": "W99", "arrive_min": 600})
    codes = _codes(inst, assigns)
    assert "UNKNOWN_REQUEST" in codes
    assert "UNKNOWN_WORKER" in codes


# ── 3. 엔진과의 독립성 ────────────────────────────────────────────────────

def test_verifier_does_not_import_planner():
    """검증기가 엔진 코드를 import 하면 자기 확인이 되어 교차검증이 안 된다."""
    src = (ROOT / "verifier" / "__init__.py").read_text(encoding="utf-8")
    body = "\n".join(line for line in src.splitlines()
                     if not line.strip().startswith("#"))
    assert "import planner" not in body
    assert "from planner" not in body
