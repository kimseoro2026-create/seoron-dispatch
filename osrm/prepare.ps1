# OSRM 데이터 준비 (Windows PowerShell 버전)
# prepare.sh 와 같은 일을 한다: 한국 OSM 내려받기 → 경로 그래프 생성
#
#   cd C:\dev\seoron-dispatch\osrm
#   .\prepare.ps1
#
# 필요: Docker Desktop 실행 중, 디스크 3GB 이상 여유
# 주의: osmium 도구가 없으면 전국 데이터로 진행한다(메모리 6GB 이상 권장).
#       20~40분 걸린다. 중간에 멈춰도 data\ 폴더를 지우고 다시 실행하면 된다.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force -Path "data" | Out-Null

$Image = "ghcr.io/project-osrm/osrm-backend:v5.27.1"
$Url = "https://download.geofabrik.de/asia/south-korea-latest.osm.pbf"
$Full = "data\south-korea-latest.osm.pbf"

# 1) 데이터 내려받기
if (-not (Test-Path $Full)) {
    Write-Host "[1/4] 한국 OSM 데이터 내려받는 중 (약 700MB)..."
    curl.exe -L --fail -o $Full $Url
    if ($LASTEXITCODE -ne 0) { throw "다운로드 실패: $Url" }
} else {
    Write-Host "[1/4] 이미 받은 데이터가 있어 건너뜁니다: $Full"
}

# 2) 경로 그래프 생성 (전국 데이터 기준)
$Name = "south-korea-latest"
$Volume = "$PWD\data:/data"

Write-Host "[2/4] osrm-extract 실행 중 (10~20분)..."
docker run --rm -v "${Volume}" $Image osrm-extract -p /opt/car.lua "/data/$Name.osm.pbf"
if ($LASTEXITCODE -ne 0) { throw "osrm-extract 실패" }

Write-Host "[3/4] osrm-partition 실행 중..."
docker run --rm -v "${Volume}" $Image osrm-partition "/data/$Name.osrm"
if ($LASTEXITCODE -ne 0) { throw "osrm-partition 실패" }

Write-Host "[4/4] osrm-customize 실행 중..."
docker run --rm -v "${Volume}" $Image osrm-customize "/data/$Name.osrm"
if ($LASTEXITCODE -ne 0) { throw "osrm-customize 실패" }

# docker-compose 가 읽을 .env
"OSRM_BASE=$Name" | Out-File -Encoding utf8 -NoNewline ".env"

Write-Host ""
Write-Host "완료. 이제 서버를 켜세요:"
Write-Host "  docker compose up -d"
Write-Host ""
Write-Host "동작 확인:"
Write-Host '  curl "http://localhost:5000/route/v1/driving/127.075,37.2;127.06,37.21"'
