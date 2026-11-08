"""오프라인 모의 원천 데이터.

API 승인·URL 설정 전에도 F0 전체 흐름을 검증하기 위한 것이다.
행정동 이름·병원 이름은 모두 가상이며 실제 기관을 뜻하지 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .sources import HOSPITAL_COLUMNS


def mock_boundaries(bbox: dict, cols: int = 3, rows: int = 2) -> dict:
    """권역 bbox를 격자로 나눈 가상 행정동 GeoJSON."""
    feats = []
    w = (bbox["max_lon"] - bbox["min_lon"]) / cols
    h = (bbox["max_lat"] - bbox["min_lat"]) / rows
    k = 1
    for r in range(rows):
        for c in range(cols):
            x0, y0 = bbox["min_lon"] + c * w, bbox["min_lat"] + r * h
            ring = [[x0, y0], [x0 + w, y0], [x0 + w, y0 + h], [x0, y0 + h], [x0, y0]]
            feats.append({
                "type": "Feature",
                "properties": {"adm_nm": f"가상동{k}"},
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            })
            k += 1
    return {"type": "FeatureCollection", "features": feats}


def mock_population(dong_names: list[str], rng: np.random.Generator) -> pd.DataFrame:
    return pd.DataFrame({"dong": dong_names, "age_65_plus": rng.integers(1500, 6000, len(dong_names))})


def mock_hospitals(bbox: dict, rng: np.random.Generator) -> pd.DataFrame:
    spec = [("종합병원", 3), ("병원", 8), ("요양병원", 4), ("의원", 25)]
    rows, k = [], 1
    for cls, n in spec:
        for _ in range(n):
            rows.append({
                "hospital_id": f"MOCK-H{k:03d}",
                "name": f"가상{cls}{k}",
                "class_name": cls,
                "address": "가상 주소",
                "lat": round(rng.uniform(bbox["min_lat"], bbox["max_lat"]), 6),
                "lon": round(rng.uniform(bbox["min_lon"], bbox["max_lon"]), 6),
            })
            k += 1
    return pd.DataFrame(rows, columns=HOSPITAL_COLUMNS)


def write_mock_raw(raw_dir: Path, region: dict, seed: int) -> None:
    rng = np.random.default_rng(seed)
    raw_dir.mkdir(parents=True, exist_ok=True)
    gj = mock_boundaries(region["bbox"])
    (raw_dir / "boundaries.geojson").write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    names = [f["properties"]["adm_nm"] for f in gj["features"]]
    mock_population(names, rng).to_csv(raw_dir / "population.csv", index=False, encoding="utf-8-sig")
    mock_hospitals(region["bbox"], rng).to_csv(raw_dir / "hospitals.csv", index=False, encoding="utf-8-sig")
