"""취소·지연 대응 화면: 승인된 일정 → 취소 등록 → 바뀔 일정 확인 → 적용 → 현황·하루 계산에 반영."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not (ROOT / "data" / "processed" / "requests.csv").exists(),
                                reason="생성 데이터 없음")
DATE = "2026-11-02"


def _btn(at, label):
    return next(b for b in at.button if b.label == label)


def test_cancel_flow(_temp_store):
    from streamlit.testing.v1 import AppTest
    from store import Store

    day = AppTest.from_file(str(ROOT / "app" / "day_compare.py"), default_timeout=180)
    day.run()
    day.sidebar.selectbox[0].set_value(DATE).run()
    _btn(day, "모두 수락으로 채우기").click().run()
    _btn(day, "승인 적용").click().run()
    s = Store(_temp_store)
    conf = s.confirmed(DATE)
    target = next(c for c in sorted(conf, key=lambda c: -c["arrive_min"]) if c["arrive_min"] >= 660)

    ev = AppTest.from_file(str(ROOT / "app" / "events.py"), default_timeout=180)
    ev.run()
    ev.sidebar.selectbox[0].set_value(DATE).run()
    assert not ev.exception, [e.message for e in ev.exception]
    req_box = next(b for b in ev.selectbox if b.label == "요청")
    req_box.set_value(target["request_id"]).run()
    _btn(ev, "바뀔 일정 계산").click().run()
    assert not ev.exception, [e.message for e in ev.exception]
    assert any("바뀌는 일정" in m.value for m in ev.success)
    table = ev.dataframe[0].value
    assert (table["요청"] == target["request_id"]).any() and "취소" in set(table["바뀜"])

    # 처리자 없이 적용 → 막힘, 넣고 적용 → 반영
    _btn(ev, "이대로 적용").click().run()
    assert any("처리자" in e.value for e in ev.error)
    next(t for t in ev.text_input if t.key == "event_actor").input("테스트").run()
    _btn(ev, "이대로 적용").click().run()
    assert not ev.exception, [e.message for e in ev.exception]
    s = Store(_temp_store)
    assert target["request_id"] not in {c["request_id"] for c in s.confirmed(DATE)}
    assert target["request_id"] in s.cancelled_ids(DATE) and len(s.events(DATE)) == 1

    # 현황: 취소 1건, 하루 계산에서는 빠진다
    stp = AppTest.from_file(str(ROOT / "app" / "status_board.py"), default_timeout=120)
    stp.run()
    stp.sidebar.selectbox[0].set_value(DATE).run()
    assert next(m.value for m in stp.metric if m.label == "취소") == "1건"
    from planner.data import load_day
    inst = load_day(ROOT / "data" / "processed", DATE, extra_requests=s.ready_requests(DATE),
                    exclude_ids=s.cancelled_ids(DATE))
    assert target["request_id"] not in {r["request_id"] for r in inst.requests}
