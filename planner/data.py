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

    def as_instance(self) -> dict:
        return {"workers": self.workers, "requests": self.requests, "travel": self.travel}


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


def load_travel(data_dir: str | Path, node_ids: set[str] | None = None) -> dict[tuple[str, str], float]:
    """travel_matrix.csv → {(from, to): 분}.

    node_ids 를 주면 그 노드들 사이만 남긴다(하루치 화면에 필요한 구간만 쓰기 위함).
    파일이 없으면 nodes.csv 좌표로 추정 행렬을 만든다.
    (61MB 행렬을 저장소에 넣지 않기 위한 대체 경로. 추정값이므로 화면에 출처를 표시한다.)
    """
    path = matrix_path(data_dir)
    if path is None:
        return estimate_travel(data_dir, node_ids)
    df = pd.read_csv(path, usecols=["from_node", "to_node", "minutes"])
    if node_ids is not None:
        df = df[df["from_node"].isin(node_ids) & df["to_node"].isin(node_ids)]
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
             travel: dict[tuple[str, str], float] | None = None) -> DayInstance:
    """하루치 instance 를 만든다.

    travel 을 주지 않으면 그날 필요한 노드만으로 추정 행렬을 만든다.
    전체 721개 노드를 쓰면 519,120구간이지만, 하루치면 70개 안팎(4,900구간)이라
    화면이 훨씬 빠르게 뜬다.
    """
    d = Path(data_dir)
    req = pd.read_csv(d / "requests.csv", dtype={"request_id": str, "beneficiary_id": str})
    req = req[req["service_date"] == service_date]
    workers = _workers(d)
    requests = [_request(r) for r in req.itertuples()]
    if travel is None:
        node_ids = (
            {w["base_node"] for w in workers}
            | {r["home_node"] for r in requests}
            | {r["hospital_node"] for r in requests if r.get("hospital_node")}
        )
        travel = load_travel(d, node_ids)
    return DayInstance(service_date=service_date, workers=workers, requests=requests, travel=travel)


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
