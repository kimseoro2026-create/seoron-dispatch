"""외부 자료 받기(실제 호출). 화면은 저장된 결과만 읽는다.

    python -m adapters.fetch weather            # 기상청 단기예보 → data/raw/weather_forecast.csv
    python -m adapters.fetch transit --n 5      # 병원동행 n건의 집→병원 대중교통 → data/raw/transit_sample.csv

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
    ap.add_argument("what", choices=["weather", "transit"])
    ap.add_argument("--n", type=int, default=5)
    a = ap.parse_args(argv)
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


if __name__ == "__main__":
    sys.exit(main())
