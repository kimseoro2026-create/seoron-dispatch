@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Violation diagnosis

echo ============================================
echo  Diagnosing constraint violations
echo  Reproducing violations on 2026-11-18 and others
echo ============================================
echo.

python -m tools.diagnose_violations
if errorlevel 1 goto failed

echo.
pause
exit /b 0

:failed
echo.
echo [ERROR] Diagnosis failed.
pause
exit /b 1
