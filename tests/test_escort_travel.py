"""병원동행 구간 대중교통 기본(docs/travel_modes_plan.md 3-1)."""
import pandas as pd

from adapters import escort_travel as et
from planner.data import apply_escort_travel, escort_travel_status, load_travel

H, B = "HOSP:X", "BENEFICIARY:B1"


def _write(tmp_path, rows):
    pd.DataFrame(rows, columns=et.OUT_COLUMNS).to_csv(tmp_path / et.OUT_NAME, index=False)


def test_transit_mode_replaces_only_escort_legs(tmp_path):
    _write(tmp_path, [{"from_node": B, "to_node": H, "minutes": 31.0, "mode": "transit", "source": "odsay"},
                      {"from_node": H, "to_node": B, "minutes": 9.5, "mode": "walk", "source": "walk_osrm"}])
    travel = {(B, H): 12.0, (H, B): 11.0, ("WORKER:W1", B): 7.0}
    out = apply_escort_travel(dict(travel), tmp_path, "transit")
    assert out[(B, H)] == 31.0 and out[(H, B)] == 9.5
    assert out[("WORKER:W1", B)] == 7.0                      # 인력 이동은 그대로 자동차
    assert apply_escort_travel(dict(travel), tmp_path, "car") == travel


def test_zero_or_missing_never_becomes_zero_minutes(tmp_path):
    _write(tmp_path, [{"from_node": B, "to_node": H, "minutes": 0, "mode": "transit", "source": "odsay"}])
    out = apply_escort_travel({(B, H): 12.0, (H, B): 11.0}, tmp_path, "transit")
    assert out[(B, H)] == 12.0                               # 0분은 버리고 자동차 값 유지


def test_does_not_add_legs_outside_day_matrix(tmp_path):
    _write(tmp_path, [{"from_node": B, "to_node": H, "minutes": 31.0, "mode": "transit", "source": "odsay"}])
    assert apply_escort_travel({}, tmp_path, "transit") == {}


def test_status_counts_car_fallback(tmp_path):
    _write(tmp_path, [{"from_node": B, "to_node": H, "minutes": 31.0, "mode": "transit", "source": "odsay"}])
    reqs = [{"service_type": "hospital_escort", "home_node": B, "hospital_node": H},
            {"service_type": "household_support", "home_node": B, "hospital_node": None}]
    s = escort_travel_status(reqs, tmp_path, "transit")
    assert (s["legs"], s["transit"], s["walk"], s["car_fallback"]) == (2, 1, 0, 1)
    assert escort_travel_status(reqs, tmp_path, "car")["car"] == 2


def test_load_travel_without_table_is_unchanged(tmp_path):
    pd.DataFrame([{"from_node": B, "to_node": H, "minutes": 12.0, "source": "osrm"}]).to_csv(
        tmp_path / "travel_matrix.csv", index=False)
    assert load_travel(tmp_path, escort="transit") == {(B, H): 12.0}


def test_classify_too_close_and_resume():
    assert et.classify({"status": "provider_error", "provider_result_code": -98}) == "too_close"
    assert et.classify({"status": "ok", "provider_result_code": None}) == "ok"
    pairs = [{"from_id": "a", "to_id": "b"}, {"from_id": "b", "to_id": "a"}, {"from_id": "c", "to_id": "d"}]
    raw = pd.DataFrame([{"from_id": "a", "to_id": "b", "pair_status": "ok"},
                        {"from_id": "b", "to_id": "a", "pair_status": "timeout"}])
    assert [(p["from_id"], p["to_id"]) for p in et.pending(pairs, raw)] == [("b", "a"), ("c", "d")]


class _FakeClient:
    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.calls = 0

    def fetch_body(self, name, **kw):
        self.calls += 1
        b = self.bodies.pop(0)
        if isinstance(b, Exception):
            raise b
        return b


def test_fetch_stops_on_rate_limit_and_keeps_results(tmp_path):
    from f0.apiclient import ApiCallError
    ok = '{"result": {"path": [{"pathType": 2, "info": {"totalTime": 25, "totalDistance": 5000, "totalWalk": 300}}]}}'
    pairs = [{"from_id": f"F{i}", "from": (37.2, 127.07), "to_id": f"T{i}", "to": (37.21, 127.08),
              "request_id": f"R{i}"} for i in range(4)]
    c = _FakeClient([ok, ApiCallError("rate_limited", "HTTP 429"), ok, ok])
    res = et.fetch(c, pairs, tmp_path, max_calls=10, log=lambda *_: None)
    assert c.calls == 2 and res["stopped"] == "rate_limited" and res["remaining"] == 3
    df = et.build(tmp_path, tmp_path, walk_kmh=3.0)
    assert df.to_dict("records")[0]["minutes"] == 25.0 and len(df) == 1


def test_walk_estimate_uses_elderly_speed():
    m, src = et.walk_minutes((37.2, 127.07), (37.2045, 127.07), walk_kmh=3.0)   # 직선 약 0.5km
    assert src == "walk_estimate" and 12 < m < 14                              # 0.5×1.3/3×60 ≈ 13분
