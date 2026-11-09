@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Commit and push approval flow

echo ============================================
echo  Commit and push: approval flow
echo ============================================
echo.

git add app/main.py planner/approval.py planner/data.py f0/geo.py
git add docs/STATUS.md tests/test_approval.py
if errorlevel 1 goto failed

echo --- staged files ---
git status --short
echo.

git commit -F commit_message_approval.txt
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
echo  Pushed. Streamlit Cloud rebuilds in 1-2 min.
echo.
echo  What is new:
echo    - approval flow: accept / reject / hold per request
echo    - locked confirmed assignments (cannot overwrite)
echo    - reject requires a reason
echo    - audit trail of every decision
echo    - faster: day-scoped travel matrix, 8 workers in timeline
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
