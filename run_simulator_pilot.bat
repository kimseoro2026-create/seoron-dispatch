@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Simulator Pilot

echo ============================================
echo  Simulator PILOT run
echo ============================================
echo.
echo Scope: 3 days x 1 seed x 2 load factors, baselines only
echo Expected: about 1 minute
echo.
echo Use this to check that the pipeline works.
echo For the full run, use run_simulator_full.bat
echo.

python -m simulator.run --days 3 --seeds 42 --rhos 0.6,0.9 --no-optimize --tag pilot
if errorlevel 1 goto failed

echo.
echo ============================================
echo  Pilot done. Results are in data\experiments\
echo ============================================
pause
exit /b 0

:failed
echo.
echo [ERROR] Pilot run failed. See the message above.
pause
exit /b 1
