"""동행 인력 수 실험 변수 테스트."""
import copy
import json
from pathlib import Path

import pytest

from simulator.experiment import change_staffing, run_day

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "verify_set.json"
E = "hospital_escort"


@pytest.fixture
def inst():
    d = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # 검증세트는 2명 모두 동행 가능 → 한 명을 가사 전담으로 바꿔 교육 실험이 의미 있게 한다
    d["workers"][1]["skills"] = ["household_support"]
    return d


def _escort_ids(instance):
    return sorted(w["worker_id"] for w in instance["workers"] if E in w["skills"])


def test_original_not_mutated(inst):
    before = copy.deepcopy(inst)
    change_staffing(inst, retrain=1, add=2)
    assert inst == before


def test_zero_keeps_workers(inst):
    assert change_staffing(inst)["workers"] == inst["workers"]


def test_retrain_adds_skill_same_headcount(inst):
    out = change_staffing(inst, retrain=1)
    assert len(out["workers"]) == len(inst["workers"])
    assert len(_escort_ids(out)) == len(_escort_ids(inst)) + 1
    assert out["requests"] == inst["requests"]


def test_retrain_too_many_raises(inst):
    with pytest.raises(ValueError):
        change_staffing(inst, retrain=5)


def test_add_clones_existing_escort(inst):
    out = change_staffing(inst, add=2)
    new = [w for w in out["workers"] if w["worker_id"].startswith("WADD")]
    assert [w["worker_id"] for w in new] == ["WADD01", "WADD02"]
    src = [w for w in inst["workers"] if E in w["skills"]][0]
    for w in new:
        assert E in w["skills"]
        assert w["base_node"] == src["base_node"]
        assert w["shift_start_min"] == src["shift_start_min"]
    assert len({w["worker_id"] for w in out["workers"]}) == len(out["workers"])


@pytest.mark.parametrize("algo", ["request_order", "nearest", "optimize"])
def test_changed_staffing_verifies_clean(inst, algo):
    base = run_day(inst, algo, time_limit_sec=2)
    more = run_day(change_staffing(inst, retrain=1, add=1), algo, time_limit_sec=2)
    assert base.violations == 0 and more.violations == 0
    assert more.assigned >= 0


def test_stagger_breaks_cycles_and_keeps_length(inst):
    from simulator.experiment import stagger_breaks
    before = copy.deepcopy(inst)
    out = stagger_breaks(inst, starts=[660, 780])
    assert inst == before
    by = {w["worker_id"]: w for w in out["workers"]}
    ids = sorted(by)
    assert [by[i]["break_start_min"] for i in ids] == [660, 780][: len(ids)]
    for w0 in inst["workers"]:
        assert by[w0["worker_id"]]["break_minutes"] == w0["break_minutes"]


def test_stagger_breaks_outside_shift_raises(inst):
    from simulator.experiment import stagger_breaks
    with pytest.raises(ValueError):
        stagger_breaks(inst, starts=[100])


@pytest.mark.parametrize("algo", ["request_order", "nearest", "optimize"])
def test_staggered_verifies_clean(inst, algo):
    from simulator.experiment import stagger_breaks
    res = run_day(stagger_breaks(change_staffing(inst, add=1)), algo, time_limit_sec=2)
    assert res.violations == 0
