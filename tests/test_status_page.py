"""오늘의 현황 화면: 계산 전 → (하루 배정 비교에서 계산) → 제안·미배정 → 승인 → 확정. 카드 숫자와 표 행 수가 같다."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not (ROOT / "data" / "processed" / "requests.csv").exists(),
                                reason="생성 데이터 없음")


def _metric(at, label):
    return next(m.value for m in at.metric if m.label == label)


def test_status_flow(_temp_store):
    from streamlit.testing.v1 import AppTest

    st_page = AppTest.from_file(str(ROOT / "app" / "status_board.py"), default_timeout=120)
    st_page.run()
    assert not st_page.exception, [e.message for e in st_page.exception]
    date = "2026-11-02"
    st_page.sidebar.selectbox[0].set_value(date).run()
    assert _metric(st_page, "제안") == "0건" and _metric(st_page, "계산 전") != "0건"
    assert any("계산을 한 기록이 없습니다" in m.value for m in st_page.info)

    # 하루 배정 비교에서 같은 날짜 계산 + 모두 수락 → 승인
    day = AppTest.from_file(str(ROOT / "app" / "day_compare.py"), default_timeout=180)
    day.run()
    day.sidebar.selectbox[0].set_value(date).run()
    next(b for b in day.button if b.label == "모두 수락으로 채우기").click().run()
    next(b for b in day.button if b.label == "승인 적용").click().run()
    assert not day.exception, [e.message for e in day.exception]

    st_page.run()
    assert not st_page.exception, [e.message for e in st_page.exception]
    assert _metric(st_page, "계산 전") == "0건"
    confirmed = int(_metric(st_page, "확정").rstrip("건"))
    assert confirmed > 0 and _metric(st_page, "제안") == "0건"     # AI 최적화안을 모두 확정했으므로
    assert int(_metric(st_page, "미배정").rstrip("건")) > 0
    total = int(_metric(st_page, "보이는 요청").rstrip("건"))
    table = st_page.dataframe[-1].value
    assert len(table) == total

    # 서비스 필터를 바꿔도 숫자와 표가 같다
    st_page.sidebar.selectbox[2].set_value("병원동행").run()
    total = int(_metric(st_page, "보이는 요청").rstrip("건"))
    table = st_page.dataframe[-1].value
    assert len(table) == total and set(table["서비스"]) == {"병원동행"}
