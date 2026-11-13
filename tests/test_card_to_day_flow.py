"""요청 카드 '확인 완료' → 하루 배정 비교 계산에 들어가는 흐름(화면 테스트).

두 화면은 앱 저장소(store, SQLite)로 이어진다. 새로고침해도 처리 기록과 요청이 남는다.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not (ROOT / "data" / "processed" / "requests.csv").exists(),
                                reason="생성 데이터 없음")


def test_confirm_card_puts_request_in_store(_temp_store):
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
    from store import Store
    cards = Store(_temp_store).ready_requests()
    assert len(cards) == 1
    req = cards[0]
    assert req["request_id"] == "카드-E01" and req["source"] == "card"
    assert any("배정 계산에 들어갔습니다" in m.value for m in at.info)
    # AI 값과 확정 값, 처리자가 함께 남는다(FR-11)
    hist = Store(_temp_store).card_history(Store(_temp_store).card_review_keys()[0])
    assert hist[-1]["reviewer"] == "테스트 담당자" and hist[-1]["extracted"] and hist[-1]["final"]

    # 새로고침(새 세션) 뒤에도 처리 기록이 보인다
    at2 = AppTest.from_file(str(ROOT / "app" / "request_cards.py"), default_timeout=120)
    at2.run()
    radio = at2.sidebar.radio[0]
    rules = next(i for i, o in enumerate(radio.options) if "규칙" in o)
    radio.set_value(radio.options[rules] if isinstance(radio.value, str) else rules).run()
    at2.selectbox[0].set_value(0).run()
    assert any("확인 완료" in m.value for m in at2.success)


def test_day_page_includes_confirmed_card(_temp_store):
    from streamlit.testing.v1 import AppTest
    from intake.to_request import card_to_request

    case = json.loads((ROOT / "fixtures/request_cards/cases.json").read_text(encoding="utf-8"))["cases"][0]
    final = {k: v["value"] for k, v in case["gold"].items()}
    req, probs = card_to_request(final, ROOT / "data" / "processed", case["case_id"], "2026-11-01T09:00")
    assert probs == []

    at = AppTest.from_file(str(ROOT / "app" / "day_compare.py"), default_timeout=180)
    from store import Store
    Store(_temp_store).save_card_review("k", card_id=case["case_id"], text_hash="h", extracted={}, final=final,
                                        action="확인 완료", reason="", reviewer="테스트", request=req)
    at.run()
    at.sidebar.selectbox[0].set_value(req["service_date"]).run()
    assert not at.exception, [e.message for e in at.exception]
    assert any("확인한 요청" in m.value for m in at.info)
    # 세 방식 모두 검증기를 거친다(위반이 있으면 화면에 오류 문구가 뜬다)
    assert any("위반 0건" in m.value for m in at.markdown) or any("위반 0건" in s.value for s in at.success) \
        or any("위반 0건" in c.value for c in at.caption)
