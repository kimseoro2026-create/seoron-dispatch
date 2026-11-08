@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Push to GitHub

set USERNAME=kimseoro2026-create
set REPO=seoron-dispatch

echo ============================================
echo  Push to GitHub
echo  https://github.com/%USERNAME%/%REPO%
echo ============================================
echo.

echo Before running this, create the repository on GitHub:
echo   https://github.com/new
echo     name: %REPO%
echo     visibility: Private (recommended) or Public
echo     do NOT add README, .gitignore, or license
echo.
echo It must be EMPTY. If it has a README, push will be rejected.
echo.

git remote get-url origin >nul 2>&1
if errorlevel 1 goto addremote

echo [1/3] Remote already set. Updating URL...
git remote set-url origin https://github.com/%USERNAME%/%REPO%.git
goto branch

:addremote
echo [1/3] Adding remote origin...
git remote add origin https://github.com/%USERNAME%/%REPO%.git
if errorlevel 1 goto failed

:branch
echo.
echo [2/3] Setting branch to main...
git branch -M main
if errorlevel 1 goto failed

echo.
echo [3/3] Pushing...
echo       A browser window may open for GitHub sign-in.
echo.
git push -u origin main
if errorlevel 1 goto pushfail

echo.
echo ============================================
echo  Pushed successfully.
echo  Check: https://github.com/%USERNAME%/%REPO%
echo ============================================
pause
exit /b 0

:pushfail
echo.
echo [ERROR] Push failed.
echo.
echo Common causes:
echo   1. The repository does not exist yet - create it at https://github.com/new
echo   2. The repository is not empty - it must have no README/gitignore/license
echo   3. You are not signed in - run: git config --global credential.helper manager
echo   4. Wrong username or repo name
echo.
pause
exit /b 1

:failed
echo.
echo [ERROR] Step failed. See the message above.
pause
exit /b 1
