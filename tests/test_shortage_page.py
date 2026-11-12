"""부족 자원·비용 리포트: 작은 실행 → 리포트 생성 → 화면 표시까지."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "processed"


@pytest.fixture(scope="module")
def exp_dir(tmp_path_factory):
    if not (DATA / "requests.csv").exists():
        pytest.skip("생성 데이터 없음")
    from tools.run_shortage_report import main
    out = tmp_path_factory.mktemp("exp")
    main(["--days", "1", "--seeds", "42", "--algorithms", "nearest", "--scenarios", "base", "add1",
          "--out-dir", str(out)])
    return out


def test_report_markdown(exp_dir, tmp_path):
    from tools.shortage_report import main
    main(["--exp-dir", str(exp_dir), "--out-dir", str(tmp_path)])
    md = (tmp_path / "shortage_report.md").read_text(encoding="utf-8")
    assert "가상 입력 기반 추정" in md and "근거리(참고)" in md and "미배정 진단" in md
    assert (tmp_path / "shortage_scenarios.csv").exists() and (tmp_path / "shortage_unmet.csv").exists()


def test_page_renders(exp_dir, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("SEORON_EXP_DIR", str(exp_dir))
    at = AppTest.from_file(str(ROOT / "app" / "shortage_report.py"), default_timeout=60).run()
    assert not at.exception
    assert any("결과가 실험 파일에 없습니다" in w.value for w in at.warning)   # 최적화를 돌리지 않은 실험
    at.radio[0].set_value("nearest").run()
    assert not at.exception
    assert any("부족 자원" in t.value for t in at.title)
    assert len(at.dataframe) >= 2


def test_page_without_results(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("SEORON_EXP_DIR", str(tmp_path))
    at = AppTest.from_file(str(ROOT / "app" / "shortage_report.py"), default_timeout=60).run()
    assert not at.exception
    assert any("실험 결과가 없습니다" in i.value for i in at.info)
