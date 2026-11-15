"""날씨 — 기상청 단기예보조회(플랫폼 A10). PRD 10장: 일정 화면 주의 표시용. 배정 숫자에는 쓰지 않는다.

- 요청변수·출력 항목은 2026-11-15 플랫폼 상세 화면에서 확인한 이름만 쓴다(config/apis.yaml weather).
- category 코드의 뜻은 플랫폼 화면에 없어 기상청 활용가이드(공공데이터포털 참고문서)에서 옮겼다.
  가이드에 없는 코드는 코드 그대로 보여준다(지어내지 않음).
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

# 출처: 기상청 「단기예보 조회서비스 API 활용가이드」(261019, 공공데이터포털 참고문서) '코드값 정보'.
# 항목명(단위). 가이드에 적힌 것만 옮겼다.
CATEGORY_KO: dict[str, str] = {
    "POP": "강수확률(%)", "PTY": "강수형태", "PCP": "1시간 강수량", "REH": "습도(%)", "SNO": "1시간 신적설",
    "SKY": "하늘상태", "TMP": "1시간 기온(℃)", "TMN": "일 최저기온(℃)", "TMX": "일 최고기온(℃)",
    "UUU": "풍속 동서성분(m/s)", "VVV": "풍속 남북성분(m/s)", "WAV": "파고(M)", "VEC": "풍향(deg)", "WSD": "풍속(m/s)",
}
# 같은 가이드 '특정 요소의 코드값'(단기예보). PCP·SNO 는 기상청이 문자열('강수없음', '1mm 미만' 등)로 보내 그대로 쓴다.
VALUE_KO: dict[str, dict[str, str]] = {
    "SKY": {"1": "맑음", "3": "구름많음", "4": "흐림"},
    "PTY": {"0": "없음", "1": "비", "2": "비/눈", "3": "눈", "4": "소나기"},
}
# 담당자 화면에 보일 항목(바람 성분·풍향·파고는 일정 주의 표시에 쓰지 않아 뺀다)
DISPLAY = ("TMP", "TMN", "TMX", "SKY", "PTY", "POP", "PCP", "SNO", "REH", "WSD")

# 단기예보 발표시각(하루 8회)과 API 제공 시각(발표 10분 뒤) — 같은 가이드 '예보 발표시각'.
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


def _kst(d: str, t: str) -> dt.datetime:
    return dt.datetime.strptime(f"{d}{str(t).zfill(4)}", "%Y%m%d%H%M").replace(tzinfo=KST)


def freshness(df: pd.DataFrame, now: dt.datetime | None = None) -> dict:
    """저장된 예보가 지금 기준으로 얼마나 오래됐는지.

    state: current(지금 쓸 최신 발표) / stale(더 새 발표가 이미 나옴) / expired(예보 시각이 모두 지남) /
    unknown(발표 시각을 읽을 수 없음). age_hours 는 발표 시각부터 지금까지(시간). 지어내지 않고, 읽을 수 없으면 unknown.
    """
    now = (now or dt.datetime.now(KST)).astimezone(KST)
    try:
        base = _kst(str(df["base_date"].iloc[0]), str(df["base_time"].iloc[0]))
        last = max(_kst(str(d), str(t)) for d, t in zip(df["fcst_date"], df["fcst_time"]))
    except (ValueError, KeyError, IndexError):
        return {"state": "unknown", "age_hours": None, "newer_base": None}
    age = round((now - base).total_seconds() / 3600, 2)
    newest = latest_base(now)
    newer = newest if _kst(*newest) > base else None
    if last < now:
        state = "expired"
    elif newer is not None:
        state = "stale"
    else:
        state = "current"
    return {"state": state, "age_hours": age, "newer_base": newer}


def freshness_words(f: dict) -> str | None:
    """화면 경고 문구. 최신이면 None."""
    st, age, nb = f["state"], f["age_hours"], f["newer_base"]
    if st == "current":
        return None
    if st == "unknown":
        return "예보 발표 시각을 확인할 수 없어 최신인지 알 수 없습니다."
    when = f"{nb[0][4:6]}/{nb[0][6:]} {nb[1][:2]}시" if nb else ""
    if st == "expired":
        return (f"지난 예보입니다. 예보 시각이 모두 지났습니다(발표 후 {age:.0f}시간). "
                "`python -m adapters.fetch weather` 로 새로 받아야 합니다.")
    return (f"발표 후 {age:.0f}시간 지난 예보입니다. 기상청 새 발표({when})가 이미 나왔습니다. "
            "`python -m adapters.fetch weather` 로 새로 받으면 바뀝니다.")


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


def by_time(df: pd.DataFrame, display_only: bool = True) -> pd.DataFrame:
    """예보 시각 × 항목 표. 하늘상태·강수형태 코드는 가이드의 말로, 나머지 값은 기상청이 보낸 그대로."""
    df = df.copy()
    for cat, m in VALUE_KO.items():
        sel = df["category"] == cat
        df.loc[sel, "value"] = df.loc[sel, "value"].map(lambda v: m.get(str(v), f"코드 {v}"))
    if display_only:
        df = df[df["category"].isin(DISPLAY)]
    t = df.pivot_table(index=["fcst_date", "fcst_time"], columns="category", values="value", aggfunc="first")
    t = t.reset_index()
    t.insert(0, "예보 시각", pd.to_datetime(t["fcst_date"] + t["fcst_time"], format="%Y%m%d%H%M")
             .dt.strftime("%m-%d %H:%M"))
    t = t.drop(columns=["fcst_date", "fcst_time"])
    t.columns.name = None
    t = t.fillna("")                      # 그 시각에 없는 코드(예: 하루 한 번 나오는 값)는 빈칸
    order = ["예보 시각"] + [c for c in DISPLAY if c in t.columns] + [c for c in t.columns
                                                                    if c != "예보 시각" and c not in DISPLAY]
    return t[order].rename(columns=CATEGORY_KO)
