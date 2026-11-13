"""부족 자원 리포트 보강(W10): 시간대 근무 확대(FR-42), 직접 고른 조건으로 다시 계산, 인쇄용 보고(FR-44)."""
from pathlib import Path

import pytest

from simulator.report import BY_KEY, Scenario, added_paid_minutes, extend_shifts

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "processed"
HAS = (DATA / "requests.csv").exists()


def _inst():
    w = lambda i, sk: {"worker_id": f"W{i}", "skills": sk, "base_node": f"B{i}", "shift_start_min": 540,
                       "shift_end_min": 1080, "break_start_min": 720, "break_minutes": 60, "max_work_minutes": 480}
    return {"workers": [w(1, ["hospital_escort", "household_support"]), w(2, ["household_support"])],
            "requests": [], "travel": {}}


def test_extend_shifts_scope_and_cap():
    out = extend_shifts(_inst(), 60, "escort")
    assert [w["shift_end_min"] for w in out["workers"]] == [1140, 1080]
    assert out["workers"][0]["max_work_minutes"] == 540
    out = extend_shifts(_inst(), 600, "all")
    assert all(w["shift_end_min"] == 1440 for w in out["workers"])          # 24시를 넘기지 않음
    assert _inst()["workers"][0]["shift_end_min"] == 1080                     # 원본 그대로


def test_added_paid_minutes_counts_extension_and_new_workers():
    assert added_paid_minutes(_inst(), Scenario("x", "x", extend_end=60, extend_scope="all")) == 120
    assert added_paid_minutes(_inst(), BY_KEY["stagger"]) == 0
    assert added_paid_minutes(_inst(), BY_KEY["add1"]) == 480                  # 복제 인력 1명 유급시간(근무−휴게)


@pytest.mark.skipif(not HAS, reason="생성 데이터 없음")
def test_custom_compare_same_requests():
    from simulator.report import custom_compare
    sc = Scenario("custom", "동행 근무 +60분", extend_end=60)
    out = custom_compare(DATA, sc, n_days=1, algorithm="nearest", hourly_krw=13000)
    t = out["table"]
    assert list(t.index) == ["base", "custom"] and set(t["violations"]) == {0}
    assert t.loc["custom", "added_paid_min"] > 0 and t.loc["custom", "extra_cost_per_day"] > 0
    raw = out["raw"]
    assert raw.groupby("scenario")["requests"].sum().nunique() == 1           # 같은 요청 집합


@pytest.mark.skipif(not (ROOT / "data" / "experiments").exists(), reason="실험 파일 없음")
def test_print_html_has_assumptions_and_synthetic_note():
    from tools.shortage_report import build, to_print_html
    try:
        r = build(ROOT / "data" / "experiments", DATA)
    except SystemExit:
        pytest.skip("리포트용 실험 결과 없음")
    html = to_print_html(r)
    assert "<table>" in html and "가상 입력 기반 추정" in html and r["days_path"].name in html and "@page" in html


@pytest.mark.skipif(not HAS, reason="생성 데이터 없음")
def test_shortage_page_custom_recompute():
    from streamlit.testing.v1 import AppTest
    if not list((ROOT / "data" / "experiments").glob("shortage-*.csv")):
        pytest.skip("리포트용 실험 결과 없음")
    at = AppTest.from_file(str(ROOT / "app" / "shortage_report.py"), default_timeout=300)
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    next(s for s in at.selectbox if s.label == "근무 연장 대상").set_value("동행 가능 인력").run()
    next(s for s in at.selectbox if s.label == "근무 끝 늦추기").set_value(60).run()
    next(n for n in at.number_input if n.label == "날짜 수").set_value(1).run()
    next(b for b in at.button if b.label == "다시 계산").click().run()
    assert not at.exception, [e.message for e in at.exception]
    t = at.dataframe[-1].value
    assert len(t) == 2 and "근무 +60분" in t["조건"].iloc[1]
