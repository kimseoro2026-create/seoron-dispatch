"""날씨(기상청)·대중교통(ODsay) 어댑터. 실제 네트워크는 쓰지 않는다(PRD AT-06·21·26)."""
import datetime as dt
import json

import pytest

from adapters import transit, weather
from f0.apiclient import ApiCallError, ApiNotConfigured, PlatformClient

KST = weather.KST


# ── 날씨 ──────────────────────────────────────────────────────────────
def test_grid_conversion_known_points():
    assert weather.latlon_to_grid(37.5665, 126.9780) == (60, 127)   # 서울시청
    # 가상 권역 중심(동탄구) — 2026-11-15 실제 호출에서 기상청이 돌려준 격자도 62,119 였다
    assert weather.latlon_to_grid(37.192484, 127.100436) == (62, 119)


@pytest.mark.parametrize("hhmm, expect", [
    ("0005", ("20261114", "2300")), ("0209", ("20261114", "2300")), ("0210", ("20261115", "0200")),
    ("1159", ("20261115", "1100")), ("2359", ("20261115", "2300")),
])
def test_latest_base(hhmm, expect):
    now = dt.datetime(2026, 11, 15, int(hhmm[:2]), int(hhmm[2:]), tzinfo=KST)
    assert weather.latest_base(now) == expect


def _item(t, cat, v):
    return {"baseDate": "20261115", "baseTime": "2000", "fcstDate": "20261116", "fcstTime": t,
            "category": cat, "fcstValue": v, "nx": "62", "ny": "119"}


def test_pivot_uses_guide_names_and_codes():
    df = weather.items_to_frame([_item("0900", "TMP", "8"), _item("0900", "POP", "20"),
                                 _item("0900", "SKY", "4"), _item("0900", "PTY", "9"),
                                 _item("0900", "PCP", "강수없음"), _item("0900", "UUU", "0.3"),
                                 _item("1000", "TMP", "9"), _item("0900", "ZZZ", "1")],
                                "2026-11-15T21:00:00+09:00")
    t = weather.by_time(df)
    assert list(t["예보 시각"]) == ["11-16 09:00", "11-16 10:00"]
    assert t.loc[0, "1시간 기온(℃)"] == "8" and t.loc[1, "강수확률(%)"] == ""
    assert t.loc[0, "하늘상태"] == "흐림" and t.loc[0, "강수형태"] == "코드 9"   # 가이드에 없는 값은 지어내지 않음
    assert t.loc[0, "1시간 강수량"] == "강수없음"
    assert "풍속 동서성분(m/s)" not in t.columns and "ZZZ" not in t.columns   # 담당자 화면에서는 뺀다
    full = weather.by_time(df, display_only=False)
    assert "풍속 동서성분(m/s)" in full.columns and "ZZZ" in full.columns       # 모르는 코드는 코드 그대로


def test_missing_field_is_invalid_response():
    bad = _item("0900", "TMP", "8")
    del bad["fcstValue"]
    with pytest.raises(ApiCallError) as e:
        weather.items_to_frame([bad], "x")
    assert e.value.kind == "invalid_response"


def test_empty_forecast_is_failure_not_empty_table():
    class C:
        def call_all_pages(self, *a, **k):
            return []
    with pytest.raises(ApiCallError):
        weather.fetch_forecast(C(), 37.2, 127.07, dt.datetime(2026, 11, 15, 12, tzinfo=KST))


# ── 대중교통 ──────────────────────────────────────────────────────────
class FakeClient:
    def __init__(self, body=None, exc=None):
        self.body, self.exc, self.calls = body, exc, []

    def fetch_body(self, name, **kw):
        self.calls.append((name, kw))
        if self.exc:
            raise self.exc
        return self.body


OK = json.dumps({"result": {"searchType": 0, "path": [
    {"pathType": 2, "info": {"totalTime": 31, "totalDistance": 8000, "totalWalk": 500}},
    {"pathType": 3, "info": {"totalTime": 12, "totalDistance": 7000, "totalWalk": 300}}]}})
A, B = (37.20, 127.07), (37.21, 127.08)
NOW = dt.datetime(2026, 11, 15, 21, tzinfo=KST)


def test_ok_uses_seconds_and_lon_lat_order():
    c = FakeClient(OK)
    r = transit.estimate(c, "BENEFICIARY:B1", A, "HOSPITAL:H1", B, NOW)
    assert r["status"] == "ok" and r["duration_seconds"] == 720           # 12분 → 720초 (AT-21)
    assert r["mode"] == "transit" and r["provider"] == "odsay"
    assert r["time_basis"] == "provider_unspecified"                        # AT-26
    assert c.calls[0][1] == {"sx": 127.07, "sy": 37.20, "ex": 127.08, "ey": 37.21}   # X=경도, Y=위도


def test_no_route_never_zero():
    r = transit.estimate(FakeClient(json.dumps({"result": {"path": []}})), "a", A, "b", B, NOW)
    assert r["status"] == "no_route" and r["duration_seconds"] is None


@pytest.mark.parametrize("t", [0, -5, "NaN", None, "12분"])
def test_abnormal_time_is_invalid_response(t):
    """AT-25: 음수·0·NaN·숫자 아닌 소요시간 → invalid_response. 정상 경로가 함께 있어도 그 응답 전체를 쓰지 않는다."""
    body = json.dumps({"result": {"path": [{"info": {"totalTime": 20}}, {"info": {"totalTime": t}}]}})
    body = body.replace('"NaN"', "NaN")                        # JSON 표준 밖 NaN 도 들어올 수 있다
    r = transit.estimate(FakeClient(body), "a", A, "b", B, NOW)
    assert r["status"] == "invalid_response" and r["duration_seconds"] is None


@pytest.mark.parametrize("exc, status", [
    (ApiCallError("auth", "x", 401), "auth_failed"), (ApiCallError("forbidden", "x", 403), "access_denied"),
    (ApiCallError("rate_limited", "x", 429), "rate_limited"), (ApiCallError("timeout", "x"), "timeout"),
    (ApiCallError("server", "x", 503), "provider_error"), (ApiNotConfigured("키 없음"), "missing_key"),
])
def test_failures_keep_null_duration(exc, status):
    r = transit.estimate(FakeClient(exc=exc), "a", A, "b", B, NOW)
    assert r["status"] == status and r["duration_seconds"] is None and r["distance_m"] is None


def test_unreadable_body():
    r = transit.estimate(FakeClient("not json"), "a", A, "b", B, NOW)
    assert r["status"] == "invalid_response" and r["duration_seconds"] is None


def test_odsay_error_body_through_real_client():
    """ODsay 오류 본문(-98)이 플랫폼 클라이언트를 거쳐 provider_error + 코드로 남는다."""
    from tests.test_apiclient_errors import Resp, Session
    cfg = {"key_header": "api_user_key_id", "retries": 0, "retry_backoff_sec": 0,
           "transit": {"url": "https://example.test/t", "params": {"SX": "{sx}", "SY": "{sy}", "EX": "{ex}",
                                                                   "EY": "{ey}"}}}
    c = PlatformClient(api_key="k", config=cfg)
    c.session = Session(Resp(200, json.dumps({"error": {"code": "-98", "message": "출, 도착지가 700m이내입니다."}}),
                             "application/json"))
    r = transit.estimate(c, "a", A, "b", B, NOW)
    assert r["status"] == "provider_error" and r["provider_result_code"] == "-98"
    assert r["duration_seconds"] is None


def test_display_falls_back_to_car_time_and_says_so():
    ok = transit.estimate(FakeClient(OK), "a", A, "b", B, NOW)
    m, why = transit.display_minutes(ok, 9)
    assert m == 12 and "대중교통" in why
    fail = transit.estimate(FakeClient(exc=ApiCallError("timeout", "x")), "a", A, "b", B, NOW)
    m, why = transit.display_minutes(fail, 9)
    assert m == 9 and "자동차 시간으로 대체" in why                    # AT-06
    m, why = transit.display_minutes(fail, None)
    assert m is None and "확인 불가" in why                             # 0분으로 바꾸지 않음


# ── 오래된 예보 표시 ─────────────────────────────────────────────────────────
def _wf(base_date, base_time, fcst):
    import pandas as pd
    return pd.DataFrame({"base_date": base_date, "base_time": base_time,
                         "fcst_date": [d for d, _ in fcst], "fcst_time": [t for _, t in fcst],
                         "category": "TMP", "value": "5", "nx": "62", "ny": "120",
                         "fetched_at": "2026-11-16T05:20:00+09:00"})


def test_freshness_current_when_no_newer_issue():
    df = _wf("20261116", "0500", [("20261116", "0600"), ("20261117", "0600")])
    f = weather.freshness(df, dt.datetime(2026, 11, 16, 7, 0, tzinfo=weather.KST))
    assert f["state"] == "current" and f["newer_base"] is None
    assert f["age_hours"] == pytest.approx(2.0)


def test_freshness_stale_when_newer_issue_exists():
    df = _wf("20261116", "0500", [("20261116", "0600"), ("20261117", "0600")])
    f = weather.freshness(df, dt.datetime(2026, 11, 16, 14, 30, tzinfo=weather.KST))
    assert f["state"] == "stale" and f["newer_base"] == ("20261116", "1400")
    assert f["age_hours"] == pytest.approx(9.5)


def test_freshness_expired_when_all_times_passed():
    df = _wf("20261110", "0500", [("20261110", "0600"), ("20261111", "2300")])
    f = weather.freshness(df, dt.datetime(2026, 11, 16, 7, 0, tzinfo=weather.KST))
    assert f["state"] == "expired"


def test_freshness_unknown_on_bad_base():
    df = _wf("2026-11-16", "abc", [("20261116", "0600")])
    assert weather.freshness(df, dt.datetime(2026, 11, 16, 7, 0, tzinfo=weather.KST))["state"] == "unknown"


def test_freshness_words():
    assert weather.freshness_words({"state": "current", "age_hours": 1.0, "newer_base": None}) is None
    assert "새 발표" in weather.freshness_words({"state": "stale", "age_hours": 9.5, "newer_base": ("20261116", "1400")})
    assert "지난" in weather.freshness_words({"state": "expired", "age_hours": 146.0, "newer_base": ("20261116", "0500")})
    assert "확인할 수 없" in weather.freshness_words({"state": "unknown", "age_hours": None, "newer_base": None})
