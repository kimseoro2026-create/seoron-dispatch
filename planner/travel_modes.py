"""인력별 이동수단(travel_mode) — docs/travel_modes_plan.md 5단계.

입력 계약:
  worker["travel_mode"]            car(기본) / transit
  instance["worker_travel"][mode]  그 수단의 '인력 이동' 표 — 행 목록 또는 {(from, to): 분}

인력이 직전 위치에서 다음 집으로 가는 이동(leg)은 그 인력 수단의 표에서만 찾는다.
표에 없는 구간·표가 없는 수단은 자동차 시간으로 메우지 않고 '이동시간 없음'으로 둔다(검증기와 같은 규칙).
병원동행의 집↔병원은 요청(어르신)의 수단이므로 언제나 공용 표(instance["travel"])다.
"""
from __future__ import annotations

CAR = "car"
TRANSIT = "transit"
MODES = (CAR, TRANSIT)


def _as_map(table) -> dict[tuple[str, str], float]:
    if isinstance(table, dict):
        return {(a, b): float(m) for (a, b), m in table.items()}
    return {(r["from_node"], r["to_node"]): float(r["minutes"]) for r in table}


def worker_mode(worker: dict) -> str:
    return worker.get("travel_mode") or CAR


def leg_maps(instance: dict, shared: dict | None = None) -> dict[str, dict | None]:
    """worker_id → 인력 이동 표(자동차 인력은 None = 공용 표 그대로). shared 는 쓰지 않는다(호환용)."""
    tables = {m: _as_map(t) for m, t in (instance.get("worker_travel") or {}).items()}
    out: dict[str, dict | None] = {}
    for w in instance["workers"]:
        mode = worker_mode(w)
        if mode == CAR:
            out[w["worker_id"]] = None
        elif mode in tables:
            out[w["worker_id"]] = tables[mode]
        else:
            out[w["worker_id"]] = {}      # 표 없음 → 모든 이동 '없음'
    return out


class WorkerTravel:
    """한 인력용 이동 조회 보기. 병원이 끼는 구간(동행 집↔병원)은 공용 표, 나머지(인력 이동)는 인력 수단 표.

    인력 한 명을 다루는 보조 함수(_fits_at, _retime, _attempt_insert, _sequence 등)에 travel 대신 넘긴다.
    인력 수단 표에 없는 구간은 None — 자동차 시간으로 메우지 않는다.
    """

    __slots__ = ("shared", "own", "hospitals")

    def __init__(self, shared: dict, own: dict, hospitals: set[str]):
        self.shared, self.own, self.hospitals = shared, own, hospitals

    def _table(self, key):
        a, b = key
        if a in self.hospitals or b in self.hospitals:
            return self.shared
        return self.own

    def get(self, key, default=None):
        return self._table(key).get(key, default)

    def __getitem__(self, key):
        return self._table(key)[key]

    def __contains__(self, key) -> bool:
        return key in self._table(key)


def hospital_nodes(instance: dict) -> set[str]:
    return {r["hospital_node"] for r in instance["requests"] if r.get("hospital_node")}


def worker_travels(instance: dict, shared: dict) -> dict[str, object]:
    """worker_id → 그 인력이 쓸 이동 조회. 자동차 인력은 공용 표(shared) 그대로."""
    lm = leg_maps(instance)
    if all(v is None for v in lm.values()):
        return {wid: shared for wid in lm}
    hosp = hospital_nodes(instance)
    return {wid: (shared if own is None else WorkerTravel(shared, own, hosp)) for wid, own in lm.items()}
