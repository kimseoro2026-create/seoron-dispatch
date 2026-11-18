"""f0 생성 결과(CSV) → 배정 엔진 입력(instance dict).

이름은 CLAUDE.md 5절 데이터 계약을 따른다. f0가 만든 열 이름이 계약과 다른 부분은
여기서 한 번만 변환하고, 엔진·검증기는 계약 이름만 본다.

  service              → service_type
  time_window_start_min → earliest_start_min
  time_window_end_min   → latest_start_min
  service_duration_min  → service_minutes

주의(미래 정보 금지, CLAUDE.md 절대원칙 7):
  cancelled_same_day 는 '당일 취소'라 계획 시점에는 알 수 없다. 따라서 여기서 걸러내지 않고
  요청을 그대로 남긴다. 취소는 시뮬레이터에서 이벤트로 재생한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# f0 가 만든 노드 접두어 (f0/matrix.py build_nodes 와 같아야 한다)
WORKER_PREFIX = "WORKER:"
BENEFICIARY_PREFIX = "BENEFICIARY:"
HOSPITAL_PREFIX = "HOSP:"


@dataclass
class DayInstance:
    """하루치 배정 입력. verifier.verify(instance, assignments) 에 그대로 넣을 수 있다."""
    service_date: str
    workers: list[dict]
    requests: list[dict]
    travel: dict[tuple[str, str], float]
    worker_travel: dict | None = None      # {수단: {(from, to): 분}} — 대중교통 인력의 이동(planner.travel_modes)

    def as_instance(self) -> dict:
        inst = {"workers": self.workers, "requests": self.requests, "travel": self.travel}
        if self.worker_travel:
            inst["worker_travel"] = self.worker_travel
        return inst


def matrix_path(data_dir: str | Path) -> Path | None:
    """이동시간 행렬 파일 위치. 원본 CSV 를 먼저, 없으면 압축본(.csv.gz)을 쓴다.

    원본(약 61MB)은 저장소에 넣지 않고, 압축본(약 4MB)만 넣어 심사용 배포에서도
    OSRM 행렬을 쓰게 한다. 둘 다 없으면 None — 호출자가 추정 행렬로 대체한다.
    """
    d = Path(data_dir)
    for name in ("travel_matrix.csv", "travel_matrix.csv.gz"):
        if (d / name).exists():
            return d / name
    return None


ESCORT_TRAVEL_NAME = "escort_travel.csv"


def escort_mode(default: str = "car") -> str:
    """config/region.yaml escort_travel.default_mode (transit / car). 설정이 없으면 예전 가정(car)."""
    import yaml
    p = Path(__file__).resolve().parent.parent / "config" / "region.yaml"
    try:
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except OSError:
        return default
    mode = ((cfg.get("escort_travel") or {}).get("default_mode") or default)
    if mode not in ("transit", "car"):
        raise ValueError(f"escort_travel.default_mode 는 transit 또는 car 여야 합니다: {mode!r}")
    return mode


def load_escort_travel(data_dir: str | Path) -> pd.DataFrame:
    """escort_travel.csv(병원동행 집↔병원 구간의 대중교통·도보 시간). 없으면 빈 표."""
    p = Path(data_dir) / ESCORT_TRAVEL_NAME
    cols = ["from_node", "to_node", "minutes", "mode", "source"]
    if not p.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(p)
    m = pd.to_numeric(df["minutes"], errors="coerce")
    return df[m.notna() & np.isfinite(m) & (m > 0)]          # 0·음수·NaN 은 쓰지 않는다(0분 대체 금지)


def apply_escort_travel(travel: dict[tuple[str, str], float], data_dir: str | Path,
                        mode: str | None = None) -> dict[tuple[str, str], float]:
    """동행 수단이 transit 이면 (집, 병원)·(병원, 집) 구간을 escort_travel.csv 값으로 바꾼다.

    병원 노드는 동행 안에서만 지나가므로(인력이 혼자 병원에 가는 일은 없다) 이 두 방향만 바꾸면
    엔진·기준선·검증기·시뮬레이터가 같은 동행 점유시간을 쓴다. 표에 없는 구간은 자동차 값을 그대로 둔다
    — 화면은 escort_travel_status 로 '자동차 시간 대체' 건수를 센다.
    """
    mode = mode or escort_mode()
    if mode != "transit":
        return travel
    et = load_escort_travel(data_dir)
    for a, b, m in zip(et["from_node"], et["to_node"], et["minutes"]):
        if (a, b) in travel:                 # 하루치(node_ids)로 줄인 행렬이면 그 안의 구간만
            travel[(a, b)] = float(m)
    return travel


ESCORT_PICKUP_WIDTH = 20   # f0 픽업창 폭: 예약 − 이동 − 30분 ~ 예약 − 이동 − 10분 (f0/generate.py)


def align_pickup_default() -> bool:
    """config escort_travel.align_pickup (기본 False = f0 픽업창 그대로). 동행 수단이 transit 일 때만 쓴다."""
    try:
        import yaml
        cfg = yaml.safe_load((Path(__file__).resolve().parent.parent / "config" / "region.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        return False
    return bool((cfg.get("escort_travel") or {}).get("align_pickup", False))


def align_escort_pickup(requests: list[dict], travel: dict[tuple[str, str], float],
                        skip_ids: set[str] | None = None) -> int:
    """병원동행 픽업창을 실제로 쓰는 집→병원 이동시간에 맞춰 다시 정한다(제자리 수정). 바꾼 요청 수를 돌려준다.

    f0 는 픽업창을 '자동차 추정 이동'으로 만들었다(가장 늦은 픽업 = 예약 − 이동 − 도착 여유).
    동행을 대중교통으로 계산하면 그 창 안에서 출발해도 예약에 못 닿는 요청이 생긴다 — 실제 담당자라면
    가는 수단에 맞춰 픽업 시각을 정한다. 같은 규칙을 그 이동시간으로 다시 적용한다:
      가장 늦은 픽업 = floor(예약 − 이동 − 도착 여유), 가장 이른 픽업 = 그보다 20분 앞.
    예약 시각·도착 여유·체류는 바꾸지 않는다. 근무 시작 전으로 당겨져도 자르지 않는다
    (그 요청은 그대로 맡을 수 없다고 나온다 — 예약을 늦추어 성공으로 바꾸지 않음).
    구간이 없는 요청과 skip_ids(담당자가 확인한 요청 카드)는 그대로 둔다.
    """
    import math
    skip = skip_ids or set()
    n = 0
    for r in requests:
        if r.get("service_type") != "hospital_escort" or r["request_id"] in skip or r.get("appointment_min") is None:
            continue
        to_h = travel.get((r["home_node"], r["hospital_node"]))
        back = travel.get((r["hospital_node"], r["home_node"]))
        if to_h is None or back is None:
            continue
        latest = int(math.floor(int(r["appointment_min"]) - float(to_h) - int(r.get("arrival_buffer_min") or 0)))
        earliest = latest - ESCORT_PICKUP_WIDTH
        if (earliest, latest) != (r["earliest_start_min"], r["latest_start_min"]):
            n += 1
        r["earliest_start_min"], r["latest_start_min"] = earliest, latest
        r["service_minutes"] = int(r["hospital_stay_min"]) + int(round(to_h)) + int(round(back))
        r["pickup_rule"] = "travel_aligned"
    return n


def escort_travel_status(requests: list[dict], data_dir: str | Path, mode: str | None = None) -> dict:
    """병원동행 구간(두 방향)이 무엇으로 계산됐는지 센다. {mode, legs, transit, walk, car_fallback, sources}."""
    mode = mode or escort_mode()
    legs = [(r["home_node"], r["hospital_node"]) for r in requests
            if r.get("service_type") == "hospital_escort" and r.get("hospital_node")]
    legs = legs + [(b, a) for a, b in legs]
    out = {"mode": mode, "legs": len(legs), "transit": 0, "walk": 0, "car_fallback": 0, "sources": {}}
    if mode != "transit":
        out["car_fallback"] = 0
        out["car"] = len(legs)
        return out
    et = load_escort_travel(data_dir)
    kind = {(a, b): (md, src) for a, b, md, src in zip(et["from_node"], et["to_node"], et["mode"], et["source"])}
    for leg in legs:
        md, src = kind.get(leg, ("car_fallback", "osrm"))
        out[md if md in ("transit", "walk") else "car_fallback"] += 1
        out["sources"][src] = out["sources"].get(src, 0) + 1
    return out


def load_travel(data_dir: str | Path, node_ids: set[str] | None = None,
                escort: str | None = None) -> dict[tuple[str, str], float]:
    """travel_matrix.csv → {(from, to): 분}.

    node_ids 를 주면 그 노드들 사이만 남긴다(하루치 화면에 필요한 구간만 쓰기 위함).
    파일이 없으면 nodes.csv 좌표로 추정 행렬을 만든다.
    (61MB 행렬을 저장소에 넣지 않기 위한 대체 경로. 추정값이므로 화면에 출처를 표시한다.)
    escort: 병원동행 구간 수단(transit / car). None 이면 config 기본값. apply_escort_travel 참고.
    """
    return apply_escort_travel(_load_matrix(data_dir, node_ids), data_dir, escort)


def _load_matrix(data_dir: str | Path, node_ids: set[str] | None = None) -> dict[tuple[str, str], float]:
    path = matrix_path(data_dir)
    if path is None:
        return estimate_travel(data_dir, node_ids)
    df = pd.read_csv(path, usecols=["from_node", "to_node", "minutes"])
    if node_ids is not None:
        df = df[df["from_node"].isin(node_ids) & df["to_node"].isin(node_ids)]
    # AT-25: 음수·NaN·무한대 구간은 배정 입력에서 뺀다. 빠진 구간은 엔진이 '경로 확인 불가'로 다룬다(0분 대체 금지).
    m = pd.to_numeric(df["minutes"], errors="coerce")
    bad = ~(m.notna() & np.isfinite(m) & (m >= 0))
    if bad.any():
        import warnings
        warnings.warn(f"이동시간 행렬에서 비정상 구간 {int(bad.sum())}개를 뺐습니다(음수·NaN·무한대)", UserWarning)
    df = df[~bad]
    return {(r.from_node, r.to_node): float(r.minutes) for r in df.itertuples()}


def estimate_travel(data_dir: str | Path,
                    node_ids: set[str] | None = None) -> dict[tuple[str, str], float]:
    """nodes.csv 만으로 직선거리 기반 추정 행렬을 만든다(source=estimate 와 같은 식).

    numpy 로 한꺼번에 계산한다. 노드 721개면 519,120구간인데,
    파이썬 이중 반복문으로 돌리면 서버에서 수십 초가 걸려 화면이 멈춘다.
    node_ids 를 주면 그 노드들만 계산한다(하루치면 70개 안팎 → 100배 이상 빠르다).
    """
    import numpy as np
    from f0.geo import HAVERSINE_R_KM, DRIVE_DETOUR, DRIVE_KMH, DRIVE_FIXED

    nodes = pd.read_csv(Path(data_dir) / "nodes.csv")
    if node_ids is not None:
        nodes = nodes[nodes["node_id"].isin(node_ids)]
    ids = nodes["node_id"].to_numpy()
    lat = np.radians(nodes["lat"].to_numpy(dtype=float))
    lon = np.radians(nodes["lon"].to_numpy(dtype=float))

    # 위도·경도 차이를 브로드캐스트로 한 번에 (n×n)
    dlat = lat[:, None] - lat[None, :]
    dlon = lon[:, None] - lon[None, :]
    a = np.sin(dlat / 2) ** 2 + np.cos(lat)[:, None] * np.cos(lat)[None, :] * np.sin(dlon / 2) ** 2
    km = 2 * HAVERSINE_R_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))

    minutes = np.round(km * DRIVE_DETOUR / DRIVE_KMH * 60 + DRIVE_FIXED, 1)
    np.fill_diagonal(minutes, 0.0)   # 자기 자신은 0

    out: dict[tuple[str, str], float] = {}
    for i, src in enumerate(ids):
        row = minutes[i]
        for j, dst in enumerate(ids):
            if i != j:
                out[(src, dst)] = float(row[j])
    return out


def load_day(data_dir: str | Path, service_date: str,
             travel: dict[tuple[str, str], float] | None = None,
             extra_requests: list[dict] | None = None,
             exclude_ids: set[str] | list[str] | None = None,
             worker_profiles: dict | None = None,
             day_shifts: dict | None = None,
             transit_share: float | None = None,
             worker_transit: dict | None = None,
             align_pickup: bool | None = None) -> DayInstance:
    """하루치 instance 를 만든다.

    travel 을 주지 않으면 그날 필요한 노드만으로 추정 행렬을 만든다.
    전체 721개 노드를 쓰면 519,120구간이지만, 하루치면 70개 안팎(4,900구간)이라
    화면이 훨씬 빠르게 뜬다.

    extra_requests: 담당자가 확인한 요청 카드(intake.to_request). 그날 것만 더하고,
    같은 대상자·서비스 요청이 이미 있으면 카드 값으로 바꾼다.
    exclude_ids: 담당자가 취소 처리한 요청(저장소). 계산에서 뺀다.
    worker_profiles·day_shifts: 담당자가 고친 인력 설정·날짜별 근무(저장소). apply_worker_settings 참고.
    transit_share: 대중교통으로 다니는 인력 비율(None 이면 config worker_travel.transit_share — 실제 인력은 대중교통 기준이라 1).
    worker_transit: 미리 만든 대중교통 인력 이동 표(load_worker_transit). 주지 않으면 그날 노드로 만든다.
    align_pickup: 병원동행 픽업창을 travel 의 집→병원 시간에 맞춤(align_escort_pickup). None 이면
                  config 동행 수단이 transit 이고 escort_travel.align_pickup 이 켜져 있을 때만. 요청 카드는 그대로.
    """
    d = Path(data_dir)
    req = pd.read_csv(d / "requests.csv", dtype={"request_id": str, "beneficiary_id": str})
    req = req[req["service_date"] == service_date]
    workers = _workers(d)
    if worker_profiles or day_shifts:
        workers = apply_worker_settings(workers, service_date, worker_profiles or {}, day_shifts or {})
    requests = [_request(r) for r in req.itertuples()]
    cards = [dict(c) for c in (extra_requests or []) if c.get("service_date") == service_date]
    if cards:
        from intake.to_request import merge_card_requests
        requests, _ = merge_card_requests(requests, cards)
    if exclude_ids:
        ex = set(exclude_ids)
        requests = [r for r in requests if r["request_id"] not in ex]
    if travel is None:
        node_ids = (
            {w["base_node"] for w in workers}
            | {r["home_node"] for r in requests}
            | {r["hospital_node"] for r in requests if r.get("hospital_node")}
        )
        travel = load_travel(d, node_ids)
    for r in cards:
        # 카드 병원동행의 service_minutes 는 비워 두었다. 엔진·검증기는 이동+체류로 다시 계산하지만
        # 계약상 값이 있어야 하므로 같은 식(이동+체류+이동)으로 채운다. 경로가 없으면 체류만 넣고
        # 검증기가 '경로 없음'으로 막게 둔다(0분으로 숨기지 않음).
        if r["service_type"] == "hospital_escort" and r.get("service_minutes") is None:
            to_h = travel.get((r["home_node"], r["hospital_node"]))
            back = travel.get((r["hospital_node"], r["home_node"]))
            r["service_minutes"] = int(r["hospital_stay_min"]) + (
                int(round(to_h)) + int(round(back)) if to_h is not None and back is not None else 0)
    if align_pickup is None:
        align_pickup = escort_mode() == "transit" and align_pickup_default()
    if align_pickup:
        align_escort_pickup(requests, travel, skip_ids={c["request_id"] for c in cards})
    share = worker_transit_share() if transit_share is None else float(transit_share)
    worker_travel = None
    if share > 0:
        workers = assign_worker_modes(workers, share)
        if worker_transit is None:
            worker_transit = load_worker_transit(d, {w["base_node"] for w in workers} | {r["home_node"] for r in requests})
        worker_travel = {"transit": worker_transit}
    return DayInstance(service_date=service_date, workers=workers, requests=requests, travel=travel,
                       worker_travel=worker_travel)


# ── 인력별 이동수단 (docs/travel_modes_plan.md 3-2, 5단계) ───────────────────

def _worker_travel_cfg() -> dict:
    import yaml
    p = Path(__file__).resolve().parent.parent / "config" / "region.yaml"
    if not p.exists():
        return {}
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("worker_travel") or {}


def worker_transit_share() -> float:
    """config worker_travel.transit_share(0~1). 실제 인력은 대중교통 기준(사용자 확인 2026-11-18) → 1. 설정이 없으면 0."""
    v = float(_worker_travel_cfg().get("transit_share", 0.0) or 0.0)
    if not 0.0 <= v <= 1.0:
        raise ValueError(f"worker_travel.transit_share 는 0~1 이어야 합니다: {v}")
    return v


def assign_worker_modes(workers: list[dict], share: float) -> list[dict]:
    """같은 자격 묶음(동행 가능 / 가사만)마다 같은 비율로 대중교통 인력을 고른다.

    고르는 순서는 인력 ID 해시로 고정한다(실행마다 같은 인력). 실제 수단 정보가 생기면 그 값으로 바꾼다.
    """
    import hashlib
    groups: dict[tuple, list[dict]] = {}
    for w in workers:
        groups.setdefault(tuple(sorted(w.get("skills") or [])), []).append(w)
    transit: set[str] = set()
    for ws in groups.values():
        order = sorted(ws, key=lambda w: hashlib.sha1(w["worker_id"].encode()).hexdigest())
        transit |= {w["worker_id"] for w in order[:int(round(share * len(ws)))]}
    return [dict(w, travel_mode="transit" if w["worker_id"] in transit else "car") for w in workers]


def load_worker_transit(data_dir: str | Path, node_ids: set[str] | None = None) -> dict[tuple[str, str], float]:
    """대중교통 인력의 이동 표(인력 출발지·어르신 집 사이) — 보정 모형 값(source=estimate).

    모형(data/processed/worker_transit_model.json)이 없으면 멈춘다. 자동차 시간으로 대신하지 않는다.
    자동차 시간이 없는 구간은 표에 넣지 않는다(엔진은 '이동시간 없음'으로 다룬다).
    """
    from adapters.worker_transit import load_model, predict, worker_nodes
    d = Path(data_dir)
    model = load_model(d)
    if model is None:
        raise FileNotFoundError("대중교통 인력 이동 모형이 없습니다. python -m adapters.fetch worker-transit 을 먼저 실행하세요.")
    nodes = worker_nodes(d)
    if node_ids is not None:
        nodes = nodes[nodes["node_id"].isin(node_ids)]
    pos = nodes.set_index("node_id")[["lat", "lon"]]
    car = _load_matrix(d, set(pos.index))
    if not car:
        return {}
    df = pd.DataFrame([(a, b, m) for (a, b), m in car.items()], columns=["a", "b", "car"])
    df = df[df["a"].isin(pos.index) & df["b"].isin(pos.index) & (df["a"] != df["b"])]
    la1, lo1 = np.radians(pos.loc[df["a"], "lat"].to_numpy()), np.radians(pos.loc[df["a"], "lon"].to_numpy())
    la2, lo2 = np.radians(pos.loc[df["b"], "lat"].to_numpy()), np.radians(pos.loc[df["b"], "lon"].to_numpy())
    h = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    hav = 6371.0 * 2 * np.arcsin(np.sqrt(h))
    mins = predict(df["car"].to_numpy(), hav, model)
    ok = np.isfinite(mins)
    return {(a, b): round(float(m), 1) for a, b, m in zip(df["a"][ok], df["b"][ok], mins[ok])}


def apply_worker_settings(workers: list[dict], service_date: str, profiles: dict, shifts: dict) -> list[dict]:
    """담당자가 고친 인력 설정을 그날 입력에 적용한다(PRD W04).

    - profiles[wid]: {skills: [{code, valid_from?, valid_to?}], shift_start_min, shift_end_min, break_start_min,
      break_minutes, max_work_minutes, active}. 활동 중지면 그날 인력에서 뺀다.
      자격은 유효기간 안인 것만 쓴다(만료되면 후보 제외). 적힌 자격만 쓴다 — 동행 가능을 가사 가능으로 넓히지 않는다.
    - shifts[wid]: 그날 근무 {off} 또는 {start_min, end_min, break_start_min, break_minutes}. 휴무면 뺀다.
    - 출발지(base_node)는 바꾸지 않는다(이동시간 행렬이 그 위치로 계산되어 있다).
    """
    out = []
    for w in workers:
        w = dict(w)
        wid = w["worker_id"]
        prof = profiles.get(wid)
        if prof is not None:
            if not prof.get("active", True):
                continue
            w["skills"] = [sk["code"] for sk in prof.get("skills", [])
                           if (not sk.get("valid_from") or sk["valid_from"] <= service_date)
                           and (not sk.get("valid_to") or service_date <= sk["valid_to"])]
            for k in ("shift_start_min", "shift_end_min", "break_start_min", "break_minutes", "max_work_minutes"):
                if prof.get(k) is not None:
                    w[k] = int(prof[k])
        sh = shifts.get(wid)
        if sh is not None:
            if sh.get("off"):
                continue
            w["shift_start_min"], w["shift_end_min"] = int(sh["start_min"]), int(sh["end_min"])
            w["break_start_min"], w["break_minutes"] = int(sh["break_start_min"]), int(sh["break_minutes"])
        out.append(w)
    return out


def _workers(data_dir: Path) -> list[dict]:
    df = pd.read_csv(data_dir / "workers.csv", dtype={"worker_id": str})
    out = []
    for r in df.itertuples():
        skills = [s.strip() for s in str(r.skills).split(",") if s.strip()]
        out.append({
            "worker_id": r.worker_id,
            "skills": skills,
            "base_node": f"{WORKER_PREFIX}{r.worker_id}",
            "shift_start_min": int(r.shift_start_min),
            "shift_end_min": int(r.shift_end_min),
            "break_start_min": int(r.break_start_min),
            "break_minutes": int(r.break_minutes),
            "max_work_minutes": int(r.max_work_minutes),
        })
    return out


def _request(r) -> dict:
    """f0 요청 행 → 계약 이름. 병원동행이면 동행 필드를 함께 넣는다."""
    service_type = str(r.service)
    is_escort = service_type == "hospital_escort"

    item = {
        "request_id": r.request_id,
        "beneficiary_id": r.beneficiary_id,
        "service_type": service_type,
        "service_date": str(r.service_date),
        "home_node": f"{BENEFICIARY_PREFIX}{r.beneficiary_id}",
        "earliest_start_min": int(r.time_window_start_min),
        "latest_start_min": int(r.time_window_end_min),
        "service_minutes": int(r.service_duration_min),
        "priority": int(r.priority),
        "priority_reason": _priority_reason(r),
        "requested_at": str(r.requested_at),
        "required_skills": [service_type],
        # 취소 정보 — 재배정(reschedule)이 '언제 통보되는지'를 알아야 한다.
        # 배정 단계에서는 쓰지 않는다(미래 정보 금지). 이벤트 재생에서만 쓴다.
        "cancelled_same_day": bool(getattr(r, "cancelled_same_day", False)),
        "cancel_notice_min_before": (
            None if pd.isna(getattr(r, "cancel_notice_min_before", None))
            else int(r.cancel_notice_min_before)
        ),
        # 지연 정보 — 취소와 같은 이유로 배정 단계에서는 쓰지 않고 이벤트 재생에서만 쓴다.
        # 지연은 '시작이 늦어짐'이므로 서비스 길이는 그대로다.
        "delayed_same_day": bool(getattr(r, "delayed_same_day", False)),
        "delay_minutes": (
            None if pd.isna(getattr(r, "delay_minutes", None))
            else int(r.delay_minutes)
        ),
        "delay_notice_min_before": (
            None if pd.isna(getattr(r, "delay_notice_min_before", None))
            else int(r.delay_notice_min_before)
        ),
    }
    if is_escort:
        item.update(
            hospital_node=f"{HOSPITAL_PREFIX}{r.hospital_id}",
            appointment_min=int(r.appointment_min),
            # f0 는 픽업창을 '예약 − 추정이동 − 10분'으로 만들었다(generate.py).
            # 그 10분을 도착 여유로 기록한다. 값을 새로 지어내지 않고 데이터에서 되돌려 계산한다.
            arrival_buffer_min=_implied_buffer(r),
            hospital_stay_min=int(r.hospital_stay_min),
        )
    return item


def _implied_buffer(r) -> int:
    """f0 픽업창이 함축한 예약 도착 여유(분). 음수면 0으로 두지 않고 그대로 검증에 맡긴다."""
    if pd.isna(getattr(r, "appointment_min", None)):
        return 0
    travel = getattr(r, "est_travel_min", None)
    if travel is None or pd.isna(travel):
        return 0
    return max(0, int(r.appointment_min) - int(r.time_window_end_min) - int(round(float(travel))))


def _priority_reason(r) -> str | None:
    """f0 는 priority_reason 을 만들지 않는다. 데이터에 있는 사실만 문장으로 옮긴다."""
    if bool(getattr(r, "discharge_case", False)):
        days = getattr(r, "days_since_discharge", None)
        if days is not None and not pd.isna(days):
            return f"퇴원 {int(days)}일 경과"
        return "퇴원 사례"
    return "정기 서비스"


def service_dates(data_dir: str | Path) -> list[str]:
    df = pd.read_csv(Path(data_dir) / "requests.csv", usecols=["service_date"])
    return sorted(df["service_date"].unique().tolist())
