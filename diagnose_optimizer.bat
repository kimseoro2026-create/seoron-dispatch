@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo  Optimizer diagnosis
echo ============================================
echo.
echo Building the model with one component disabled at a time.
echo If disabling a component turns the status into SUCCESS or PARTIAL,
echo that component is the cause.
echo.
echo Status legend:
echo   1 SUCCESS   2 PARTIAL   3 FAIL (no first solution)
echo   4 TIMEOUT   5 INVALID (model construction error)
echo.

python -m tools.diagnose_optimize --time-limit 3
if errorlevel 1 goto failed

echo.
pause
exit /b 0

:failed
echo.
echo [ERROR] Diagnosis failed to run.
pause
exit /b 1
