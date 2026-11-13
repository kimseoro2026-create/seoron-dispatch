"""인력 일정(읽기 전용, W08 최소판): 확정 일정만, 필요한 정보만."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not (ROOT / "data" / "processed" / "requests.csv").exists(),
                                reason="생성 데이터 없음")
DATE = "2026-11-03"


def test_shows_only_confirmed_jobs_of_one_worker(_temp_store):
    from streamlit.testing.v1 import AppTest
    from planner.approval import ACCEPT, Decision
    from planner.baseline import nearest
    from planner.data import load_day
    from store import Store

    s = Store(_temp_store)
    at = AppTest.from_file(str(ROOT / "app" / "worker_schedule.py"), default_timeout=120)
    at.run()
    assert not at.exception and any("확정 일정이 없습니다" in m.value for m in at.info)

    inst = load_day(ROOT / "data" / "processed", DATE).as_instance()
    prop = nearest(inst).assignments
    first_w = sorted(a["worker_id"] for a in prop)[0]
    mine = [a for a in prop if a["worker_id"] == first_w]
    s.approve(DATE, inst, prop, [Decision(a["request_id"], ACCEPT) for a in mine], expected_version=0)

    at = AppTest.from_file(str(ROOT / "app" / "worker_schedule.py"), default_timeout=120)
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    t = at.dataframe[0].value
    assert len(t) == len(mine) and {"도착", "끝", "필요한 위치", "지원 조건"} <= set(t.columns)
    assert "이름" not in " ".join(t.columns)
