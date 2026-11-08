import json

import numpy as np
import pandas as pd
import pytest
import yaml

from f0.apiclient import parse_response
from f0.generate import generate_world
from f0.geo import latlon_to_kma_grid
from f0.matrix import build_matrix, build_nodes
from f0.mock import mock_boundaries, mock_hospitals, mock_population
from shapely.geometry import shape


@pytest.fixture(scope="module")
def cfg():
    with open("config/region.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def inputs(cfg):
    rng = np.random.default_rng(7)
    gj = mock_boundaries(cfg["region"]["bbox"])
    boundaries = {f["properties"]["adm_nm"]: shape(f["geometry"]) for f in gj["features"]}
    pop = mock_population(list(boundaries), rng)
    hosp = mock_hospitals(cfg["region"]["bbox"], rng)
    return boundaries, pop, hosp


def test_kma_grid_seoul_city_hall():
    # 기상청 기준: 서울특별시청 부근 = (60, 127)
    assert latlon_to_kma_grid(37.5665, 126.9780) == (60, 127)


def test_same_seed_same_world(cfg, inputs):
    b, p, h = inputs
    w1 = generate_world(cfg["simulation"], h, p, b, seed=1)
    w2 = generate_world(cfg["simulation"], h, p, b, seed=1)
    pd.testing.assert_frame_equal(w1.requests, w2.requests)
    pd.testing.assert_frame_equal(w1.beneficiaries, w2.beneficiaries)


def test_counts_and_windows(cfg, inputs):
    b, p, h = inputs
    sim = cfg["simulation"]
    w = generate_world(sim, h, p, b, seed=3)
    assert len(w.beneficiaries) == sim["beneficiaries"]
    assert len(w.workers) == sum(sim["workers"].values())
    r = w.requests
    assert (r["time_window_end_min"] >= r["time_window_start_min"]).all()
    e = r[r["service"] == "hospital_escort"]
    # 시간창 끝에 출발해도 예약 시각 전에 도착해야 한다
    assert (e["time_window_end_min"] + e["est_travel_min"] <= e["appointment_min"]).all()
    assert w.beneficiaries["primary_worker_id"].notna().all()


def test_matrix_estimate(cfg, inputs):
    b, p, h = inputs
    sim = dict(cfg["simulation"], beneficiaries=30, days=2)
    w = generate_world(sim, h, p, b, seed=5)
    nodes = build_nodes(cfg["region"], w)
    long, source = build_matrix(nodes, osrm_url=None)
    n = len(nodes)
    assert source == "estimate"
    assert len(long) == n * (n - 1)
    assert (long["minutes"] > 0).all()


def test_parse_json_and_xml():
    js = json.dumps({"response": {"body": {"totalCount": 2, "items": {"item": [{"a": 1}, {"a": 2}]}}}})
    items, total = parse_response(js, "totalCount")
    assert len(items) == 2 and total == 2
    xml = "<response><body><totalCount>1</totalCount><items><item><yadmNm>가상병원</yadmNm></item></items></body></response>"
    items, total = parse_response(xml, "totalCount")
    assert items == [{"yadmNm": "가상병원"}] and total == 1


def test_osrm_table_chunking(monkeypatch):
    """OSRM table 응답을 흉내 내어, 블록 나누기 결과가 전체 행렬과 같은지 확인."""
    from f0 import matrix as m
    from f0.geo import estimated_drive_minutes

    rng = np.random.default_rng(0)
    coords = [(37.2 + rng.uniform(-0.03, 0.03), 127.07 + rng.uniform(-0.04, 0.04)) for _ in range(23)]

    class Resp:
        ok = True
        def __init__(self, data): self._d = data
        def json(self): return self._d
        def raise_for_status(self): pass

    def fake_get(url, params=None, timeout=None):
        pts = [tuple(map(float, p.split(","))) for p in url.split("/driving/")[1].split(";")]
        src = [int(x) for x in params["sources"].split(";")]
        dst = [int(x) for x in params["destinations"].split(";")]
        dur = [[estimated_drive_minutes(pts[i][1], pts[i][0], pts[j][1], pts[j][0]) * 60 for j in dst] for i in src]
        return Resp({"code": "Ok", "durations": dur})

    monkeypatch.setattr(m.requests, "get", fake_get)
    got = m.OsrmClient("http://fake").table_minutes(coords, chunk=7)
    want = np.round(m.estimated_matrix(coords), 1)
    assert np.allclose(got, want, atol=0.11)
