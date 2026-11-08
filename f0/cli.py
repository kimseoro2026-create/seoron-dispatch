"""F0 실행 명령.

python -m f0 mock        모의 원천 데이터 만들기(오프라인 검증용)
python -m f0 fetch       플랫폼 API로 병원·인구 받기 (apis.yaml, KOREACONNECT_API_USER_KEY_ID 필요)
python -m f0 generate    가상 구 생성 → data/processed
python -m f0 matrix      이동시간 행렬 계산 (OSRM_URL 있으면 OSRM, 없으면 대략값)
python -m f0 all --offline   mock → generate → matrix 한 번에
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass


def _region_cfg() -> dict:
    with open(ROOT / "config" / "region.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _seed() -> int:
    return int(os.environ.get("F0_SEED", "42"))


def cmd_mock(_args) -> None:
    from .mock import write_mock_raw
    cfg = _region_cfg()
    write_mock_raw(RAW, cfg["region"], _seed())
    print(f"모의 원천 데이터 저장: {RAW} (boundaries.geojson, population.csv, hospitals.csv)")


def cmd_fetch(args) -> None:
    from .apiclient import PlatformClient
    from .sources import fetch_hospitals, fetch_population
    cfg = _region_cfg()
    client = PlatformClient()
    RAW.mkdir(parents=True, exist_ok=True)
    hosp = fetch_hospitals(client, cfg["region"])
    hosp.to_csv(RAW / "hospitals.csv", index=False, encoding="utf-8-sig")
    print(f"병원 {len(hosp)}곳 저장")
    if not args.skip_population:
        extra = dict(kv.split("=", 1) for kv in (args.population_param or []))
        pop = fetch_population(client, **extra)
        pop.to_csv(RAW / "population.csv", index=False, encoding="utf-8-sig")
        print(f"행정동 {len(pop)}곳 인구 저장")


def cmd_generate(_args) -> None:
    from .geo import load_boundaries
    from .generate import generate_world
    cfg = _region_cfg()
    region, sim = cfg["region"], cfg["simulation"]
    boundaries = load_boundaries(ROOT / region["boundaries_file"], region["dong_name_field"])
    hospitals = pd.read_csv(RAW / "hospitals.csv", dtype={"hospital_id": str})
    population = pd.read_csv(RAW / "population.csv")
    world = generate_world(sim, hospitals, population, boundaries, _seed())
    OUT.mkdir(parents=True, exist_ok=True)
    for name in ("beneficiaries", "workers", "hospitals", "requests"):
        getattr(world, name).to_csv(OUT / f"{name}.csv", index=False, encoding="utf-8-sig")
    req = world.requests
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "seed": _seed(),
        "assumption_note": "simulation 파라미터는 초기 가정값입니다(config/region.yaml).",
        "beneficiaries": len(world.beneficiaries), "workers": len(world.workers), "hospitals": len(world.hospitals),
        "service_days": int(req["service_date"].nunique()),
        "requests": len(req),
        "requests_per_day_avg": round(len(req) / max(1, req["service_date"].nunique()), 1),
        "escort_share": round(float((req["service"] == "hospital_escort").mean()), 3),
        "cancel_rate": round(float(req["cancelled_same_day"].mean()), 3),
        "discharge_share": round(float(req["discharge_case"].mean()), 3),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_matrix(_args) -> None:
    from .generate import World
    from .matrix import build_matrix, build_nodes
    cfg = _region_cfg()
    world = World(
        beneficiaries=pd.read_csv(OUT / "beneficiaries.csv"),
        workers=pd.read_csv(OUT / "workers.csv"),
        hospitals=pd.read_csv(OUT / "hospitals.csv", dtype={"hospital_id": str}),
        requests=pd.read_csv(OUT / "requests.csv"),
    )
    nodes = build_nodes(cfg["region"], world)
    factor = float(os.environ.get("OSRM_TIME_FACTOR", "1.0"))
    long, source = build_matrix(nodes, os.environ.get("OSRM_URL"), factor)
    nodes.to_csv(OUT / "nodes.csv", index=False, encoding="utf-8-sig")
    long.to_csv(OUT / "travel_matrix.csv", index=False, encoding="utf-8-sig")
    note = "OSRM 도로 소요시간" if source == "osrm" else "직선거리 기반 대략값(OSRM 미연결)"
    print(f"노드 {len(nodes)}개, 행렬 {len(long):,}칸 저장 - 출처: {note}")


def cmd_all(args) -> None:
    if args.offline:
        cmd_mock(args)
    else:
        cmd_fetch(args)
    cmd_generate(args)
    cmd_matrix(args)


def main() -> None:
    _load_env()
    # Windows 콘솔 기본 인코딩(cp949)에서 한글·기호 출력이 깨지거나 죽지 않도록 UTF-8로 고정한다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="f0", description="F0 가상 구 데이터")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("mock").set_defaults(func=cmd_mock)
    f = sub.add_parser("fetch")
    f.add_argument("--skip-population", action="store_true")
    f.add_argument("--population-param", nargs="*", help="인구 API 추가 요청변수 key=value")
    f.set_defaults(func=cmd_fetch)
    sub.add_parser("generate").set_defaults(func=cmd_generate)
    sub.add_parser("matrix").set_defaults(func=cmd_matrix)
    a = sub.add_parser("all")
    a.add_argument("--offline", action="store_true")
    a.add_argument("--skip-population", action="store_true")
    a.add_argument("--population-param", nargs="*")
    a.set_defaults(func=cmd_all)
    args = p.parse_args()
    args.func(args)
