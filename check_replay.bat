@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Check replay result

echo ============================================
echo  Cancellation replay - detailed check
echo ============================================
echo.
echo Runs 5 days x 2 baselines, prints violation codes.
echo Expect: violations = 0 everywhere.
echo.

python -m tools.check_replay
if errorlevel 1 goto failed

echo.
echo ============================================
echo  Legend
echo    refilled      : empty slot refilled by another request
echo    retimed       : arrival changed after a cancellation
echo    displaced     : dropped from the plan as a knock-on effect
echo    violations    : must be 0, otherwise the plan is unusable
echo ============================================
pause
exit /b 0

:failed
echo.
echo [ERROR] Check failed.
pause
exit /b 1
