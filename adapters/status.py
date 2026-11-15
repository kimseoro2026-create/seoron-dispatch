"""데이터 연결 상태(W11) — 외부 자원마다 지금 상태를 정한다. 화면을 열 때 외부로 호출하지 않는다.

상태(FR-45)
  verified   : 이 앱의 데이터에 그 자원에서 받은 값이 실제로 들어 있다(근거를 함께 적는다)
  configured : 키·주소는 설정돼 있지만, 이 앱 데이터에서 성공 근거를 찾지 못했다
  mock       : 모의(가상) 값을 쓰고 있다
  degraded   : 쓰려고 했지만 실패해 대체값(예: 직선거리 추정)을 쓰고 있다
  disabled   : 아직 쓰지 않는다
비밀값은 절대 보여주지 않는다. 키는 '설정됨/없음'만 적는다(FR-47).
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import pandas as pd
import yaml


def _env(name: str) -> str:
    return "설정됨" if os.environ.get(name) else "없음"


def _mtime(p: Path) -> str | None:
    return dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M") if p.exists() else None


def _todo(cfg: dict, key: str) -> bool:
    return "TODO" in json.dumps(cfg.get(key, {}), ensure_ascii=False)


# 사용 승인 단계(FR-51). 승인 여부는 CLAUDE.md 8절 표에 기록된 사실만 적는다.
APPROVAL = {"병원 위치": "승인", "고령인구": "승인", "날씨": "승인", "대중교통": "승인",
            "주소→좌표": "신청 전", "차량 이동시간": "자체 서버(승인 불필요)", "AI(요청 카드)": "승인"}
STAGES = ("신청 전", "승인", "키 설정", "호출 검증")


def connection_stage(row: dict) -> str:
    """문서확인/신청중/승인/키설정/호출검증 중 지금 단계. 키 설정만으로 '호출 검증'이 되지 않는다(FR-51, AT-22)."""
    approval = APPROVAL.get(row["name"], "신청 전")
    if row["state"] == "verified":
        return "호출 검증"
    if str(row.get("key", "")).endswith("설정됨") and approval != "자체 서버(승인 불필요)":
        return "키 설정"                 # 키를 받았다면 승인된 것
    if approval == "신청 전":
        return "신청 전"
    return approval if approval in STAGES else "승인"


def adapter_states(root: str | Path) -> list[dict]:
    root = Path(root)
    proc, raw = root / "data" / "processed", root / "data" / "raw"
    try:
        apis = yaml.safe_load((root / "config" / "apis.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        apis = {}
    try:
        summary = json.loads((proc / "summary.json").read_text(encoding="utf-8"))
    except Exception:
        summary = {}
    rows = []

    # 병원 위치 — 심평원 병원기본목록
    try:
        h = pd.read_csv(proc / "hospitals.csv")
        mock = h["hospital_id"].astype(str).str.startswith("MOCK").any()
        state, evidence = ("mock", f"모의 병원 {len(h)}곳") if mock else ("verified", f"API로 받은 병원 {len(h)}곳")
    except Exception:
        state, evidence = "disabled", "병원 파일 없음"
    rows.append({"name": "병원 위치", "provider": "디지털융합플랫폼 · 심평원 병원기본목록", "state": state,
                 "evidence": evidence, "key": _env("KOREACONNECT_API_USER_KEY_ID"),
                 "last_update": _mtime(raw / "hospitals.csv"), "period": "수집 시점 기준",
                 "used_for": "병원 위치(동행 목적지)"})

    # 고령인구 — 행안부
    p = pd.DataFrame()
    try:
        p = pd.read_csv(raw / "population.csv")
        mock = p["dong"].astype(str).str.startswith("가상동").any()
    except Exception:
        mock = True
    pop_todo = _todo(apis, "population")
    api_note, period = "", "확인 안 됨"
    try:
        pa = pd.read_csv(raw / "population_api.csv", dtype={"stats_ym": str})
        period = f"{pa['stats_ym'].iloc[0]} 기준"
        api_note = (f" · 실제 인구는 받아 둠({len(pa)}개 행정동, {period}, population_api.csv) — "
                    "행정동 경계가 모의라 아직 분포에 쓰지 않음")
    except Exception:
        pass
    rows.append({"name": "고령인구", "provider": "디지털융합플랫폼 · 행안부 행정동별 성·연령별 인구",
                 "state": "mock" if mock else "verified",
                 "evidence": (("모의 인구(요청변수 미확인: config/apis.yaml 에 TODO)" if pop_todo else "모의 인구")
                              + api_note) if mock else
                 ("API로 받은 행정동 인구" + (" · 65세 이상은 대략값(70세 이상 + 60대 절반)"
                                           if "age_65_plus_method" in p.columns else "")),
                 "key": _env("KOREACONNECT_API_USER_KEY_ID"),
                 "last_update": _mtime(raw / "population.csv"), "period": period, "used_for": "가상 어르신 분포"})

    # 날씨 — 기상청 단기예보(저장된 실제 호출 결과로 판단)
    wf = raw / "weather_forecast.csv"
    try:
        w = pd.read_csv(wf, dtype=str)
        st_, ev = "verified", (f"API로 받은 예보 {len(w)}행(발표 {w['base_date'].iloc[0]} {w['base_time'].iloc[0]}, "
                               f"격자 {w['nx'].iloc[0]},{w['ny'].iloc[0]})")
        period = f"{w['fcst_date'].min()}~{w['fcst_date'].max()} 예보"
    except Exception:
        st_, ev, period = "disabled", ("호출 설정 미완료(TODO)" if _todo(apis, "weather")
                                       else "저장된 예보 없음(python -m adapters.fetch weather)"), "-"
    rows.append({"name": "날씨", "provider": "디지털융합플랫폼 · 기상청 단기예보", "state": st_, "evidence": ev,
                 "key": _env("KOREACONNECT_API_USER_KEY_ID"), "last_update": _mtime(wf), "period": period,
                 "used_for": "오늘의 돌봄 현황의 날씨 참고(배정 계산에는 쓰지 않음)"})

    # 대중교통 — ODsay(표본 조회 결과로 판단)
    tf = raw / "transit_sample.csv"
    try:
        t = pd.read_csv(tf)
        ok = int((t["status"] == "ok").sum())
        fails = t.loc[t["status"] != "ok", "status"].value_counts().to_dict()
        fail_txt = (" · 실패 " + ", ".join(f"{k} {v}건" for k, v in fails.items())) if fails else ""
        st_ = "verified" if ok else "degraded"
        ev = f"표본 {len(t)}건 중 성공 {ok}건{fail_txt}"
    except Exception:
        st_, ev = "disabled", ("호출 설정 미완료(TODO)" if _todo(apis, "transit")
                               else "저장된 조회 없음(python -m adapters.fetch transit)")
    rows.append({"name": "대중교통", "provider": "디지털융합플랫폼 · ODsay 대중교통 길찾기", "state": st_,
                 "evidence": ev, "key": _env("KOREACONNECT_API_USER_KEY_ID"), "last_update": _mtime(tf),
                 "period": "출발 시각 지정 불가(시간 기준 미지정)",
                 "used_for": "병원동행 대중교통 참고 시간(배정 계산은 자동차 이동)"})

    # 차량 이동시간 — OSRM 행렬
    from planner.data import matrix_path
    mp = matrix_path(proc)
    src = None
    if mp is not None:
        try:
            src = str(pd.read_csv(mp, nrows=1)["source"].iloc[0])
        except Exception:
            src = None
    if src == "osrm":
        st_, ev = "verified", f"OSRM 도로 경로 행렬({mp.name})"
    elif mp is None or src == "estimate":
        st_, ev = "degraded", "행렬 없음 → 직선거리 추정(estimate)으로 계산"
    else:
        st_, ev = "configured", f"행렬 출처 '{src}'"
    rows.append({"name": "차량 이동시간", "provider": "OSRM(자체 서버) — 카카오 표본 보정 후보", "state": st_, "evidence": ev,
                 "key": "주소 " + _env("OSRM_URL"), "last_update": _mtime(mp) if mp else None,
                 "period": "지도 자료 기준", "used_for": "모든 배정 계산의 이동시간"})

    # 주소→좌표 — 카카오
    rows.append({"name": "주소→좌표", "provider": "카카오 로컬", "state": "configured" if os.environ.get("KAKAO_REST_API_KEY")
                 else "disabled", "evidence": "키만 설정됨(호출 기록 없음)" if os.environ.get("KAKAO_REST_API_KEY")
                 else "필요 시 신청", "key": _env("KAKAO_REST_API_KEY"), "last_update": None, "period": "-",
                 "used_for": "아직 쓰지 않음(가상 좌표 사용)"})

    # AI(요청 카드) — LLM 게이트웨이
    cache = root / "data" / "llm_cache"
    n_cache = len(list(cache.glob("*.json"))) if cache.exists() else 0
    configured = bool(os.environ.get("LLM_API_KEY"))
    if n_cache:
        st_, ev = "verified", f"저장된 AI 정리 결과 {n_cache}건(키 없이 이 결과로 보여줌)"
    else:
        st_, ev = ("configured", "키 설정됨, 저장된 결과 없음") if configured else ("disabled", "키·저장 결과 없음")
    last = max((p.stat().st_mtime for p in cache.glob("*.json")), default=None) if n_cache else None
    rows.append({"name": "AI(요청 카드)", "provider": "모두의 AI 실험실 게이트웨이(11월까지)", "state": st_, "evidence": ev,
                 "key": _env("LLM_API_KEY"),
                 "last_update": dt.datetime.fromtimestamp(last).strftime("%Y-%m-%d %H:%M") if last else None,
                 "period": "팀 포인트 12월 초 소멸", "used_for": "메모 → 요청 카드 정리"})

    for r in rows:
        r["terms"] = "확인 필요"     # 이용조건 검토 상태: 아직 기록이 없다(지어내지 않음)
        r["quota"] = "확인 안 됨"
        r["stage"] = connection_stage(r)
    if summary:
        rows.append({"name": "가상 권역", "provider": "f0 생성기(이 앱)", "state": "mock",
                     "evidence": f"어르신 {summary.get('beneficiaries')}명·인력 {summary.get('workers')}명·"
                                 f"요청 {summary.get('requests')}건(모두 가상)", "key": "-",
                     "last_update": str(summary.get("generated_at", ""))[:16].replace("T", " "), "period": "평일 30일",
                     "used_for": "모든 화면의 어르신·인력·요청", "terms": "해당 없음", "quota": "-",
                     "stage": "해당 없음"})
    return rows
