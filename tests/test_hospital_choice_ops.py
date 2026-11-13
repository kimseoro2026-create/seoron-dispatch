"""같은 이름 병원 고르기(FR-15)와 검토 조작 수 기록(FR-57)."""
import json
from pathlib import Path

import pytest

from intake.to_request import card_to_request, hospital_candidates

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "processed"
pytestmark = pytest.mark.skipif(not (DATA / "requests.csv").exists(), reason="생성 데이터 없음")


def _e15():
    case = next(c for c in json.loads((ROOT / "fixtures/request_cards/cases.json").read_text(encoding="utf-8"))["cases"]
                if c["case_id"] == "E15")
    return {k: v["value"] for k, v in case["gold"].items()}


def test_duplicate_name_needs_choice_and_accepts_only_candidates():
    final = _e15()
    cands = hospital_candidates(final["hospital_name"], DATA)
    assert len(cands) >= 2 and all(c["address"] for c in cands)
    req, probs = card_to_request(final, DATA, "E15", "2026-11-01T09:00")
    assert req is None and any("주소" in p for p in probs)
    req, probs = card_to_request(final, DATA, "E15", "2026-11-01T09:00", hospital_id=cands[1]["hospital_id"])
    assert probs == [] and req["hospital_node"] == f"HOSP:{cands[1]['hospital_id']}"
    _, probs = card_to_request(final, DATA, "E15", "2026-11-01T09:00", hospital_id="없는-ID")
    assert probs and "후보가 아닙니다" in probs[0]


def test_screen_choose_hospital_then_confirm_records_ops(_temp_store):
    from streamlit.testing.v1 import AppTest
    from store import Store

    at = AppTest.from_file(str(ROOT / "app" / "request_cards.py"), default_timeout=120)
    at.run()
    radio = at.sidebar.radio[0]
    rules = next(i for i, o in enumerate(radio.options) if "규칙" in o)
    radio.set_value(radio.options[rules] if isinstance(radio.value, str) else rules).run()
    box = at.selectbox[0]
    box.set_value(next(o for o in box.options if o.startswith("E15"))).run()
    hosp = next(s for s in at.selectbox if s.label.startswith("같은 이름의 병원이"))
    next(t for t in at.text_input if t.label == "처리자").input("테스트").run()
    next(b for b in at.button if b.label == "처리 적용").click().run()          # 고르기 전 → 막힘
    assert any("골라 주세요" in e.value for e in at.error)
    hosp = next(s for s in at.selectbox if s.label.startswith("같은 이름의 병원이"))
    pick = hospital_candidates(_e15()["hospital_name"], DATA)[1]["hospital_id"]
    hosp.set_value(pick).run()
    next(b for b in at.button if b.label == "처리 적용").click().run()
    assert not at.exception, [e.message for e in at.exception]
    s = Store(_temp_store)
    req = s.ready_requests()[0]
    assert req["request_id"] == "카드-E15" and req["hospital_node"] == f"HOSP:{pick}"
    ops = s.card_history(s.card_review_keys()[0])[-1]["ops"]
    assert ops["hospital_choice"] == 1 and ops["apply_clicks"] == 2 and ops["total"] >= 3
    assert s.review_ops_summary()["cards"] == 1
    assert any("검토 조작 수" in c.value for c in at.caption)
