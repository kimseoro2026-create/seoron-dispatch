@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Push cancel30 results

echo ============================================
echo  Commit and push: 30-day cancellation results
echo ============================================
echo.

git add simulator/experiment.py tools/resummarize.py docs/STATUS.md
if errorlevel 1 goto failed

echo --- staged files ---
git status --short
echo.

git commit -F commit_message_cancel30.txt
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
echo  Pushed.
echo.
echo  Key result (rho=0.9, unique beneficiaries served):
echo    nearest + replay        28.97
echo    request_order + replay  27.00
echo.
echo  But refill success:
echo    request_order  68% of cancelled assignments recovered
echo    nearest         8% - schedules too tight to refill
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
