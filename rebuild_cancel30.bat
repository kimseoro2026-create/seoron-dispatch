@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Rebuild cancel30 summary

echo ============================================
echo  Rebuild summary from saved raw CSV
echo ============================================
echo.
echo The 1080-row run already finished. Only the summary
echo step failed (a missing column). No need to re-run it.
echo.

if not exist "data\experiments\cancel30-20261109-131309.csv" goto notfound

python -m tools.resummarize data/experiments/cancel30-20261109-131309.csv cancel30
if errorlevel 1 goto failed

echo.
echo ============================================
echo  Done. Summary saved next to the raw CSV.
echo ============================================
pause
exit /b 0

:notfound
echo [ERROR] Raw file not found:
echo         data\experiments\cancel30-20261109-131309.csv
echo.
echo Use this instead if the name changed:
echo    dir data\experiments\cancel30-*.csv
pause
exit /b 1

:failed
echo.
echo [ERROR] Summary rebuild failed. See the message above.
pause
exit /b 1
