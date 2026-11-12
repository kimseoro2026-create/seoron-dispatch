"""배포 서버처럼 예전 simulator.experiment 가 메모리에 남아 있어도 하루 비교 화면이 멈추지 않는지 확인한다.

2026-11-12 배포에서 화면 파일만 새로 읽히고 simulator.experiment 는 예전 것이 남아
'못 맡은 요청' 표를 만들다 ImportError 로 화면 아래쪽이 멈췄다.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_day_compare_survives_stale_experiment_module(monkeypatch):
    if not (ROOT / "data" / "processed" / "requests.csv").exists():
        pytest.skip("생성 데이터 없음")
    from streamlit.testing.v1 import AppTest

    import simulator.experiment as ex
    monkeypatch.delattr(ex, "idle_check")   # 예전 모듈 흉내: 진단 함수가 없음

    at = AppTest.from_file(str(ROOT / "app" / "day_compare.py"), default_timeout=120)
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    assert hasattr(ex, "idle_check")        # 화면이 모듈을 다시 읽어 복구했다
    assert any("못 맡은 요청" in m.value for m in at.markdown)
