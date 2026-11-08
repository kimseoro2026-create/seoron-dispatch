@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Optimizer quick check

echo ============================================
echo  Optimizer quick check (1 day, 1 seed)
echo ============================================
echo.
echo Checking that the optimizer now assigns requests
echo after the PATH_CHEAPEST_ARC fix.
echo Expected: assigned > 0, no timeout
echo.

python -m simulator.run --days 1 --seeds 42 --rhos 0.9 --time-limit 30 --tag optcheck
if errorlevel 1 goto failed

echo.
echo ============================================
echo  Check complete. See numbers above.
echo  If assigned > 0 for optimize, run the full experiment.
echo ============================================
pause
exit /b 0

:failed
echo.
echo [ERROR] Check failed.
pause
exit /b 1
