"""AT-25: 이동시간 행렬의 음수·NaN·무한대 구간은 배정 입력에서 뺀다(0분으로 바꾸지 않음)."""
import pandas as pd
import pytest

from planner.data import load_travel


def test_invalid_travel_rows_are_excluded(tmp_path):
    pd.DataFrame({"from_node": ["A", "A", "B", "B", "C"], "to_node": ["B", "C", "A", "C", "A"],
                  "minutes": [5.0, -3.0, float("nan"), float("inf"), 7.5], "source": "osrm"}).to_csv(
        tmp_path / "travel_matrix.csv", index=False)
    with pytest.warns(UserWarning, match="3"):
        t = load_travel(tmp_path)
    assert t == {("A", "B"): 5.0, ("C", "A"): 7.5}                 # 빠진 구간은 엔진에서 '경로 확인 불가'


def test_baseline_reports_route_unknown_for_excluded_leg():
    """빠진 구간은 엔진이 0분으로 쓰지 않고 ROUTE_UNKNOWN 으로 남긴다."""
    import json
    from pathlib import Path
    from planner.baseline import ROUTE_UNKNOWN, request_order
    inst = json.loads((Path(__file__).resolve().parent.parent / "fixtures" / "verify_set.json").read_text(encoding="utf-8"))
    inst["travel"] = [t for t in inst["travel"] if t["to_node"] != "N_B02"]     # R02 집으로 가는 구간이 모두 빠짐
    res = request_order(inst)
    assert "R02" not in {a["request_id"] for a in res.assignments}
    assert {u.request_id: u.reason for u in res.unassigned}["R02"] == ROUTE_UNKNOWN
