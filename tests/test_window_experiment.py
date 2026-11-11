"""시간창 폭 실험 변수 테스트."""
import copy
import json
from pathlib import Path

import pytest

from simulator.experiment import run_day, widen_windows
from verifier import verify

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "verify_set.json"


@pytest.fixture
def inst():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _by_id(instance):
    return {r["request_id"]: r for r in instance["requests"]}


def test_original_not_mutated(inst):
    before = copy.deepcopy(inst)
    widen_windows(inst, escort_width=120, household_extra=60)
    assert inst == before


def test_none_and_zero_keep_windows(inst):
    assert widen_windows(inst)["requests"] == inst["requests"]


def test_escort_keeps_latest_and_moves_earliest(inst):
    out = _by_id(widen_windows(inst, escort_width=120))
    lo = min(w["shift_start_min"] for w in inst["workers"])
    for rid, r in _by_id(inst).items():
        o = out[rid]
        if r["service_type"] == "hospital_escort":
            assert o["latest_start_min"] == r["latest_start_min"]
            assert o["earliest_start_min"] == max(lo, r["latest_start_min"] - 120)
        else:
            assert (o["earliest_start_min"], o["latest_start_min"]) == \
                   (r["earliest_start_min"], r["latest_start_min"])


def test_household_widened_within_shift(inst):
    out = _by_id(widen_windows(inst, household_extra=60))
    lo = min(w["shift_start_min"] for w in inst["workers"])
    hi = max(w["shift_end_min"] for w in inst["workers"])
    for rid, r in _by_id(inst).items():
        o = out[rid]
        if r["service_type"] == "household_support":
            assert o["earliest_start_min"] == max(lo, r["earliest_start_min"] - 60)
            assert o["latest_start_min"] == min(hi - r["service_minutes"], r["latest_start_min"] + 60)
            assert o["earliest_start_min"] <= r["earliest_start_min"]
            assert o["latest_start_min"] >= r["latest_start_min"]


@pytest.mark.parametrize("algo", ["request_order", "nearest", "optimize"])
def test_widened_runs_verify_clean(inst, algo):
    wide = widen_windows(inst, escort_width=120, household_extra=60)
    res = run_day(wide, algo, time_limit_sec=2)
    assert res.violations == 0
    assert res.assigned >= 0 and res.requests_escort == sum(
        1 for r in wide["requests"] if r["service_type"] == "hospital_escort")
    assert 0 <= res.assigned_escort <= res.requests_escort
    row = res.as_row()
    assert "assigned_escort" in row and "requests_escort" in row
