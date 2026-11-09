@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Test replay integration

echo ============================================
echo  Test: reschedule connected to simulator
echo ============================================
echo.

echo [1/3] Unit tests (simulator + reschedule)...
python -m pytest tests/test_simulator.py tests/test_reschedule.py -q
if errorlevel 1 goto failed

echo.
echo [2/3] Full test suite...
python -m pytest -q
if errorlevel 1 goto failed

echo.
echo [3/3] Replay on real data (1 day, baselines only)...
python -m simulator.run --days 1 --seeds 42 --rhos 0.9 --no-optimize --replay --tag replaycheck
if errorlevel 1 goto failed

echo.
echo ============================================
echo  Done. Look for the *_replay rows above.
echo  Columns: cancellations, cancelled_with_assignment,
echo           refilled, left_empty, wasted_travel_min
echo ============================================
pause
exit /b 0

:failed
echo.
echo [ERROR] Step failed. See the message above.
pause
exit /b 1
