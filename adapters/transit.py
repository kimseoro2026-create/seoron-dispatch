"""대중교통 이동시간 — ODsay 대중교통 길찾기(플랫폼 A03). 병원동행 참고용이며 배정 계산에는 쓰지 않는다.

PRD 11.3 경로 계약:
- 내부 시간은 duration_seconds 로 통일한다(ODsay totalTime 은 분 → ×60, AT-21).
- 성공일 때만 양수 duration_seconds. 실패면 시간·거리는 None(0분으로 바꾸지 않음).
- 출발 시각을 받는 요청변수가 없어 time_basis 는 provider_unspecified(AT-26).
- 실패하면 화면은 자동차 시간(OSRM)으로 대체하고 그 사실을 표시한다(AT-06). 둘 다 없으면 '확인 불가'.
응답 항목은 2026-11-15 상세 화면의 출력결과 표 이름만 쓴다: result.path[].pathType, path[].info.totalTime,
info.totalDistance, info.totalWalk, error.code, error.message.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

KST = ZoneInfo("Asia/Seoul")
CACHE_NAME = "transit_sample.csv"
PATH_TYPE_KO = {1: "지하철", 2: "버스", 3: "버스+지하철"}        # 상세 화면 pathType [도시내]

# ApiCallError.kind → PRD 11.3 status
_STATUS = {"auth": "auth_failed", "forbidden": "access_denied", "rate_limited": "rate_limited",
           "timeout": "timeout", "invalid_response": "invalid_response"}


def _base(from_id: str, to_id: str, now: dt.datetime) -> dict:
    return {"provider": "odsay", "mode": "transit", "from_id": from_id, "to_id": to_id,
            "duration_seconds": None, "distance_m": None, "walk_m": None, "path_type": None,
            "time_basis": "provider_unspecified", "requested_departure_at": None,
            "fetched_at": now.astimezone(KST).isoformat(timespec="seconds"),
            "data_mode": "live", "status": None, "provider_result_code": None, "error_kind": None,
            "message": ""}


def parse_paths(body: str) -> dict:
    """ODsay 본문 → 가장 짧은 경로의 {minutes, distance_m, walk_m, path_type}. 경로가 없으면 None."""
    data = json.loads(body)
    paths = ((data.get("result") or {}).get("path")) or []
    best = None
    for p in paths:
        info = p.get("info") or {}
        t = info.get("totalTime")
        if not isinstance(t, (int, float)) or t <= 0:
            continue
        if best is None or t < best["minutes"]:
            best = {"minutes": int(t), "distance_m": info.get("totalDistance"), "walk_m": info.get("totalWalk"),
                    "path_type": p.get("pathType")}
    return best


def estimate(client, from_id: str, frm: tuple[float, float], to_id: str, to: tuple[float, float],
             now: dt.datetime | None = None) -> dict:
    """한 방향 대중교통 소요시간. frm/to 는 (lat, lon). 실패해도 예외 대신 status 를 채워 돌려준다."""
    from f0.apiclient import ApiCallError, ApiNotConfigured
    now = now or dt.datetime.now(KST)
    r = _base(from_id, to_id, now)
    try:
        body = client.fetch_body("transit", sx=frm[1], sy=frm[0], ex=to[1], ey=to[0])
        best = parse_paths(body)
    except ApiNotConfigured as e:
        r.update(status="missing_key", message=str(e))
        return r
    except ApiCallError as e:
        r.update(status=_STATUS.get(e.kind, "provider_error"), error_kind=e.kind,
                 provider_result_code=getattr(e, "provider_code", None), message=str(e))
        return r
    except (ValueError, AttributeError) as e:
        r.update(status="invalid_response", error_kind="invalid_response",
                 message=f"경로 응답을 읽을 수 없습니다: {type(e).__name__}")
        return r
    if best is None:
        r.update(status="no_route", message="대중교통 경로가 없습니다")
        return r
    r.update(status="ok", duration_seconds=best["minutes"] * 60, distance_m=best["distance_m"],
             walk_m=best["walk_m"], path_type=best["path_type"])
    return r


def display_minutes(route: dict | None, car_minutes: float | None) -> tuple[float | None, str]:
    """화면에 보일 (분, 설명). 대중교통 실패면 자동차 시간으로 대체하고 표시한다. 0분으로 바꾸지 않는다."""
    if route and route.get("status") == "ok" and route.get("duration_seconds"):
        kind = PATH_TYPE_KO.get(int(route["path_type"])) if route.get("path_type") is not None else None
        return route["duration_seconds"] / 60, f"대중교통{f'({kind})' if kind else ''} · ODsay · 시간 기준 미지정"
    why = route.get("status") if route else "조회 안 함"
    if car_minutes is not None and car_minutes > 0:
        return float(car_minutes), f"자동차 시간으로 대체(대중교통 {why})"
    return None, f"확인 불가(대중교통 {why}, 자동차 시간 없음)"


def save_cache(rows: list[dict], raw_dir: str | Path) -> Path:
    p = Path(raw_dir) / CACHE_NAME
    pd.DataFrame(rows).to_csv(p, index=False, encoding="utf-8-sig")
    return p


def load_cache(raw_dir: str | Path) -> pd.DataFrame | None:
    p = Path(raw_dir) / CACHE_NAME
    return pd.read_csv(p) if p.exists() else None
