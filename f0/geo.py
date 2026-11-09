"""좌표·경계 유틸리티."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from shapely.geometry import Point, box, shape
from shapely.geometry.base import BaseGeometry

# 대략값 계산 상수. numpy 벡터화(planner/data.py)와 값이 어긋나지 않도록 한곳에 둔다.
HAVERSINE_R_KM = 6371.0088
DRIVE_DETOUR = 1.35      # 직선거리 → 도로거리 우회계수
DRIVE_KMH = 25.0         # 도심 평균 속도
DRIVE_FIXED = 3.0        # 출발·정차 고정시간(분)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = HAVERSINE_R_KM
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def estimated_drive_minutes(lat1, lon1, lat2, lon2, detour=DRIVE_DETOUR,
                            kmh=DRIVE_KMH, fixed=DRIVE_FIXED) -> float:
    """OSRM이 없을 때 쓰는 대략값: 직선거리 × 우회계수 ÷ 도심 평균속도 + 고정시간."""
    km = haversine_km(lat1, lon1, lat2, lon2)
    return round(km * detour / kmh * 60 + (fixed if km > 0 else 0.0), 1)


def latlon_to_kma_grid(lat: float, lon: float) -> tuple[int, int]:
    """위경도 → 기상청 단기예보 격자(nx, ny). 기상청 LCC 투영 공식."""
    re, grid = 6371.00877, 5.0
    slat1, slat2, olon, olat = 30.0, 60.0, 126.0, 38.0
    xo, yo = 43, 136
    deg = math.pi / 180.0
    re_g = re / grid
    s1, s2, ol, oa = slat1 * deg, slat2 * deg, olon * deg, olat * deg
    sn = math.log(math.cos(s1) / math.cos(s2)) / math.log(
        math.tan(math.pi * 0.25 + s2 * 0.5) / math.tan(math.pi * 0.25 + s1 * 0.5)
    )
    sf = math.tan(math.pi * 0.25 + s1 * 0.5) ** sn * math.cos(s1) / sn
    ro = re_g * sf / math.tan(math.pi * 0.25 + oa * 0.5) ** sn
    ra = re_g * sf / math.tan(math.pi * 0.25 + lat * deg * 0.5) ** sn
    theta = lon * deg - ol
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn
    nx = int(math.floor(ra * math.sin(theta) + xo + 0.5))
    ny = int(math.floor(ro - ra * math.cos(theta) + yo + 0.5))
    return nx, ny


def load_boundaries(path: str | Path, name_field: str) -> dict[str, BaseGeometry]:
    """GeoJSON(WGS84 위경도) → {행정동명: 도형}."""
    with open(path, encoding="utf-8") as f:
        gj = json.load(f)
    out: dict[str, BaseGeometry] = {}
    for feat in gj["features"]:
        name = feat["properties"][name_field]
        geom = shape(feat["geometry"])
        out[name] = out[name].union(geom) if name in out else geom
    return out


def bbox_polygon(bbox: dict) -> BaseGeometry:
    return box(bbox["min_lon"], bbox["min_lat"], bbox["max_lon"], bbox["max_lat"])


def random_points_in(poly: BaseGeometry, n: int, rng: np.random.Generator) -> list[tuple[float, float]]:
    """도형 안의 무작위 점 n개 → [(lat, lon), ...]. 거부 표본추출."""
    minx, miny, maxx, maxy = poly.bounds
    pts: list[tuple[float, float]] = []
    tries = 0
    while len(pts) < n:
        tries += 1
        if tries > n * 1000:
            raise RuntimeError("경계 안에 점을 만들지 못했습니다. 경계 좌표계를 확인하세요(WGS84 필요).")
        x, y = rng.uniform(minx, maxx), rng.uniform(miny, maxy)
        if poly.contains(Point(x, y)):
            pts.append((round(y, 6), round(x, 6)))
    return pts
