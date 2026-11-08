@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Verify optimizer repair

echo ============================================
echo  Verify optimizer with repair pass
echo ============================================
echo.
echo [1/3] Running optimizer tests...
python -m pytest tests/test_optimize.py -q
if errorlevel 1 goto failed

echo.
echo [2/3] Checking violations are still zero...
python -m tools.diagnose_violations
if errorlevel 1 goto failed

echo.
echo [3/3] Comparing methods on one day...
python -m simulator.run --days 1 --seeds 42 --rhos 0.9 --time-limit 10 --tag repaircheck
if errorlevel 1 goto failed

echo.
echo ============================================
echo  Done. Compare 'optimize' row with previous run.
echo  Before repair: optimize 33, nearest 33 (tie)
echo ============================================
pause
exit /b 0

:failed
echo.
echo [ERROR] Step failed. See the message above.
pause
exit /b 1
