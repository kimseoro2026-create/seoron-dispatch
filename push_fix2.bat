@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Push travel-source fix

echo ============================================
echo  Commit and push the travel-source fix
echo ============================================
echo.

git add app/main.py
if errorlevel 1 goto failed

git status --short
echo.

git commit -F commit_message_fix2.txt
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
echo  Pushed. Streamlit Cloud rebuilds automatically.
echo  After 1-2 minutes the sidebar should read:
echo    이동시간 출처: 직선거리 대략값
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
