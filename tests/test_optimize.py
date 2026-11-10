"""Routing 최적화 테스트.

핵심 검사:
  1) 검증기 통과 (하드 제약 위반이 없어야 배정안으로 쓸 수 있다)
  2) 모든 요청이 배정되거나 미배정으로 기록된다(조용히 사라지지 않는다)
  3) 중복 배정 없음
  4) 상태코드가 명시적으로 기록된다(timeout 을 '미배정 0건'으로 숨기지 않는다)
  5) 검증세트에서 기준선보다 같거나 많이 배정한다
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from planner.optimize import (STATUS_ERROR, STATUS_INFEASIBLE, optimize)
from verifier import verify

# 검증세트는 요청 5건이라 최적해가 즉시 나온다. 시간 제한을 길게 주면
# 개선 여지가 없어도 OR-Tools 가 제한 시간을 꽉 채워 돌아 테스트만 느려진다.
TIME_LIMIT = 2

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "verify_set.json"


@pytest.fixture
def inst():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


# ── 기본 동작 ────────────────────────────────────────────────────────────

def test_optimizer_returns_valid_status(inst):
    r = optimize(inst, time_limit_sec=TIME_LIMIT)
    assert r.status != STATUS_ERROR, f"해가 없는 게 아니라 오류: {r.status}"


def test_optimizer_passes_verifier(inst):
    r = optimize(inst, time_limit_sec=TIME_LIMIT)
    rep = verify(inst, r.assignments)
    assert rep.ok, [v.as_dict() for v in rep.violations]


def test_no_request_is_assigned_twice(inst):
    r = optimize(inst, time_limit_sec=TIME_LIMIT)
    ids = [a["request_id"] for a in r.assignments]
    assert len(ids) == len(set(ids))


def test_every_request_is_accounted_for(inst):
    """모든 요청은 배정 또는 미배정으로 기록된다. 조용히 사라지면 안 된다."""
    r = optimize(inst, time_limit_sec=TIME_LIMIT)
    handled = {a["request_id"] for a in r.assignments} | \
              {u["request_id"] for u in r.unassigned}
    assert handled == {req["request_id"] for req in inst["requests"]}


def test_timeout_is_not_hidden_as_empty_result(inst):
    """CLAUDE.md 6절: timeout 을 '미배정 0건'으로 표시하지 않는다.

    매우 짧은 시간을 줘도 infeasible 이나 timeout 이 나올 뿐, STATUS_ERROR 는 아니어야 한다.
    동시에 미배정 + 배정의 합계는 전체 요청 수와 같아야 한다.
    """
    r = optimize(inst, time_limit_sec=1)
    total = len(r.assignments) + len(r.unassigned)
    assert total == len(inst["requests"]), \
        "timeout 이 일부 요청을 기록하지 않고 버렸다"
    assert r.status != STATUS_ERROR


def test_optimizer_beats_or_ties_baselines(inst):
    """검증세트에서 최적화가 기준선보다 같거나 많이 배정해야 한다."""
    from planner.baseline import nearest, request_order
    base_count = max(request_order(inst).assigned_count(),
                     nearest(inst).assigned_count())
    r = optimize(inst, time_limit_sec=TIME_LIMIT)
    assert r.assigned_count() >= base_count, \
        (f"최적화({r.assigned_count()}) < 기준선 최선({base_count}). "
         f"상태: {r.status}")


# ── 제약 원칙 ────────────────────────────────────────────────────────────

def test_infeasible_gives_unassigned_not_empty(inst):
    """배정할 수 없는 요청은 미배정으로 남고, '미배정 0건'처럼 보이지 않는다."""
    broken = json.loads(json.dumps(inst))  # deep copy
    for req in broken["requests"]:
        req["earliest_start_min"] = 1000
        req["latest_start_min"] = 900     # 역전된 시간창

    r = optimize(broken, time_limit_sec=5)

    # 미배정 페널티(disjunction)가 걸려 있으므로 아무것도 배정하지 않는 해가 정상이다.
    # 상태는 optimal 일 수 있고, 중요한 것은 배정 0건 + 미배정 기록이다.
    assert r.assignments == [], "역전된 시간창인데 배정이 나왔다"
    assert len(r.unassigned) == len(broken["requests"]), "미배정 기록이 빠졌다"
    assert all(u["reason"] for u in r.unassigned), "사유 없는 미배정이 있다"


def test_repair_pass_keeps_constraints(inst):
    """후처리로 덧붙인 배정도 하드 제약을 지켜야 한다.

    후처리는 '모델이 버린 요청을 실제 이동시간으로 다시 시도'하는 단계다.
    검증기를 통과하지 못하면 채택하지 않지만, 채택된 결과는 반드시 통과해야 한다.
    """
    r = optimize(inst, time_limit_sec=TIME_LIMIT, repair=True)
    rep = verify(inst, r.assignments)
    assert rep.ok, [v.as_dict() for v in rep.violations]


def test_repair_never_reduces_assignments(inst):
    """후처리는 덧붙이기만 한다. 배정 수가 줄면 안 된다."""
    without = optimize(inst, time_limit_sec=TIME_LIMIT, repair=False)
    with_rep = optimize(inst, time_limit_sec=TIME_LIMIT, repair=True)
    assert with_rep.assigned_count() >= without.assigned_count()


def test_repair_does_not_duplicate_requests(inst):
    """덧붙이기 단계에서 같은 요청이 두 번 들어가면 안 된다."""
    r = optimize(inst, time_limit_sec=TIME_LIMIT, repair=True)
    ids = [a["request_id"] for a in r.assignments]
    assert len(ids) == len(set(ids))


def test_verifier_is_independent_of_planner():
    """검증기는 엔진을 import 하지 않아야 교차검증이 성립한다.

    (최적화 모듈은 후처리 결과를 검증기로 확인하므로 verifier 를 import 한다.
     이는 자기 확인이 아니라 '독립 검증기에 물어보는' 것이므로 문제가 없다.
     금지해야 하는 방향은 verifier → planner 다.)
    """
    src = (ROOT / "verifier" / "__init__.py").read_text(encoding="utf-8")
    body = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "import planner" not in body
    assert "from planner" not in body


# ── 상태 코드 해석 (OR-Tools RoutingSearchStatus) ─────────────────────────
def test_status_success_is_feasible_not_optimal():
    """SUCCESS(1)는 해를 찾았다는 뜻일 뿐 최적 증명이 아니다."""
    from planner.optimize import STATUS_FEASIBLE, map_status
    assert map_status(1) == STATUS_FEASIBLE
    assert map_status(2) == STATUS_FEASIBLE


def test_status_optimal_infeasible_timeout():
    from planner.optimize import (STATUS_INFEASIBLE, STATUS_OPTIMAL,
                                  STATUS_TIMEOUT, map_status)
    assert map_status(7) == STATUS_OPTIMAL
    assert map_status(6) == STATUS_INFEASIBLE
    assert map_status(3) == STATUS_INFEASIBLE
    assert map_status(4) == STATUS_TIMEOUT


def test_status_unknown_is_error():
    from planner.optimize import STATUS_ERROR, map_status
    assert map_status(0) == STATUS_ERROR
    assert map_status(5) == STATUS_ERROR
    assert map_status(99) == STATUS_ERROR
