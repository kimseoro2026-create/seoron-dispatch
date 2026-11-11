@echo off
chcp 65001 >nul
cd /d "%~dp0.."

echo ============================================
echo  OSRM matrix rebuild
echo ============================================
echo.

echo [1/2] Checking OSRM server at localhost:5000 ...
curl.exe -s -o nul -w "  HTTP %%{http_code}\n" "http://localhost:5000/nearest/v1/driving/127.075,37.2"
if errorlevel 1 goto noserver

echo.
echo [2/2] Rebuilding travel matrix with OSRM ...
python -m f0 matrix
if errorlevel 1 goto failed

echo.
echo ============================================
echo  Done. Check travel_matrix.csv "source" column.
echo  It should now say "osrm".
echo ============================================
pause
exit /b 0

:noserver
echo.
echo [ERROR] OSRM server is not responding on port 5000.
echo         Start it first:
echo             cd /d "%~dp0osrm"
echo             docker compose up -d
pause
exit /b 1

:failed
echo.
echo [ERROR] Matrix build failed. See the message above.
pause
exit /b 1
