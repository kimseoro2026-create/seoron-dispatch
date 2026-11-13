"""담당자 승인: AI 최적화가 기본으로 골라지고, '모두 수락으로 채우기' → '승인 적용'으로 확정되는지 확인한다.

'모두 수락'은 표의 기본값만 바꾼다. '승인 적용'을 누르기 전에는 확정이 생기지 않아야 한다.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _btn(at, label):
    return next(b for b in at.button if b.label == label)


def test_accept_all_then_apply(monkeypatch):
    if not (ROOT / "data" / "processed" / "requests.csv").exists():
        pytest.skip("생성 데이터 없음")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app" / "day_compare.py"), default_timeout=180)
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    sel = next(s for s in at.selectbox if s.label == "승인할 배정안")
    assert sel.value == "AI 최적화"

    _btn(at, "모두 수락으로 채우기").click().run()
    assert not at.exception, [e.message for e in at.exception]
    assert not at.session_state["gate"].confirmed      # 채우기만으로는 확정되지 않는다

    _btn(at, "승인 적용").click().run()
    assert not at.exception, [e.message for e in at.exception]
    assert at.session_state["gate"].confirmed           # 승인 적용 후에만 확정
    assert any("확정된 배정" in m.value for m in at.markdown)
