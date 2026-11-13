"""요청 카드 '확인 완료' → 하루 배정 비교 계산에 들어가는 흐름(화면 테스트).

두 화면은 같은 브라우저 세션(st.session_state['card_requests'])으로 이어진다.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not (ROOT / "data" / "processed" / "requests.csv").exists(),
                                reason="생성 데이터 없음")


def test_confirm_card_puts_request_in_session():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app" / "request_cards.py"), default_timeout=120)
    at.run()
    radio = at.sidebar.radio[0]
    rules = next(i for i, o in enumerate(radio.options) if "규칙" in o)
    radio.set_value(radio.options[rules] if isinstance(radio.value, str) else rules).run()
    at.selectbox[0].set_value(0).run()                       # 첫 예시 메모(E01 · 표준 메모)
    next(t for t in at.text_input if t.label == "처리자").input("테스트 담당자").run()
    next(b for b in at.button if b.label == "처리 적용").click().run()
    assert not at.exception, [e.message for e in at.exception]
    cards = at.session_state["card_requests"]
    assert len(cards) == 1
    req = next(iter(cards.values()))
    assert req["request_id"] == "카드-E01" and req["source"] == "card"
    assert any("배정 계산에 들어갔습니다" in m.value for m in at.info)


def test_day_page_includes_confirmed_card():
    from streamlit.testing.v1 import AppTest
    from intake.to_request import card_to_request

    case = json.loads((ROOT / "fixtures/request_cards/cases.json").read_text(encoding="utf-8"))["cases"][0]
    final = {k: v["value"] for k, v in case["gold"].items()}
    req, probs = card_to_request(final, ROOT / "data" / "processed", case["case_id"], "2026-11-01T09:00")
    assert probs == []

    at = AppTest.from_file(str(ROOT / "app" / "day_compare.py"), default_timeout=180)
    at.session_state["card_requests"] = {"k": req}
    at.run()
    at.sidebar.selectbox[0].set_value(req["service_date"]).run()
    assert not at.exception, [e.message for e in at.exception]
    assert any("확인한 요청" in m.value for m in at.info)
    # 세 방식 모두 검증기를 거친다(위반이 있으면 화면에 오류 문구가 뜬다)
    assert any("위반 0건" in m.value for m in at.markdown) or any("위반 0건" in s.value for s in at.success) \
        or any("위반 0건" in c.value for c in at.caption)
