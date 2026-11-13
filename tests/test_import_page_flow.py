"""자료 불러오기 화면: 예시 CSV → 미리보기(오류 행 표시) → 가져오기(draft) → 확인 완료 → 하루 배정 계산에 포함."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not (ROOT / "data" / "processed" / "requests.csv").exists(),
                                reason="생성 데이터 없음")


def _btn(at, label):
    return next(b for b in at.button if b.label == label)


def test_sample_import_review_and_day_calc(_temp_store):
    from streamlit.testing.v1 import AppTest
    from store import Store

    at = AppTest.from_file(str(ROOT / "app" / "import_data.py"), default_timeout=120)
    at.run()
    at.radio[0].set_value("예시 자료(가상)").run()
    assert not at.exception, [e.message for e in at.exception]
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["전체 행"] == "7" and metrics["오류 행"] == "2"

    # 처리자 없이 가져오기 → 막힘
    _btn(at, "가져오기 (검토 전 요청으로 저장)").click().run()
    assert any("처리자" in e.value for e in at.error)
    next(t for t in at.text_input if t.key == "import_actor").input("테스트").run()
    _btn(at, "가져오기 (검토 전 요청으로 저장)").click().run()
    assert not at.exception, [e.message for e in at.exception]
    s = Store(_temp_store)
    assert len(s.requests(status="draft")) == 5 and s.ready_requests() == []

    # 같은 파일 다시 → 중복 안내, 새 요청 없음(AT-01)
    at.run()
    assert any("같은 파일" in w.value for w in at.warning)
    _btn(at, "가져오기 (검토 전 요청으로 저장)").click().run()
    assert len(Store(_temp_store).requests()) == 5

    # 모두 선택 → 확인 완료
    _btn(at, "모두 선택").click().run()
    next(t for t in at.text_input if t.key == "waiting_reviewer").input("테스트").run()
    _btn(at, "선택한 요청 처리").click().run()
    assert not at.exception, [e.message for e in at.exception]
    ready = Store(_temp_store).ready_requests("2026-11-24")
    assert {r["request_id"] for r in ready} == {"가져옴-IMP-001", "가져옴-IMP-002", "가져옴-IMP-003", "가져옴-IMP-004"}

    # 하루 배정 비교에서 그 날짜를 고르면 가져온 요청이 계산에 들어간다(위반 0)
    day = AppTest.from_file(str(ROOT / "app" / "day_compare.py"), default_timeout=180)
    day.run()
    day.sidebar.selectbox[0].set_value("2026-11-24").run()
    assert not day.exception, [e.message for e in day.exception]
    assert any("확인한 요청" in m.value and "4건" in m.value for m in day.info)
    assert any("위반 0건" in m.value for m in day.success) or any("위반 0건" in m.value for m in day.markdown)
