"""담당자 승인: AI 최적화가 기본으로 골라지고, '모두 수락으로 채우기' → '승인 적용'으로 확정되는지 확인한다.

'모두 수락'은 표의 기본값만 바꾼다. '승인 적용'을 누르기 전에는 확정이 생기지 않아야 한다.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _btn(at, label):
    return next(b for b in at.button if b.label == label)


def test_accept_all_then_apply(_temp_store):
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
    from store import Store
    date = at.sidebar.selectbox[0].value
    assert not Store(_temp_store).confirmed(date)       # 채우기만으로는 확정되지 않는다

    _btn(at, "승인 적용").click().run()
    assert not at.exception, [e.message for e in at.exception]
    confirmed = Store(_temp_store).confirmed(date)      # 승인 적용 후에만 확정 — 저장소에 남는다
    assert confirmed and all(c["run_id"].startswith("run-") for c in confirmed)
    assert any("확정된 배정" in m.value for m in at.markdown)

    # 새로고침(새 세션)해도 확정이 남아 있고, 확정된 줄은 '확정됨'으로 잠겨 보인다
    at2 = AppTest.from_file(str(ROOT / "app" / "day_compare.py"), default_timeout=180)
    at2.run()
    assert not at2.exception, [e.message for e in at2.exception]
    assert any("확정된 배정" in m.value for m in at2.markdown)
    df = at2.dataframe  # 확정 내역 표
    assert df, "확정 내역 표가 보여야 한다"


def test_double_click_apply_confirms_once(_temp_store):
    """AT-08: 같은 결정으로 '승인 적용'을 두 번 눌러도 한 번만 반영된다(일정 버전 1)."""
    if not (ROOT / "data" / "processed" / "requests.csv").exists():
        pytest.skip("생성 데이터 없음")
    from streamlit.testing.v1 import AppTest
    from store import Store

    at = AppTest.from_file(str(ROOT / "app" / "day_compare.py"), default_timeout=180)
    at.run()
    date = at.sidebar.selectbox[0].value
    _btn(at, "모두 수락으로 채우기").click().run()
    _btn(at, "승인 적용").click().run()
    n = len(Store(_temp_store).confirmed(date))
    v = Store(_temp_store).schedule_version(date)
    assert n > 0 and v == 1
    _btn(at, "승인 적용").click().run()
    assert not at.exception, [e.message for e in at.exception]
    assert len(Store(_temp_store).confirmed(date)) == n
    assert Store(_temp_store).schedule_version(date) == 1


def test_approve_with_actual_transit_check(_temp_store, monkeypatch):
    """인력 모두 대중교통 → '연결 구간 실제 조회'(가짜 조회: 모든 구간 대략값 +6분) → 승인에서 '실제 조회 반영안'을
    고르면 늦춘 시각 그대로 확정되고, 감사 기록에 근거(transit_actual)가 남는다."""
    if not (ROOT / "data" / "processed" / "requests.csv").exists():
        pytest.skip("생성 데이터 없음")
    if not (ROOT / "data" / "processed" / "worker_transit_model.json").exists():
        pytest.skip("대중교통 보정 모형 없음")
    import pandas as pd
    from streamlit.testing.v1 import AppTest

    import f0.apiclient
    from adapters import worker_transit as wt
    from planner.approval import ACCEPT
    from store import Store

    seen: dict = {}

    def fake_live_tools(raw_dir, nodes_path, foot_url, client):
        def query(legs):
            new = [l for l in legs if (l["from_id"], l["to_id"]) not in seen]
            for l in new:
                seen[(l["from_id"], l["to_id"])] = (l["model_min"] or 30.0) + 6
            return len(new)

        def load_raw():
            return pd.DataFrame([{"from_id": a, "to_id": b, "pair_status": "ok", "duration_seconds": m * 60}
                                 for (a, b), m in seen.items()])

        def walk(a, b):
            return None
        walk.state = {"up": True}
        return query, load_raw, walk

    monkeypatch.setattr(wt, "live_tools", fake_live_tools)
    monkeypatch.setattr(f0.apiclient, "PlatformClient", lambda *a, **k: object())

    at = AppTest.from_file(str(ROOT / "app" / "day_compare.py"), default_timeout=240)
    at.run()
    at.sidebar.radio(key="worker_share").set_value("모두 대중교통").run()
    assert not at.exception, [e.message for e in at.exception]
    assert any("대략값" in i.value for i in at.info), "확인 전에는 승인 표가 대략값이라고 알려야 한다"

    _btn(at, "연결 구간 실제 조회").click().run()
    assert not at.exception, [e.message for e in at.exception]
    assert any("실제 값으로 확인" in s.value for s in at.success), [w.value for w in at.warning]
    rq = next(r for r in at.radio if r.label == "승인할 시각")
    assert rq.value == "실제 조회 반영안"
    res = next(v for k, v in at.session_state.items() if str(k).startswith("wchk|"))
    assert res["confirmed"] and res["retimed"], "가짜 조회(+6분)인데 시각이 바뀐 요청이 없다"

    _btn(at, "모두 수락으로 채우기").click().run()
    _btn(at, "승인 적용").click().run()
    assert not at.exception, [e.message for e in at.exception]
    date = at.sidebar.selectbox[0].value
    got = {c["request_id"]: c["arrive_min"] for c in Store(_temp_store).confirmed(date)}
    want = {a["request_id"]: int(a["arrive_min"]) for a in res["plan"]}
    assert got == want, "확정 시각이 실제 조회 반영안과 다르다"
    acc = [r for r in Store(_temp_store).audit() if r["action"] == ACCEPT]
    assert acc and all(r["detail"].get("basis") == "transit_actual" for r in acc)
