@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Git setup

echo ============================================
echo  Git initialization and first commit
echo ============================================
echo.

git --version >nul 2>&1
if errorlevel 1 goto nogit

if not exist "commit_message.txt" goto nomsg

echo [1/5] git init
git init
if errorlevel 1 goto failed

echo.
echo [2/5] git config (local only)
git config user.name "kimseoro2026-create"
git config user.email "kimseoro2026-create@users.noreply.github.com"

echo.
echo [3/5] Staging files
git add .
echo.
echo --- files to be committed (first 25) ---
git status --short > git_status_tmp.txt
powershell -NoProfile -Command "Get-Content git_status_tmp.txt -TotalCount 25"
del git_status_tmp.txt

echo.
echo [4/5] Creating commit
git commit -F commit_message.txt
if errorlevel 1 goto failed

echo.
echo [5/5] Result
git log --oneline -3

echo.
echo ============================================
echo  Done. Next steps:
echo.
echo  1. Open https://github.com/new
echo  2. Repository name: seoron-dispatch
echo  3. Visibility: Private
echo  4. Do NOT check README / .gitignore / license
echo  5. Then run git_push.bat
echo ============================================
pause
exit /b 0

:nogit
echo [ERROR] Git is not installed or not in PATH.
echo         Download: https://git-scm.com/download/win
echo         After installing, close this window and run again.
pause
exit /b 1

:nomsg
echo [ERROR] commit_message.txt not found in this folder.
pause
exit /b 1

:failed
echo.
echo [ERROR] Step failed. See the message above.
echo.
echo If it says "nothing to commit", the files are already committed.
echo Run: git log --oneline
echo.
pause
exit /b 1
