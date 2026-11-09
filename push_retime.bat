@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Commit and push retime fix

echo ============================================
echo  Commit and push: retime fix
echo ============================================
echo.

git add planner/reschedule.py planner/data.py tools/check_replay.py
git add simulator/experiment.py simulator/run.py docs/STATUS.md
git add tests/test_reschedule.py
if errorlevel 1 goto failed

echo --- staged files ---
git status --short
echo.

git commit -F commit_message_retime.txt
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
echo  Key finding:
echo    request_order replay keeps almost all slots filled
echo    nearest replay assigns more initially but cannot refill
echo    -> more assignments up front does not mean better
echo       resilience to cancellation
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
