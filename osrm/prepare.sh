#!/usr/bin/env bash
# OSRM 데이터 준비: 한국 지도 내려받기 → (선택) 경기 남부만 자르기 → 경로 그래프 생성
# 필요: Docker, curl. 선택: osmium-tool(있으면 자르기로 메모리·시간 절약)
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data

IMAGE="ghcr.io/project-osrm/osrm-backend:v5.27.1"
PBF_URL="https://download.geofabrik.de/asia/south-korea-latest.osm.pbf"
FULL="data/south-korea-latest.osm.pbf"
# 화성·오산·수원·용인 남부를 넉넉히 포함하는 범위(경도,위도)
CLIP_BBOX="126.60,36.95,127.30,37.40"

if [ ! -f "$FULL" ]; then
  echo "[1/4] 한국 OSM 데이터 내려받는 중..."
  curl -L --fail -o "$FULL" "$PBF_URL"
fi

INPUT="south-korea-latest.osm.pbf"
if command -v osmium >/dev/null 2>&1; then
  echo "[2/4] 경기 남부 범위로 자르는 중 (osmium)..."
  osmium extract -b "$CLIP_BBOX" "$FULL" -o data/region.osm.pbf --overwrite
  INPUT="region.osm.pbf"
else
  echo "[2/4] osmium이 없어 전국 데이터로 진행합니다 (메모리 6GB 이상 권장)"
fi
BASE="${INPUT%.osm.pbf}"

echo "[3/4] 경로 그래프 생성(자동차 프로필)..."
docker run --rm -t -v "$PWD/data:/data" "$IMAGE" osrm-extract -p /opt/car.lua "/data/$INPUT"
docker run --rm -t -v "$PWD/data:/data" "$IMAGE" osrm-partition "/data/$BASE.osrm"
docker run --rm -t -v "$PWD/data:/data" "$IMAGE" osrm-customize "/data/$BASE.osrm"

echo "OSRM_BASE=$BASE" > .env
echo "[4/4] 완료. 'docker compose up -d'로 서버를 켜세요."
