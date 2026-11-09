@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Push sys.path fix

echo ============================================
echo  Commit and push the Streamlit Cloud fix
echo ============================================
echo.

git add app/main.py .gitignore planner/data.py
if errorlevel 1 goto failed

git status --short
echo.

git commit -F commit_message_fix.txt
if errorlevel 1 (
    echo.
    echo Nothing new to commit, or commit failed.
    echo Trying push anyway...
)

echo.
echo Pushing...
git push
if errorlevel 1 goto pushfail

echo.
echo ============================================
echo  Pushed. Streamlit Cloud rebuilds automatically.
echo  Wait 1-2 minutes, then reload the app page.
echo ============================================
pause
exit /b 0

:pushfail
echo.
echo [ERROR] Push failed. You may need to sign in again.
echo         Run git_push.bat instead.
pause
exit /b 1

:failed
echo.
echo [ERROR] git add failed.
pause
exit /b 1
