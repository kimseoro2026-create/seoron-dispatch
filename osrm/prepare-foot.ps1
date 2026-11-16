# OSRM 도보 경로 그래프 준비 (Windows PowerShell)
# 자동차용(data\)과 섞이지 않게 data-foot\ 에 따로 만든다. 같은 OSM 파일을 쓴다.
#
#   cd C:\dev\seoron-dispatch\osrm
#   .\prepare.ps1          # (먼저) 자동차용 — OSM 파일을 받는다
#   .\prepare-foot.ps1     # 도보용 — 20~40분
#   docker compose up -d   # osrm(5000, 자동차) + osrm-foot(5001, 도보)
#
# 필요: Docker Desktop 실행 중, 디스크 3GB 이상 여유

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Image = "ghcr.io/project-osrm/osrm-backend:v5.27.1"
$Name = "south-korea-latest"
$Src = "data\$Name.osm.pbf"
if (-not (Test-Path $Src)) { throw "OSM 파일이 없습니다: $Src (먼저 prepare.ps1 실행)" }

New-Item -ItemType Directory -Force -Path "data-foot" | Out-Null
if (-not (Test-Path "data-foot\$Name.osm.pbf")) {
    Copy-Item $Src "data-foot\$Name.osm.pbf"
}
$Volume = "$PWD\data-foot:/data"

Write-Host "[1/3] osrm-extract (foot) 실행 중 (10~20분)..."
docker run --rm -v "${Volume}" $Image osrm-extract -p /opt/foot.lua "/data/$Name.osm.pbf"
if ($LASTEXITCODE -ne 0) { throw "osrm-extract 실패" }

Write-Host "[2/3] osrm-partition 실행 중..."
docker run --rm -v "${Volume}" $Image osrm-partition "/data/$Name.osrm"
if ($LASTEXITCODE -ne 0) { throw "osrm-partition 실패" }

Write-Host "[3/3] osrm-customize 실행 중..."
docker run --rm -v "${Volume}" $Image osrm-customize "/data/$Name.osrm"
if ($LASTEXITCODE -ne 0) { throw "osrm-customize 실패" }

Write-Host ""
Write-Host "완료. 'docker compose up -d osrm-foot' 로 도보 서버(5001)를 켜고 .env 에 OSRM_FOOT_URL=http://localhost:5001 을 넣으세요."
