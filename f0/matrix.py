"""이동시간 행렬 (F0 → F3 입력).

노드: 협력기관, 인력 출발지, 어르신 집, 병원
OSRM 서버가 있으면 table 서비스로 실제 도로 소요시간을 계산하고,
없으면 직선거리 기반 대략값으로 대신한다(source 열로 구분).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import requests

from .geo import estimated_drive_minutes


class OsrmClient:
    def __init__(self, base_url: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def healthy(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/nearest/v1/driving/127.075,37.2", timeout=5)
            return r.ok and r.json().get("code") == "Ok"
        except requests.RequestException:
            return False

    def table_minutes(self, coords: list[tuple[float, float]], chunk: int = 100) -> np.ndarray:
        """coords [(lat, lon)] → n×n 분 단위 행렬. chunk씩 나눠 출발·도착 블록을 채운다."""
        n = len(coords)
        out = np.full((n, n), np.nan)
        for i0 in range(0, n, chunk):
            for j0 in range(0, n, chunk):
                src = list(range(i0, min(i0 + chunk, n)))
                dst = list(range(j0, min(j0 + chunk, n)))
                idx = sorted(set(src) | set(dst))
                pos = {g: p for p, g in enumerate(idx)}
                pts = ";".join(f"{coords[g][1]},{coords[g][0]}" for g in idx)
                params = {"sources": ";".join(str(pos[g]) for g in src),
                          "destinations": ";".join(str(pos[g]) for g in dst),
                          "annotations": "duration"}
                r = requests.get(f"{self.base_url}/table/v1/driving/{pts}", params=params, timeout=self.timeout)
                r.raise_for_status()
                data = r.json()
                if data.get("code") != "Ok":
                    raise RuntimeError(f"OSRM 오류: {data.get('code')} {data.get('message')}")
                block = np.array(data["durations"], dtype=float) / 60.0
                out[np.ix_(src, dst)] = block
        return np.round(out, 1)


def build_nodes(region: dict, world) -> pd.DataFrame:
    a = region["agency"]
    nodes = [{"node_id": "AGENCY", "kind": "agency", "lat": a["lat"], "lon": a["lon"]}]
    nodes += [{"node_id": f"WORKER:{w.worker_id}", "kind": "worker_home", "lat": w.home_lat, "lon": w.home_lon}
              for w in world.workers.itertuples()]
    nodes += [{"node_id": f"BENEFICIARY:{b.beneficiary_id}", "kind": "beneficiary", "lat": b.lat, "lon": b.lon}
              for b in world.beneficiaries.itertuples()]
    nodes += [{"node_id": f"HOSP:{h.hospital_id}", "kind": "hospital", "lat": h.lat, "lon": h.lon}
              for h in world.hospitals.itertuples()]
    return pd.DataFrame(nodes)


def estimated_matrix(coords: list[tuple[float, float]]) -> np.ndarray:
    n = len(coords)
    m = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            if i != j:
                m[i, j] = estimated_drive_minutes(coords[i][0], coords[i][1], coords[j][0], coords[j][1])
    return m


def build_matrix(nodes: pd.DataFrame, osrm_url: str | None,
                 osrm_time_factor: float = 1.0) -> tuple[pd.DataFrame, str]:
    """→ (긴 형식 행렬 표, 출처). 출처는 'osrm' 또는 'estimate'.

    osrm_time_factor: OSRM은 막히지 않는 도로 기준이라, 실측 샘플과 비교한 보정 배수를 곱한다.
    """
    coords = list(zip(nodes["lat"], nodes["lon"]))
    source = "estimate"
    mat = None
    if osrm_url:
        client = OsrmClient(osrm_url)
        if client.healthy():
            mat = np.round(client.table_minutes(coords) * osrm_time_factor, 1)
            source = "osrm"
    if mat is None:
        mat = estimated_matrix(coords)
    ids = nodes["node_id"].to_numpy()
    ii, jj = np.meshgrid(np.arange(len(ids)), np.arange(len(ids)), indexing="ij")
    long = pd.DataFrame({"from_node": ids[ii.ravel()], "to_node": ids[jj.ravel()],
                         "minutes": mat.ravel()})
    long = long[long["from_node"] != long["to_node"]].reset_index(drop=True)
    long["source"] = source
    return long, source
