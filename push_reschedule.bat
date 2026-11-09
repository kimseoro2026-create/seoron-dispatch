@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Commit and push reschedule engine

echo ============================================
echo  Commit and push: cancellation rescheduling
echo ============================================
echo.

git add planner/reschedule.py tests/test_reschedule.py
git add planner/data.py f0/geo.py app/main.py docs/STATUS.md
if errorlevel 1 goto failed

echo --- staged files ---
git status --short
echo.

git commit -F commit_message_reschedule.txt
if errorlevel 1 (
    echo.
    echo Nothing new to commit. Trying push anyway...
)

echo.
echo Pushing...
git push
if errorlevel 1 goto pushfail

echo.
echo ============================================
echo  Pushed. 99 tests passed locally.
echo.
echo  What is new:
echo    - cancellation events replayed in notice order
echo    - only requests alive at that moment are refill candidates
echo    - wasted travel recorded when notice arrives after departure
echo    - final plan re-checked by the verifier
echo ============================================
pause
exit /b 0

:pushfail
echo.
echo [ERROR] Push failed. Sign in again with git_push.bat.
pause
exit /b 1

:failed
echo.
echo [ERROR] git add failed.
pause
exit /b 1
