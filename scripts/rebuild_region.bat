@echo off
chcp 65001 >nul
cd /d "%~dp0.."
title Rebuild Region

echo ============================================
echo  Rebuild virtual district
echo ============================================
echo.
echo [1/2] Generating beneficiaries, workers, hospitals, requests...
echo       (uses real hospital data from data\raw\hospitals.csv)
echo.
python -m f0 generate
if errorlevel 1 goto failed

echo.
echo [2/2] Rebuilding travel matrix with OSRM...
python -m f0 matrix
if errorlevel 1 goto failed

echo.
echo ============================================
echo  Done. Refresh Streamlit to see new data.
echo ============================================
pause
exit /b 0

:failed
echo.
echo [ERROR] Step failed. See output above.
pause
exit /b 1
