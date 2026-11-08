@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal

set IMAGE=ghcr.io/project-osrm/osrm-backend:v5.27.1
set URL=https://download.geofabrik.de/asia/south-korea-latest.osm.pbf
set NAME=south-korea-latest
set FULL=data\%NAME%.osm.pbf

echo ============================================
echo  OSRM data preparation
echo ============================================
echo.

docker ps >nul 2>&1
if errorlevel 1 goto nodocker

if not exist data mkdir data

if exist "%FULL%" goto havedata

echo [1/4] Downloading South Korea OSM data - about 700MB, 5 to 10 min...
curl.exe -L --fail -o "%FULL%" "%URL%"
if errorlevel 1 goto dlfail
goto extract

:havedata
echo [1/4] Data already downloaded, skipping.

:extract
echo.
echo [2/4] osrm-extract - this takes 10 to 20 minutes. Do not close this window.
docker run --rm -v "%CD%\data:/data" %IMAGE% osrm-extract -p /opt/car.lua /data/%NAME%.osm.pbf
if errorlevel 1 goto fail

echo.
echo [3/4] osrm-partition
docker run --rm -v "%CD%\data:/data" %IMAGE% osrm-partition /data/%NAME%.osrm
if errorlevel 1 goto fail

echo.
echo [4/4] osrm-customize
docker run --rm -v "%CD%\data:/data" %IMAGE% osrm-customize /data/%NAME%.osrm
if errorlevel 1 goto fail

echo OSRM_BASE=%NAME%> .env

echo.
echo ============================================
echo  Done. Starting the server.
echo ============================================
docker compose up -d
if errorlevel 1 goto composefail

echo.
echo Server is up. Verify with:
echo     curl "http://localhost:5000/route/v1/driving/127.075,37.2;127.06,37.21"
echo.
echo You can close this window. The server keeps running.
pause
exit /b 0

:nodocker
echo [ERROR] Docker is not running.
echo         Start Docker Desktop first, then run this again.
pause
exit /b 1

:dlfail
echo [ERROR] Download failed. Check your internet connection.
pause
exit /b 1

:composefail
echo [ERROR] docker compose failed. Run manually:
echo     cd /d "%CD%"
echo     docker compose up -d
pause
exit /b 1

:fail
echo.
echo [ERROR] Graph build failed.
echo         Memory may be too low. Raise Docker Desktop memory to 6 GB or more.
pause
exit /b 1
