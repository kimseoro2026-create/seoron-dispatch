"""실험실·데이터 연결 화면: 저장된 결과 보기, 새 실험 돌리기(작게), 다음 주 수요, 연결 상태 표."""
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not (ROOT / "data" / "processed" / "requests.csv").exists(),
                                reason="생성 데이터 없음")


def test_lab_page(tmp_path, monkeypatch, _temp_store):
    from streamlit.testing.v1 import AppTest
    exp = tmp_path / "exp"
    exp.mkdir()
    src = sorted((ROOT / "data" / "experiments").glob("30day-*.csv"))
    if src:
        shutil.copy(src[-1], exp / src[-1].name)
    monkeypatch.setenv("SEORON_EXP_DIR", str(exp))

    at = AppTest.from_file(str(ROOT / "app" / "lab.py"), default_timeout=300)
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    if src:
        assert any("방식·부하율별 요약" in m.value for m in at.markdown)

    # 새 실험: 1일·1시드·기준선 2개
    next(m for m in at.multiselect if m.label == "방식").set_value(["request_order", "nearest"]).run()
    next(n for n in at.number_input if n.label == "날짜 수(앞에서부터)").set_value(1).run()
    next(t for t in at.text_input if t.key == "lab_actor").input("테스트").run()
    next(b for b in at.button if b.label == "실험 돌리기").click().run()
    assert not at.exception, [e.message for e in at.exception]
    assert any("저장했습니다" in m.value for m in at.success)
    assert list(exp.glob("lab-*-assumptions.json"))
    from store import Store
    assert Store(_temp_store).experiments()


def test_sources_page():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "app" / "sources.py"), default_timeout=60)
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    names = set(at.dataframe[0].value["자원"])
    assert {"병원 위치", "차량 이동시간", "AI(요청 카드)"} <= names


def test_joint_tab(tmp_path, monkeypatch, _temp_store):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("SEORON_EXP_DIR", str(tmp_path))
    at = AppTest.from_file(str(ROOT / "app" / "lab.py"), default_timeout=300)
    at.run()
    assert any("조건부 실험" in w.value for w in at.warning)
    next(n for n in at.number_input if n.key == "joint_days").set_value(1).run()
    next(t for t in at.text_input if t.key == "joint_actor").input("테스트").run()
    next(b for b in at.button if b.label == "공동 동행 실험 돌리기").click().run()
    assert not at.exception, [e.message for e in at.exception]
    assert any("짝에서 빠진 병원동행" in m.value for m in at.markdown)
    assert list(tmp_path.glob("joint-*-assumptions.json"))
    from store import Store
    assert Store(_temp_store).experiments()[0]["config"]["experiment"] == "joint_escort"
