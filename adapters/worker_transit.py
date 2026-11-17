"""대중교통으로 다니는 인력의 '다음 집까지' 이동시간 — 보정 모형 (docs/travel_modes_plan.md 3-2, 5단계).

인력 이동 행렬(인력 출발지·어르신 집 사이, 약 10만 구간)을 모두 ODsay로 조회하지 않는다.
  1. 표본: 자동차 시간 구간별로 고르게 n쌍(기본 400)을 골라 ODsay로 한 번 조회해 저장한다(이어받기).
     700m 이내(ODsay -98)는 걷는다. 걷는 거리는 OSRM 도보 서버로 잰다.
  2. 모형: 대중교통 ≈ a × 자동차 시간 + b,  걷기 ≈ 직선거리 × 우회계수 ÷ 보행 속도.
     인력은 둘 중 짧은 쪽으로 간다고 본다. 여기에 여유(margin)를 더한다 — 학습 표본 오차의 분위수
     (config worker_travel.model_quantile, 기본 0.8). 평균 모형을 그대로 쓰면 엔진이 모형이 짧게 잡은 구간을
     골라 실제로는 늦는 배정안이 나온다(11/17 재조회에서 확인). a·b·우회계수·여유는 표본의 70%로 맞추고 30%로 오차를 잰다.
  3. 엔진은 이 모형 값(source=estimate)으로 계산한다. 확정 후보안의 실제 연결 구간만 다시 조회한다(requery).

모든 값은 '대중교통 · 시간 기준 미지정'이다(ODsay에 출발 시각 요청변수가 없음). 보행 속도는 가정값이다.

    python -m adapters.fetch worker-transit --max 400   # 표본 조회(이어받기)
    python -m adapters.fetch worker-transit --fit       # 호출 없이 모형만 다시 맞추기
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

RAW_NAME = "worker_transit_pairs.csv"       # 표본·확정안 재조회 결과(모든 시도의 마지막 결과)
MODEL_NAME = "worker_transit_model.json"
BINS = [0, 5, 10, 15, 20, 1e9]              # 자동차 분 구간(표본을 고르게 뽑기 위함)
TEST_SHARE = 0.3


def _hav_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def _is_test(from_id: str, to_id: str) -> bool:
    """구간 이름으로 정하는 고정 분할(다시 맞춰도 같은 구간이 검사용)."""
    h = int(hashlib.sha1(f"{from_id}|{to_id}".encode()).hexdigest()[:8], 16)
    return (h % 1000) < TEST_SHARE * 1000


def worker_nodes(proc_dir: str | Path) -> pd.DataFrame:
    """인력 출발지와 어르신 집(병원·기관 제외). node_id, kind, lat, lon."""
    n = pd.read_csv(Path(proc_dir) / "nodes.csv")
    return n[n["kind"].isin(["worker_home", "beneficiary"])].reset_index(drop=True)


def sample_pairs(proc_dir: str | Path, travel: dict, n: int = 400, seed: int = 42) -> list[dict]:
    """자동차 시간 구간별로 고르게 n쌍. 같은 입력·시드면 같은 표본."""
    nodes = worker_nodes(proc_dir)
    pos = {r.node_id: (float(r.lat), float(r.lon)) for r in nodes.itertuples()}
    ids = list(pos)
    rows = [(a, b, travel[(a, b)]) for a in ids for b in ids if a != b and (a, b) in travel]
    df = pd.DataFrame(rows, columns=["from_id", "to_id", "car_min"])
    df["bin"] = pd.cut(df["car_min"], BINS, right=False, labels=False)
    rng = np.random.default_rng(seed)
    per = int(math.ceil(n / (len(BINS) - 1)))
    picks = []
    for _, g in df.groupby("bin"):
        k = min(per, len(g))
        picks.append(g.iloc[rng.choice(len(g), size=k, replace=False)])
    out = pd.concat(picks).head(n)
    return [{"from_id": r.from_id, "from": pos[r.from_id], "to_id": r.to_id, "to": pos[r.to_id],
             "request_id": "SAMPLE"} for r in out.itertuples()]


def foot_km(frm: tuple[float, float], to: tuple[float, float], foot_url: str) -> float | None:
    """OSRM 도보 경로 거리(km). 실패하면 None(지어내지 않음)."""
    import requests
    try:
        r = requests.get(f"{foot_url.rstrip('/')}/route/v1/foot/{frm[1]},{frm[0]};{to[1]},{to[0]}",
                         params={"overview": "false"}, timeout=10)
        j = r.json()
        if j.get("code") == "Ok" and j.get("routes"):
            return float(j["routes"][0]["distance"]) / 1000
    except Exception:       # noqa: BLE001 — 도보 서버 장애는 값 없음으로 둔다
        return None
    return None


def observations(raw: pd.DataFrame, travel: dict, walk_kmh: float, foot_url: str | None) -> pd.DataFrame:
    """조회 결과 → 관측표. truth = min(대중교통, 도보), 700m 이내는 도보. 경로 없음·실패는 뺀다(사유는 남김)."""
    rows = []
    for r in raw.itertuples():
        frm, to = (float(r.from_lat), float(r.from_lon)), (float(r.to_lat), float(r.to_lon))
        car = travel.get((r.from_id, r.to_id))
        hav = _hav_km(frm, to)
        fk = foot_km(frm, to, foot_url) if foot_url else None
        walk = fk / walk_kmh * 60 if fk is not None else None
        odsay = (float(r.duration_seconds) / 60
                 if r.pair_status == "ok" and pd.notna(r.duration_seconds) and r.duration_seconds > 0 else None)
        if r.pair_status == "ok" and odsay is not None:
            truth = min(odsay, walk) if walk is not None else odsay
        elif r.pair_status == "too_close" and walk is not None:
            truth = walk
        else:
            truth = None
        rows.append({"from_id": r.from_id, "to_id": r.to_id, "pair_status": r.pair_status, "car_min": car,
                     "hav_km": hav, "foot_km": fk, "odsay_min": odsay, "walk_min": walk, "truth_min": truth,
                     "test": _is_test(r.from_id, r.to_id)})
    return pd.DataFrame(rows)


def predict(car_min, hav_km, model: dict):
    """모형 값(분). 배열도 받는다. 자동차 시간이 없으면 NaN(0분으로 바꾸지 않음)."""
    car = np.asarray(car_min, dtype=float)
    hav = np.asarray(hav_km, dtype=float)
    transit = model["a"] * car + model["b"]
    walk = hav * model["detour"] / model["walk_kmh"] * 60
    margin = float(model.get("margin", 0.0))
    return (np.minimum(transit, walk) + margin) if np.ndim(car) else float(np.minimum(transit, walk) + margin)


def fit(obs: pd.DataFrame, walk_kmh: float, raw_file: str, quantile: float = 0.8) -> dict:
    """표본 70%로 a·b·우회계수·여유를 맞추고 30%로 오차를 잰다. quantile: 여유로 쓸 학습 오차 분위수(0.5 ≈ 여유 없음)."""
    ok = obs[obs["odsay_min"].notna() & obs["car_min"].notna()]
    tr = ok[~ok["test"]]
    if len(tr) < 20:
        raise ValueError(f"대중교통 표본이 너무 적습니다(학습용 {len(tr)}쌍). 표본을 더 조회하세요.")
    a, b = np.polyfit(tr["car_min"].to_numpy(), tr["odsay_min"].to_numpy(), 1)
    fw = obs[obs["foot_km"].notna() & (obs["hav_km"] > 0.05) & ~obs["test"]]
    if len(fw) < 10:
        raise ValueError("도보 거리 표본이 부족합니다(OSRM 도보 서버 확인).")
    detour = float((fw["foot_km"] / fw["hav_km"]).median())
    model = {"a": round(float(a), 4), "b": round(float(b), 3), "detour": round(detour, 4), "walk_kmh": walk_kmh}
    trt = obs[obs["truth_min"].notna() & obs["car_min"].notna() & ~obs["test"]]
    res = trt["truth_min"].to_numpy() - predict(trt["car_min"], trt["hav_km"], model)
    model["margin"] = round(float(np.quantile(res, quantile)), 2)
    model["margin_quantile"] = quantile
    te = obs[obs["truth_min"].notna() & obs["car_min"].notna() & obs["test"]]
    err = predict(te["car_min"], te["hav_km"], model) - te["truth_min"].to_numpy()
    ae = np.abs(err)
    model.update({
        "source": "estimate", "time_basis": "provider_unspecified",
        "formula": "min(a × 자동차분 + b, 직선km × detour ÷ walk_kmh × 60) + margin",
        "n_pairs": int(len(obs)), "status_counts": obs["pair_status"].value_counts().to_dict(),
        "n_train": int(len(tr)), "n_test": int(len(te)),
        "test_mae_min": round(float(ae.mean()), 2) if len(te) else None,
        "test_median_abs_min": round(float(np.median(ae)), 2) if len(te) else None,
        "test_p90_abs_min": round(float(np.quantile(ae, 0.9)), 2) if len(te) else None,
        "test_bias_min": round(float(err.mean()), 2) if len(te) else None,
        "test_within_10min": round(float((ae <= 10).mean()), 3) if len(te) else None,
        "test_under_share": round(float((err < 0).mean()), 3) if len(te) else None,
        "test_under_10min_share": round(float((err < -10).mean()), 3) if len(te) else None,
        "raw_file": raw_file,
        "fitted_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec="seconds"),
        "note": "보행 속도는 가정값. 대중교통 시간은 출발 시각 기준 없음(배차 시간대 미반영).",
    })
    return model


def save_model(model: dict, proc_dir: str | Path) -> Path:
    p = Path(proc_dir) / MODEL_NAME
    p.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_model(proc_dir: str | Path) -> dict | None:
    p = Path(proc_dir) / MODEL_NAME
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ── 확정 후보안 재조회 (3-2 의 3번) ─────────────────────────────────────────
# 엔진은 모형 값으로 계산한다. 담당자에게 보여 줄 후보안이 정해지면, 대중교통 인력이 실제로 지나는 연결 구간만
# ODsay 로 다시 조회하고, 그 값으로 검증기를 다시 돌린다. 늦어지는 구간이 생기면 배정안에 표시하고 다시 계산한다.
# 아래 세 함수는 호출하지 않는다(시험 가능). 실제 조회는 adapters.fetch worker-plan 이 한다.

def plan_legs(instance: dict, assignments: list[dict]) -> list[dict]:
    """대중교통 인력의 연결 구간(직전 위치 → 다음 집)을 배정 순서대로. 같은 노드면 건너뛴다."""
    workers = {w["worker_id"]: w for w in instance["workers"]}
    reqs = {r["request_id"]: r for r in instance["requests"]}
    table = (instance.get("worker_travel") or {}).get("transit") or {}
    if not isinstance(table, dict):
        table = {(r["from_node"], r["to_node"]): float(r["minutes"]) for r in table}
    out = []
    for wid in sorted({a["worker_id"] for a in assignments}):
        w = workers[wid]
        if (w.get("travel_mode") or "car") != "transit":
            continue
        prev = w["base_node"]
        for a in sorted((a for a in assignments if a["worker_id"] == wid), key=lambda a: int(a["arrive_min"])):
            home = reqs[a["request_id"]]["home_node"]
            if home != prev:
                out.append({"worker_id": wid, "request_id": a["request_id"], "from_id": prev, "to_id": home,
                            "model_min": table.get((prev, home))})
            prev = home
    return out


def actual_minutes(raw: pd.DataFrame, legs: list[dict], walk_kmh: float, walk_km: dict) -> list[dict]:
    """조회 결과로 구간별 실제 값. ok → min(대중교통, 도보), 700m 이내 → 도보.
    조회 실패·경로 없음은 실제 값을 비워 두고 '확인 실패'로 표시한다(모형 값을 실제처럼 쓰지 않음).

    walk_km: {(from, to): OSRM 도보 km} — 모르면 비워 둔다.
    """
    last = {}
    if not raw.empty:
        for r in raw.itertuples():
            last[(r.from_id, r.to_id)] = r
    out = []
    for leg in legs:
        r = last.get((leg["from_id"], leg["to_id"]))
        km = walk_km.get((leg["from_id"], leg["to_id"]))
        walk = km / walk_kmh * 60 if km is not None else None
        actual, how = None, "조회 안 함"
        if r is not None:
            if r.pair_status == "ok" and pd.notna(r.duration_seconds) and r.duration_seconds > 0:
                odsay = float(r.duration_seconds) / 60
                actual, how = (min(odsay, walk), "대중교통·도보 중 짧은 쪽") if walk is not None else (odsay, "대중교통")
            elif r.pair_status == "too_close" and walk is not None:
                actual, how = walk, "도보(700m 이내)"
            else:
                how = f"확인 실패({r.pair_status})"
        out.append(dict(leg, actual_min=None if actual is None else round(actual, 1), how=how))
    return out


def recheck(instance: dict, assignments: list[dict], actuals: list[dict]) -> dict:
    """실제 값으로 바꾼 입력에서 검증기를 다시 돌린다. 확인 실패 구간은 모형 값 그대로 두고 따로 센다."""
    import copy

    from verifier import verify
    inst = copy.copy(instance)
    table = (instance.get("worker_travel") or {}).get("transit") or {}
    if not isinstance(table, dict):
        table = {(r["from_node"], r["to_node"]): float(r["minutes"]) for r in table}
    table = dict(table)
    for a in actuals:
        if a["actual_min"] is not None:
            table[(a["from_id"], a["to_id"])] = float(a["actual_min"])
    inst["worker_travel"] = dict(instance.get("worker_travel") or {}, transit=table)
    rep = verify(inst, assignments)
    late = sorted({v.request_id for v in rep.violations if v.request_id})
    checked = [a for a in actuals if a["actual_min"] is not None]
    diffs = [a["actual_min"] - a["model_min"] for a in checked if a["model_min"] is not None]
    return {"instance": inst, "ok": rep.ok, "violations": [v.as_dict() for v in rep.violations],
            "late_requests": late, "n_legs": len(actuals), "n_checked": len(checked),
            "n_unchecked": len(actuals) - len(checked),
            "mean_diff_min": round(float(np.mean(diffs)), 1) if diffs else None,
            "max_diff_min": round(float(np.max(diffs)), 1) if diffs else None}


def repair(instance: dict, assignments: list[dict], late_requests: list[str]) -> dict:
    """실제 값으로 바꾼 입력(recheck 의 instance)에서, 늦어지는 요청이 있는 인력의 일정만 같은 순서로 다시 맞춘다.

    순서를 바꾸지 않으므로 새 연결 구간이 생기지 않는다(다시 조회할 것 없음). 시각만 늦추고,
    시간창 안에 못 들어가는 일은 빼서 '밀려난 요청'으로 돌려준다(담당자 확인용). 다른 인력 일정은 그대로다.
    """
    from planner.baseline import _travel_map
    from planner.reschedule import _retime
    from planner.travel_modes import worker_travels
    from verifier import verify
    travel = _travel_map(instance["travel"])
    wt_ = worker_travels(instance, travel)
    by_id = {r["request_id"]: r for r in instance["requests"]}
    workers = {w["worker_id"]: w for w in instance["workers"]}
    owner = {a["request_id"]: a["worker_id"] for a in assignments}
    touched = sorted({owner[r] for r in late_requests if r in owner})
    out = [dict(a) for a in assignments if a["worker_id"] not in touched]
    retimed, displaced = [], []
    for wid in touched:
        jobs = [a for a in assignments if a["worker_id"] == wid]
        kept, rt, dp = _retime(workers[wid], jobs, wt_[wid], by_id)
        out += [{"request_id": k["request_id"], "worker_id": wid, "arrive_min": k["arrive_min"]} for k in kept]
        retimed += rt
        displaced += dp
    rep = verify(instance, out)
    return {"assignments": out, "ok": rep.ok, "retimed": retimed, "displaced": displaced, "workers": touched}
