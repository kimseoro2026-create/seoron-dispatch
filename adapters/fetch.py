"""외부 자료 받기(실제 호출). 화면은 저장된 결과만 읽는다.

    python -m adapters.fetch weather            # 기상청 단기예보 → data/raw/weather_forecast.csv
    python -m adapters.fetch transit --n 5      # 병원동행 n건의 집→병원 대중교통 → data/raw/transit_sample.csv
    python -m adapters.fetch escort --max 200   # 병원동행 전체 구간(두 방향) 대중교통, 이어받기 → transit_pairs.csv
    python -m adapters.fetch escort --build     # 받은 결과로 data/processed/escort_travel.csv 다시 만들기(호출 없음)
    python -m adapters.fetch worker-transit --max 400   # 인력 대중교통 보정 표본(이어받기) + 모형 맞추기
    python -m adapters.fetch worker-transit --fit       # 호출 없이 모형만 다시 맞추기
    python -m adapters.fetch worker-plan --date 2026-11-02 --algo optimize --share 1.0
                                                # 계산한 배정안의 대중교통 인력 연결 구간만 실제 조회 → 다시 검증

대중교통은 호출량 한도를 확인하지 못해 표본만 받는다(기본 5건). 실패한 건도 status 와 함께 저장한다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PROC, RAW = ROOT / "data" / "processed", ROOT / "data" / "raw"


def _client():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from f0.apiclient import PlatformClient
    return PlatformClient()


def transit_pairs(n: int) -> list[tuple[str, tuple, str, tuple, str]]:
    """병원동행 요청 앞에서부터 서로 다른 (집, 병원) n쌍. 노드 이름은 이동시간 행렬과 같게 쓴다."""
    from planner.data import BENEFICIARY_PREFIX, HOSPITAL_PREFIX
    req = pd.read_csv(PROC / "requests.csv")
    req = req[req["service"] == "hospital_escort"].sort_values("request_id")
    ben = pd.read_csv(PROC / "beneficiaries.csv").set_index("beneficiary_id")
    hosp = pd.read_csv(PROC / "hospitals.csv").set_index("hospital_id")
    out, seen = [], set()
    for r in req.itertuples():
        key = (r.beneficiary_id, r.hospital_id)
        if key in seen or r.hospital_id not in hosp.index:
            continue
        seen.add(key)
        b, h = ben.loc[r.beneficiary_id], hosp.loc[r.hospital_id]
        out.append((f"{BENEFICIARY_PREFIX}{r.beneficiary_id}", (float(b.lat), float(b.lon)),
                    f"{HOSPITAL_PREFIX}{r.hospital_id}", (float(h.lat), float(h.lon)), r.request_id))
        if len(out) >= n:
            break
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["weather", "transit", "escort", "worker-transit", "worker-plan"])
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--max", type=int, default=200, help="escort: 이번에 부를 최대 건수")
    ap.add_argument("--build", action="store_true", help="escort: 호출하지 않고 escort_travel.csv 만 다시 만든다")
    ap.add_argument("--fit", action="store_true", help="worker-transit: 호출하지 않고 모형만 다시 맞춘다")
    ap.add_argument("--sample", type=int, default=400, help="worker-transit: 표본 쌍 수")
    ap.add_argument("--date", default=None, help="worker-plan: 서비스일(기본 첫 날)")
    ap.add_argument("--algo", default="optimize", choices=["request_order", "nearest", "optimize"])
    ap.add_argument("--share", type=float, default=1.0, help="worker-plan: 대중교통 인력 비율")
    ap.add_argument("--rho", type=float, default=0.9)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args(argv)
    if a.what == "worker-plan":
        return _worker_plan(a)
    if a.what == "escort":
        return _escort(a)
    if a.what == "worker-transit":
        return _worker_transit(a)
    c = _client()
    if a.what == "weather":
        from adapters import weather
        lat, lon = weather.region_center(PROC)
        df = weather.fetch_forecast(c, lat, lon)        # 실패하면 예외로 멈춘다(빈 파일을 만들지 않음)
        p = weather.save_cache(df, RAW)
        print(f"예보 {len(df)}행 저장: {p.name} (발표 {df['base_date'].iloc[0]} {df['base_time'].iloc[0]}, "
              f"격자 {df['nx'].iloc[0]},{df['ny'].iloc[0]})")
        return 0
    from adapters import transit
    rows = []
    for fid, frm, tid, to, rid in transit_pairs(a.n):
        r = transit.estimate(c, fid, frm, tid, to)
        r["request_id"] = rid
        rows.append(r)
        mins = f"{r['duration_seconds'] / 60:.0f}분" if r["duration_seconds"] else "-"
        print(f"  {rid}: {r['status']} {mins} {r['message'][:80]}")
    p = transit.save_cache(rows, RAW)
    ok = sum(r["status"] == "ok" for r in rows)
    print(f"대중교통 {len(rows)}건 중 성공 {ok}건 저장: {p.name}")
    return 0 if ok else 1


def _escort(a) -> int:
    import os

    import yaml

    from adapters import escort_travel as et
    cfg = (yaml.safe_load((ROOT / "config" / "region.yaml").read_text(encoding="utf-8")) or {}).get("escort_travel") or {}
    pairs = et.escort_pairs(PROC)
    if not a.build:
        res = et.fetch(_client(), pairs, RAW, a.max)
        print(f"조회 {res['called']}건 {res['counts']} · 남은 구간 {res['remaining']}/{len(pairs)}"
              + (f" · 멈춤({res['stopped']})" if res["stopped"] else ""))
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    df = et.build(RAW, PROC, float(cfg.get("walk_kmh", 3.0)), os.environ.get("OSRM_FOOT_URL") or None)
    print(f"{et.OUT_NAME}: {len(df)}구간 {df['source'].value_counts().to_dict()} (전체 {len(pairs)}구간)")
    return 0


def _worker_transit(a) -> int:
    import os

    import yaml
    from dotenv import load_dotenv

    from adapters import escort_travel as et
    from adapters import worker_transit as wt
    from planner.data import _load_matrix
    load_dotenv(ROOT / ".env")
    cfg = (yaml.safe_load((ROOT / "config" / "region.yaml").read_text(encoding="utf-8")) or {}).get("worker_travel") or {}
    walk_kmh = float(cfg.get("walk_kmh", 4.0))
    ids = set(wt.worker_nodes(PROC)["node_id"])
    travel = _load_matrix(PROC, ids)
    pairs = wt.sample_pairs(PROC, travel, a.sample)
    if not a.fit:
        res = et.fetch(_client(), pairs, RAW, a.max, name=wt.RAW_NAME)
        print(f"조회 {res['called']}건 {res['counts']} · 남은 표본 {res['remaining']}/{len(pairs)}"
              + (f" · 멈춤({res['stopped']})" if res["stopped"] else ""))
    raw = et.load_raw(RAW, wt.RAW_NAME)
    if raw.empty:
        print("조회 결과가 없습니다.")
        return 1
    raw = raw[raw["request_id"] == "SAMPLE"]
    foot = os.environ.get("OSRM_FOOT_URL") or None
    if not foot:
        print("OSRM_FOOT_URL 이 없어 걷기 거리를 잴 수 없습니다(.env).")
        return 1
    obs = wt.observations(raw, travel, walk_kmh, foot)
    model = wt.fit(obs, walk_kmh, wt.RAW_NAME, float(cfg.get("model_quantile", 0.8)))
    p = wt.save_model(model, PROC)
    obs.to_csv(PROC / "worker_transit_observations.csv", index=False, encoding="utf-8-sig")
    print(f"{p.name}: a={model['a']} b={model['b']} 우회계수={model['detour']} 여유={model['margin']}분"
          f"(분위수 {model['margin_quantile']}) · 검사 {model['n_test']}쌍 평균 오차 {model['test_mae_min']}분, "
          f"짧게 잡은 비율 {model['test_under_share']}, 10분 넘게 짧게 {model['test_under_10min_share']}")
    return 0


def _worker_plan(a) -> int:
    """실험과 같은 방식으로 하루 배정안을 만들고(자동차 기준 ρ로 요청 선택, 동행은 대중교통),
    대중교통 인력의 연결 구간만 ODsay 로 조회해 다시 검증한다. 늦어지는 요청이 있으면 그 인력 일정만
    같은 순서로 시각을 다시 맞추고(새 구간 없음), 못 들어가는 요청은 빼서 보여 준다."""
    import os

    import yaml
    from dotenv import load_dotenv

    from adapters import worker_transit as wt
    from planner.baseline import nearest, request_order
    from planner.data import load_day, load_travel, service_dates
    from planner.optimize import optimize
    from simulator.experiment import select_requests
    from verifier import verify
    load_dotenv(ROOT / ".env")
    cfg = (yaml.safe_load((ROOT / "config" / "region.yaml").read_text(encoding="utf-8")) or {}).get("worker_travel") or {}
    walk_kmh = float(cfg.get("walk_kmh", 4.0))
    foot = os.environ.get("OSRM_FOOT_URL") or None
    date = a.date or service_dates(PROC)[0]
    inst = load_day(PROC, date, travel=load_travel(PROC), transit_share=a.share).as_instance()
    sel = load_day(PROC, date, travel=load_travel(PROC, escort="car"), transit_share=0).as_instance()
    ids = {r["request_id"] for r in select_requests(sel, a.rho, a.seed)["requests"]}
    inst = dict(inst, requests=[r for r in inst["requests"] if r["request_id"] in ids])

    def solve(x):
        return (optimize(x, time_limit_sec=10).assignments if a.algo == "optimize"
                else {"request_order": request_order, "nearest": nearest}[a.algo](x).assignments)

    plan = solve(inst)
    assert verify(inst, plan).ok, "모형 값 기준 배정안이 검증을 통과하지 못했습니다"
    query, load_raw, walk = wt.live_tools(RAW, PROC / "nodes.csv", foot, _client())

    def log(i):
        print(f"[{i['round']}회] {date} {a.algo} 대중교통 인력 {int(a.share * 100)}% · 배정 {i['n_assigned']}건 · "
              f"연결 구간 {i['n_legs']}개 (새로 조회 {i['called']}건, 확인 {i['n_checked']}, 확인 실패 {i['n_unchecked']})")
        print(f"  실제 − 모형: 평균 {i['mean_diff_min']}분, 최대 {i['max_diff_min']}분 · 실제 값으로 검증 "
              f"{'통과' if i['ok'] else '위반'} · 늦어지는 요청 {i['late_requests']}")

    res = wt.confirm_plan(inst, plan, query=query, load_raw=load_raw, walk_km=walk, walk_kmh=walk_kmh, log=log)
    print(f"  → {'확정 가능' if res['confirmed'] else '확인 미완료(담당자 확인 필요)'} · 배정 {len(res['plan'])}건"
          f"(처음 {len(res['first_plan'])}건) · 시각 바뀜 {len(res['retimed'])}건 · 빠진 요청 {res['dropped']}")
    out = ROOT / "data" / "experiments" / f"requery-{date}-{a.algo}-s{int(a.share * 100)}.csv"
    pd.DataFrame(res["log_rows"]).to_csv(out, index=False, encoding="utf-8-sig")
    print(f"  저장: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
