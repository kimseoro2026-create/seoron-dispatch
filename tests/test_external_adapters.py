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


def test_items_to_frame_and_pivot_keep_raw_codes():
    df = weather.items_to_frame([_item("0900", "TMP", "8"), _item("0900", "POP", "20"),
                                 _item("1000", "TMP", "9")], "2026-11-15T21:00:00+09:00")
    t = weather.by_time(df)
    assert list(t["예보 시각"]) == ["11-16 09:00", "11-16 10:00"]
    assert "TMP" in t.columns and t.loc[0, "TMP"] == "8"          # 코드 뜻 확인 전에는 코드 그대로


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


@pytest.mark.parametrize("body", [json.dumps({"result": {"path": []}}),
                                  json.dumps({"result": {"path": [{"info": {"totalTime": 0}}]}})])
def test_no_route_never_zero(body):
    r = transit.estimate(FakeClient(body), "a", A, "b", B, NOW)
    assert r["status"] == "no_route" and r["duration_seconds"] is None


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
