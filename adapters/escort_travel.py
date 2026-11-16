"""병원동행 구간(어르신 집 ↔ 병원)의 이동시간 — 동행 수단별.

병원동행의 기본 수단은 대중교통이다. 자가용이 있으면 대개 가족이 동행하고, 기관 차량은 수가 아주 적다
(docs/travel_modes_plan.md). 그래서 동행 점유시간의 '이동' 부분은 자동차 행렬이 아니라 이 표를 쓴다.

한 구간의 값은 아래 순서로 정한다. 어느 것도 0분으로 바꾸지 않는다.
  1. ODsay 대중교통 조회 성공(ok)                 → 대중교통 시간        source=odsay
  2. ODsay '출·도착지 700m 이내'(코드 -98)       → 걸어서 가는 시간      source=walk_osrm / walk_estimate
     (어르신 보행 속도는 config/region.yaml escort_travel.walk_kmh — 가정값이며 화면·보고서에 밝힌다)
  3. 조회 안 함·실패                             → 표에 넣지 않는다. 엔진은 자동차 행렬 값을 그대로 쓰고,
                                                   화면은 '자동차 시간 대체'로 센다(planner.data.escort_travel_status).

    python -m adapters.fetch escort --max 200    # 아직 받지 않은 구간만 이어서 받는다(하루 한도 대비)
    python -m adapters.fetch escort --build      # 받은 결과로 data/processed/escort_travel.csv 다시 만들기

ODsay 원본 응답 기록은 data/raw/transit_pairs.csv(모든 시도의 마지막 결과), 배정 입력은 escort_travel.csv.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

RAW_NAME = "transit_pairs.csv"
OUT_NAME = "escort_travel.csv"
TOO_CLOSE_CODE = -98             # ODsay: 출, 도착지가 700m이내입니다 (2026-11-15 실제 응답)
FINAL = {"ok", "no_route", "too_close"}      # 다시 조회해도 같은 결과 → 이어받기에서 건너뜀
STOP = {"missing_key", "auth_failed", "access_denied", "rate_limited"}   # 계속 불러도 소용없음 → 멈춤
OUT_COLUMNS = ["from_node", "to_node", "minutes", "mode", "source", "fetched_at"]


def escort_pairs(proc_dir: str | Path) -> list[dict]:
    """requests.csv 의 병원동행 (집, 병원) 쌍을 두 방향으로. 같은 쌍은 한 번만."""
    from planner.data import BENEFICIARY_PREFIX, HOSPITAL_PREFIX
    proc = Path(proc_dir)
    req = pd.read_csv(proc / "requests.csv")
    req = req[req["service"] == "hospital_escort"].sort_values("request_id")
    ben = pd.read_csv(proc / "beneficiaries.csv").set_index("beneficiary_id")
    hosp = pd.read_csv(proc / "hospitals.csv").set_index("hospital_id")
    out, seen = [], set()
    for r in req.itertuples():
        if r.hospital_id not in hosp.index or r.beneficiary_id not in ben.index:
            continue
        b, h = ben.loc[r.beneficiary_id], hosp.loc[r.hospital_id]
        home = (f"{BENEFICIARY_PREFIX}{r.beneficiary_id}", (float(b.lat), float(b.lon)))
        hp = (f"{HOSPITAL_PREFIX}{r.hospital_id}", (float(h.lat), float(h.lon)))
        for (fid, frm), (tid, to) in ((home, hp), (hp, home)):
            if (fid, tid) in seen:
                continue
            seen.add((fid, tid))
            out.append({"from_id": fid, "from": frm, "to_id": tid, "to": to, "request_id": r.request_id})
    return out


def classify(route: dict) -> str:
    """transit.estimate 결과 → 이어받기용 상태. -98(700m 이내)은 too_close 로 따로 둔다."""
    code = route.get("provider_result_code")
    try:
        if code is not None and not pd.isna(code) and int(code) == TOO_CLOSE_CODE:
            return "too_close"
    except (TypeError, ValueError):
        pass
    return route.get("status") or "unknown"


def load_raw(raw_dir: str | Path) -> pd.DataFrame:
    p = Path(raw_dir) / RAW_NAME
    return pd.read_csv(p) if p.exists() else pd.DataFrame()


def save_raw(df: pd.DataFrame, raw_dir: str | Path) -> Path:
    p = Path(raw_dir) / RAW_NAME
    df.to_csv(p, index=False, encoding="utf-8-sig")
    return p


def pending(pairs: list[dict], raw: pd.DataFrame) -> list[dict]:
    """아직 결론이 나지 않은 구간(조회 안 함 또는 일시 실패)."""
    if raw.empty:
        return list(pairs)
    done = {(a, b) for a, b, s in zip(raw["from_id"], raw["to_id"], raw["pair_status"]) if s in FINAL}
    return [p for p in pairs if (p["from_id"], p["to_id"]) not in done]


def _merge(raw_dir: str | Path, rows: list[dict]) -> None:
    """새 결과를 raw 에 합친다(같은 구간은 새 결과로 바꿈)."""
    if not rows:
        return
    raw, new = load_raw(raw_dir), pd.DataFrame(rows)
    if not raw.empty:
        keep = raw.set_index(["from_id", "to_id"]).index.difference(new.set_index(["from_id", "to_id"]).index)
        raw = raw.set_index(["from_id", "to_id"]).loc[keep].reset_index()
        new = pd.concat([raw, new], ignore_index=True)
    save_raw(new, raw_dir)


def fetch(client, pairs: list[dict], raw_dir: str | Path, max_calls: int, log=print,
          save_every: int = 50) -> dict:
    """pending 구간을 max_calls 건까지 조회해 raw 에 합친다. 한도·인증 오류면 바로 멈춘다.

    save_every 건마다 저장한다 — 중간에 멈춰도 받은 결과는 남고, 다음 실행이 이어받는다.
    """
    from adapters import transit
    todo = pending(pairs, load_raw(raw_dir))[:max_calls]
    batch, counts, called, stopped = [], {}, 0, None
    for p in todo:
        r = transit.estimate(client, p["from_id"], p["from"], p["to_id"], p["to"])
        r["request_id"] = p["request_id"]
        r["pair_status"] = classify(r)
        r["from_lat"], r["from_lon"] = p["from"]
        r["to_lat"], r["to_lon"] = p["to"]
        batch.append(r)
        called += 1
        counts[r["pair_status"]] = counts.get(r["pair_status"], 0) + 1
        if r["status"] in STOP:
            stopped = r["status"]
            log(f"  멈춤: {r['status']} — {str(r.get('message', ''))[:80]}")
            break
        if len(batch) >= save_every:
            _merge(raw_dir, batch)
            batch = []
            log(f"  {called}/{len(todo)}건 저장 {counts}")
    _merge(raw_dir, batch)
    return {"called": called, "counts": counts, "stopped": stopped,
            "remaining": len(pending(pairs, load_raw(raw_dir)))}


# ── 걷기 ─────────────────────────────────────────────────────────────────

def walk_minutes(frm: tuple[float, float], to: tuple[float, float], walk_kmh: float,
                 foot_url: str | None = None) -> tuple[float, str]:
    """어르신 보행 속도로 걷는 시간(분)과 출처. OSRM 도보 서버가 있으면 실제 보행로 거리, 없으면 직선×우회계수."""
    from f0.geo import haversine_km
    if foot_url:
        import requests
        try:
            r = requests.get(f"{foot_url.rstrip('/')}/route/v1/foot/{frm[1]},{frm[0]};{to[1]},{to[0]}",
                             params={"overview": "false"}, timeout=10)
            data = r.json()
            if r.ok and data.get("code") == "Ok" and data.get("routes"):
                km = float(data["routes"][0]["distance"]) / 1000
                if km > 0:
                    return round(km / walk_kmh * 60, 1), "walk_osrm"
        except (requests.RequestException, ValueError, KeyError):
            pass
    km = haversine_km(frm[0], frm[1], to[0], to[1]) * WALK_DETOUR
    return round(km / walk_kmh * 60, 1), "walk_estimate"


WALK_DETOUR = 1.3        # 직선 → 보행로 거리 대략 계수(OSRM 도보 서버가 없을 때만)


def build(raw_dir: str | Path, proc_dir: str | Path, walk_kmh: float, foot_url: str | None = None) -> pd.DataFrame:
    """raw 조회 결과 → escort_travel.csv. ok 는 대중교통, too_close 는 걷기. 나머지는 넣지 않는다."""
    raw = load_raw(raw_dir)
    rows = []
    for r in raw.itertuples():
        if r.pair_status == "ok" and r.duration_seconds and r.duration_seconds > 0:
            rows.append({"from_node": r.from_id, "to_node": r.to_id, "minutes": round(r.duration_seconds / 60, 1),
                         "mode": "transit", "source": "odsay", "fetched_at": r.fetched_at})
        elif r.pair_status == "too_close":
            mins, src = walk_minutes((r.from_lat, r.from_lon), (r.to_lat, r.to_lon), walk_kmh, foot_url)
            rows.append({"from_node": r.from_id, "to_node": r.to_id, "minutes": mins,
                         "mode": "walk", "source": src, "fetched_at": r.fetched_at})
    df = pd.DataFrame(rows, columns=OUT_COLUMNS)
    df.to_csv(Path(proc_dir) / OUT_NAME, index=False, encoding="utf-8-sig")
    return df
