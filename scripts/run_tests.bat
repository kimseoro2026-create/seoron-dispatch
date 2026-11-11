@echo off
chcp 65001 >nul
cd /d "%~dp0.."
title Run Tests

echo ============================================
echo  Run test suite
echo ============================================
echo.

if "%~1"=="" goto all
if "%~1"=="fast" goto fast
goto specific

:all
echo Mode: ALL tests
echo.
python -m pytest -q
goto done

:fast
echo Mode: FAST (skip optimizer, about 10 sec)
echo.
python -m pytest -q --ignore=tests/test_optimize.py
goto done

:specific
echo Mode: specified file - %~1
echo.
python -m pytest "%~1" -q

:done
echo.
echo ============================================
echo  Finished. See the summary above.
echo ============================================
pause
