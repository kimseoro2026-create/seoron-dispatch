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


POPULATION_COLUMNS = ["dong", "admm_cd", "sgg", "stats_ym", "total", "age_60s", "age_70_plus",
                      "age_65_plus", "age_65_plus_method"]
AGE65_METHOD = "estimate: 70세 이상 + 60~69세의 절반(연령이 10세 구간이라 65세 경계를 정확히 나눌 수 없음)"


def _int(v) -> int:
    """인구 수 문자열 → 정수. 비어 있거나 숫자가 아니면 오류(0으로 바꾸지 않는다)."""
    s = str(v if v is not None else "").replace(",", "").strip()
    if not s:
        raise ValueError("인구 응답에 빈 값이 있습니다")
    return int(float(s))


def population_rows_to_frame(rows: list[dict], fm: dict) -> pd.DataFrame:
    """행안부 행정동별 성·연령별 인구 응답 item → 행정동별 고령인구 표.

    70세 이상은 응답 항목을 그대로 더한 값이다. 65세 이상은 60~69세의 절반을 더한 대략값이며
    age_65_plus_method 에 그렇게 적는다. 행정동명이 비어 있는 행(시군구 합계)은 뺀다.
    """
    out = []
    for r in rows:
        dong = str(r.get(fm["dong"]) or "").strip()
        if not dong:
            continue
        a70 = sum(_int(r.get(f)) for f in fm["age_70_plus_fields"])
        a60 = sum(_int(r.get(f)) for f in fm["age_60s_fields"])
        out.append({"dong": dong, "admm_cd": str(r.get(fm["admm_cd"]) or ""), "sgg": r.get(fm["sgg"]),
                    "stats_ym": str(r.get(fm["stats_ym"]) or ""), "total": _int(r.get(fm["total"])),
                    "age_60s": a60, "age_70_plus": a70, "age_65_plus": a70 + a60 // 2,
                    "age_65_plus_method": AGE65_METHOD})
    if not out:
        return pd.DataFrame(columns=POPULATION_COLUMNS)
    return pd.DataFrame(out)[POPULATION_COLUMNS].drop_duplicates("admm_cd").reset_index(drop=True)


def fetch_population(client: PlatformClient, admm_cd: str, stats_ym: str, lv: str = "3") -> pd.DataFrame:
    """행정동별 성/연령별 인구(행안부) → 행정동별 고령인구. admm_cd 는 시군구 행정기관코드(10자리)."""
    fm = client.config["population"]["field_map"]
    if str(fm.get("dong", "")).startswith("TODO"):
        raise ValueError("apis.yaml의 population.field_map을 상세 화면의 응답 항목명으로 채워주세요")
    rows = client.call_all_pages("population", admmCd=str(admm_cd), ym=str(stats_ym), lv=str(lv))
    df = population_rows_to_frame(rows, fm)
    if df.empty:
        raise RuntimeError(f"인구 응답에 행정동이 없습니다(행정기관코드 {admm_cd}, 기준월 {stats_ym})")
    return df


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
