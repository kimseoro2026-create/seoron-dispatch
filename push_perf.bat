@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Push performance fix

echo ============================================
echo  Commit and push performance improvements
echo ============================================
echo.

git add app/main.py planner/data.py f0/geo.py
if errorlevel 1 goto failed

git status --short
echo.

git commit -F commit_message_perf.txt
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
echo  Changes:
echo    - travel matrix built per day only (~4900 pairs vs 519120)
echo    - estimate computed with numpy (was a Python double loop)
echo    - timeline shows up to 8 workers with assignments
echo    - optimizer limit 30s -> 8s
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
