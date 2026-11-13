"""인력·근무 화면: 설정 저장 → 확정 일정과 충돌 알림(자동 변경 없음) → 근무표 CSV 가져오기."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not (ROOT / "data" / "processed" / "requests.csv").exists(),
                                reason="생성 데이터 없음")
DATE = "2026-11-02"


def _btn(at, label):
    return next(b for b in at.button if b.label == label)


def test_workers_page_flow(_temp_store):
    from streamlit.testing.v1 import AppTest
    from store import Store

    day = AppTest.from_file(str(ROOT / "app" / "day_compare.py"), default_timeout=180)
    day.run()
    day.sidebar.selectbox[0].set_value(DATE).run()
    _btn(day, "모두 수락으로 채우기").click().run()
    _btn(day, "승인 적용").click().run()
    s = Store(_temp_store)
    busy = s.confirmed(DATE)[0]["worker_id"]
    n_conf = len(s.confirmed(DATE))

    at = AppTest.from_file(str(ROOT / "app" / "workers.py"), default_timeout=180)
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    assert len(at.dataframe[0].value) == 20

    # 처리자 없이 저장 → 오류
    _btn(at, "저장").click().run()
    assert any("처리자" in e.value for e in at.error)

    # 확정 일정이 있는 인력을 그날 휴무로 → 충돌 알림, 확정은 그대로
    next(b for b in at.selectbox if b.key == "shift_date").set_value(DATE).run()
    next(b for b in at.selectbox if b.key == "shift_worker").set_value(busy).run()
    # 폼 안의 값은 '저장'을 누를 때 함께 넘어간다(따로 run 하지 않음)
    next(c for c in at.checkbox if c.label == "이날 휴무").check()
    next(t for t in at.text_input if t.key == "shift_actor").input("테스트")
    _btn(at, "이날 근무 저장").click().run()
    assert not at.exception, [e.message for e in at.exception]
    assert any("맞지 않는 확정 일정" in w.value for w in at.warning)
    s = Store(_temp_store)
    assert s.day_shifts(DATE)[busy]["off"] is True and len(s.confirmed(DATE)) == n_conf

    # 예시 근무표 → 정상 4행 가져오기(W99 오류)
    at.radio[0].set_value("예시 근무표(가상)").run()
    next(t for t in at.text_input if t.key == "csv_shift_actor").input("테스트").run()
    _btn(at, "정상 행 가져오기").click().run()
    assert not at.exception, [e.message for e in at.exception]
    assert any("오류 1행" in m.value for m in at.success)
    assert set(Store(_temp_store).day_shifts("2026-11-24")) == {"W01", "W05", "W12", "W14"}
