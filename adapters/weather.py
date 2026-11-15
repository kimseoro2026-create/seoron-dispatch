"""날씨 — 기상청 단기예보조회(플랫폼 A10). PRD 10장: 일정 화면 주의 표시용. 배정 숫자에는 쓰지 않는다.

- 요청변수·출력 항목은 2026-11-15 플랫폼 상세 화면에서 확인한 이름만 쓴다(config/apis.yaml weather).
- category 코드의 뜻은 상세 화면 '하단 코드값 정보'에 있다. 확인한 코드만 CATEGORY_KO 에 적고,
  나머지는 코드 그대로 보여준다(지어내지 않음).
- 실패는 빈 표나 0으로 바꾸지 않는다. 예외(ApiCallError/ApiNotConfigured)를 그대로 올린다.
"""
from __future__ import annotations

import datetime as dt
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

KST = ZoneInfo("Asia/Seoul")
CACHE_NAME = "weather_forecast.csv"

# 확인된 코드 설명만 적는다. 빈 사전이면 화면은 코드를 그대로 보여준다.
CATEGORY_KO: dict[str, str] = {}

# 단기예보 발표시각(하루 8회). 발표 직후에는 아직 자료가 없을 수 있어 10분 뒤부터 쓴다.
ISSUE_HOURS = (2, 5, 8, 11, 14, 17, 20, 23)
ISSUE_DELAY_MIN = 10


def latlon_to_grid(lat: float, lon: float) -> tuple[int, int]:
    """위경도 → 기상청 예보 격자(nx, ny). 기상청 격자(람베르트 정각원추, 5km) 변환식."""
    re_, grid = 6371.00877, 5.0
    slat1, slat2, olon, olat, xo, yo = 30.0, 60.0, 126.0, 38.0, 43, 136
    d = math.pi / 180.0
    re = re_ / grid
    s1, s2, olon_r, olat_r = slat1 * d, slat2 * d, olon * d, olat * d
    sn = math.log(math.cos(s1) / math.cos(s2)) / math.log(
        math.tan(math.pi * 0.25 + s2 * 0.5) / math.tan(math.pi * 0.25 + s1 * 0.5))
    sf = math.tan(math.pi * 0.25 + s1 * 0.5) ** sn * math.cos(s1) / sn
    ro = re * sf / math.tan(math.pi * 0.25 + olat_r * 0.5) ** sn
    ra = re * sf / math.tan(math.pi * 0.25 + lat * d * 0.5) ** sn
    theta = lon * d - olon_r
    if theta > math.pi:
        theta -= 2 * math.pi
    if theta < -math.pi:
        theta += 2 * math.pi
    theta *= sn
    return int(ra * math.sin(theta) + xo + 0.5), int(ro - ra * math.cos(theta) + yo + 0.5)


def latest_base(now: dt.datetime) -> tuple[str, str]:
    """now(KST) 기준 가장 최근 발표의 (base_date, base_time)."""
    now = now.astimezone(KST)
    m = now.hour * 60 + now.minute
    done = [h for h in ISSUE_HOURS if h * 60 + ISSUE_DELAY_MIN <= m]
    if done:
        return now.strftime("%Y%m%d"), f"{done[-1]:02d}00"
    prev = now - dt.timedelta(days=1)
    return prev.strftime("%Y%m%d"), f"{ISSUE_HOURS[-1]:02d}00"


def region_center(data_dir: str | Path) -> tuple[float, float]:
    """가상 권역 어르신 좌표의 평균(실제 행정동 경계 안)."""
    b = pd.read_csv(Path(data_dir) / "beneficiaries.csv")
    return float(b["lat"].mean()), float(b["lon"].mean())


def items_to_frame(items: list[dict], fetched_at: str) -> pd.DataFrame:
    need = ("baseDate", "baseTime", "fcstDate", "fcstTime", "category", "fcstValue", "nx", "ny")
    rows = []
    for it in items:
        missing = [k for k in need if k not in it]
        if missing:
            from f0.apiclient import ApiCallError
            raise ApiCallError("invalid_response", f"예보 항목이 빠졌습니다: {', '.join(missing)}")
        rows.append({"base_date": str(it["baseDate"]), "base_time": str(it["baseTime"]).zfill(4),
                     "fcst_date": str(it["fcstDate"]), "fcst_time": str(it["fcstTime"]).zfill(4),
                     "category": str(it["category"]), "value": str(it["fcstValue"]),
                     "nx": int(it["nx"]), "ny": int(it["ny"])})
    df = pd.DataFrame(rows, columns=["base_date", "base_time", "fcst_date", "fcst_time", "category", "value",
                                     "nx", "ny"])
    df["provider"] = "기상청 단기예보(플랫폼)"
    df["fetched_at"] = fetched_at
    return df


def fetch_forecast(client, lat: float, lon: float, now: dt.datetime | None = None) -> pd.DataFrame:
    """기상청 단기예보 한 번 호출. 실패는 예외로 올린다. 결과가 0건이면 invalid_response."""
    from f0.apiclient import ApiCallError
    now = now or dt.datetime.now(KST)
    nx, ny = latlon_to_grid(lat, lon)
    base_date, base_time = latest_base(now)
    items = client.call_all_pages("weather", max_pages=5, page_delay_sec=1.0,
                                  base_date=base_date, base_time=base_time, nx=nx, ny=ny)
    if not items:
        raise ApiCallError("invalid_response", f"예보가 0건입니다(발표 {base_date} {base_time}, 격자 {nx},{ny})")
    return items_to_frame(items, now.astimezone(KST).isoformat(timespec="seconds"))


def save_cache(df: pd.DataFrame, raw_dir: str | Path) -> Path:
    p = Path(raw_dir) / CACHE_NAME
    df.to_csv(p, index=False, encoding="utf-8-sig")
    return p


def load_cache(raw_dir: str | Path) -> pd.DataFrame | None:
    p = Path(raw_dir) / CACHE_NAME
    if not p.exists():
        return None
    return pd.read_csv(p, dtype=str)


def by_time(df: pd.DataFrame) -> pd.DataFrame:
    """예보 시각 × category 표(값은 원문 그대로). 열 이름은 확인된 코드만 한국어로."""
    t = df.pivot_table(index=["fcst_date", "fcst_time"], columns="category", values="value", aggfunc="first")
    t = t.reset_index()
    t.insert(0, "예보 시각", pd.to_datetime(t["fcst_date"] + t["fcst_time"], format="%Y%m%d%H%M")
             .dt.strftime("%m-%d %H:%M"))
    t = t.drop(columns=["fcst_date", "fcst_time"])
    t.columns.name = None
    t = t.fillna("")                      # 그 시각에 없는 코드(예: 하루 한 번 나오는 값)는 빈칸
    return t.rename(columns={k: f"{v}({k})" for k, v in CATEGORY_KO.items()})
