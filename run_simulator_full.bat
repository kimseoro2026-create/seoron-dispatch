@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Simulator Full Run

echo ============================================
echo  Simulator FULL run (30 days, all methods)
echo ============================================
echo.
echo Scope: 30 days x 3 seeds x 3 load factors x 3 methods
echo Expected: 45 to 90 minutes
echo.
echo This window must stay open. Progress prints for every run.
echo You can close it early and keep partial results, but the CSV
echo is only written at the end.
echo.
echo Press Ctrl+C to cancel, or
pause

echo.
echo [1/2] Running experiment...
python -m simulator.run --tag full
if errorlevel 1 goto failed

echo.
echo ============================================
echo  Done. Results are in data\experiments\
echo ============================================
pause
exit /b 0

:failed
echo.
echo [ERROR] Experiment failed. See the message above.
pause
exit /b 1
