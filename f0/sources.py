"""디지털융합플랫폼 API 4종 → 표준 표(DataFrame)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from .apiclient import PlatformClient
from .geo import latlon_to_kma_grid

HOSPITAL_COLUMNS = ["hospital_id", "name", "class_name", "address", "lat", "lon"]


def fetch_hospitals(client: PlatformClient, region: dict) -> pd.DataFrame:
    """병원기본목록: 권역 중심 반경 안의 병원 → 종별·범위로 거른다."""
    c = region["center"]
    rows = client.call_all_pages(
        "hospitals", lat=c["lat"], lon=c["lon"], radius=region["hospital_search_radius_m"]
    )
    fm = client.config["hospitals"]["field_map"]
    df = pd.DataFrame([{std: r.get(src) for std, src in fm.items()} for r in rows])
    if df.empty:
        return pd.DataFrame(columns=HOSPITAL_COLUMNS)
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df = df.dropna(subset=["lat", "lon"])
    df = df[df["class_name"].isin(region["hospital_classes"])]
    b = region["bbox"]
    df = df[df["lat"].between(b["min_lat"], b["max_lat"]) & df["lon"].between(b["min_lon"], b["max_lon"])]
    return df[HOSPITAL_COLUMNS].drop_duplicates("hospital_id").reset_index(drop=True)


def fetch_population(client: PlatformClient, **request_values) -> pd.DataFrame:
    """행정동별 성/연령별 인구 → 행정동별 65세 이상 인구."""
    rows = client.call_all_pages("population", **request_values)
    fm = client.config["population"]["field_map"]
    if str(fm["dong"]).startswith("TODO"):
        raise ValueError("apis.yaml의 population.field_map을 상세 화면의 응답 항목명으로 채워주세요")
    out = []
    for r in rows:
        total = sum(int(float(r.get(f) or 0)) for f in fm["age_65_plus_fields"])
        out.append({"dong": r.get(fm["dong"]), "age_65_plus": total})
    df = pd.DataFrame(out)
    return df.groupby("dong", as_index=False)["age_65_plus"].sum()


def latest_base_time(now: datetime) -> tuple[str, str]:
    """단기예보 발표시각(02,05,08,11,14,17,20,23시, 발표 10분 후 조회 가능) 중 가장 최근."""
    slots = [2, 5, 8, 11, 14, 17, 20, 23]
    t = now - timedelta(minutes=10)
    past = [h for h in slots if h <= t.hour]
    if past:
        return t.strftime("%Y%m%d"), f"{past[-1]:02d}00"
    prev = t - timedelta(days=1)
    return prev.strftime("%Y%m%d"), "2300"


def fetch_forecast(client: PlatformClient, lat: float, lon: float, now: datetime | None = None) -> pd.DataFrame:
    """단기예보 → 시각별 강수확률(POP)·강수형태(PTY)·기온(TMP) 표."""
    nx, ny = latlon_to_kma_grid(lat, lon)
    base_date, base_time = latest_base_time(now or datetime.now())
    items, _ = client.call("weather", base_date=base_date, base_time=base_time, nx=nx, ny=ny)
    df = pd.DataFrame(items)
    if df.empty:
        return df
    df = df[df["category"].isin(["POP", "PTY", "TMP", "SKY"])]
    wide = df.pivot_table(index=["fcstDate", "fcstTime"], columns="category", values="fcstValue", aggfunc="first")
    return wide.reset_index()


def fetch_transit_minutes(client: PlatformClient, lat1, lon1, lat2, lon2) -> float | None:
    """대중교통 길찾기 → 첫 번째 경로의 총 소요시간(분). 경로가 없으면 None."""
    items, _ = client.call("transit", sx=lon1, sy=lat1, ex=lon2, ey=lat2)
    # ODsay 응답은 result.path[].info.totalTime 구조. apiclient가 item을 못 찾으면 원문을 다시 본다.
    for it in items:
        info = it.get("info") if isinstance(it, dict) else None
        if isinstance(info, dict) and "totalTime" in info:
            return float(info["totalTime"])
    return None
