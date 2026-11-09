@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 30-day cancellation experiment

echo ============================================
echo  30-day cancellation experiment
echo ============================================
echo.
echo Scope: 30 days x 3 seeds x 3 load factors x 4 methods = 1080 runs
echo   request_order, nearest, request_order_replay, nearest_replay
echo   Optimizer is excluded (it does not support replay yet)
echo.
echo Expected: 10 to 20 minutes
echo This window must stay open. Progress prints for every run.
echo.

timeout /t 3 >nul

python -m simulator.run --no-optimize --replay --tag cancel30
if errorlevel 1 goto failed

echo.
echo ============================================
echo  Done. Results in data\experiments\cancel30-*.csv
echo  and summary-cancel30-*.csv
echo ============================================
pause
exit /b 0

:failed
echo.
echo [ERROR] Experiment failed. See the message above.
pause
exit /b 1
