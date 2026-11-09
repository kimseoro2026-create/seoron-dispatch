@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Git auth check

echo ============================================
echo  Git authentication check
echo ============================================
echo.

echo [1] Git version
git --version
echo.

echo [2] Remote URL
git remote -v
echo.

echo [3] Credential helper
git config --get credential.helper
if errorlevel 1 echo   (not set)
echo.

echo [4] Git Credential Manager
git credential-manager --version 2>nul
if errorlevel 1 (
    echo   NOT FOUND - this is likely the problem
) else (
    echo   OK - found
)
echo.

echo [5] Testing access to the remote
echo   If this hangs or asks for a password, authentication is not set up.
echo.
git ls-remote https://github.com/kimseoro2026-create/seoron-dispatch.git >nul 2>&1
if errorlevel 1 (
    echo   RESULT: FAILED - cannot access the repository
    echo           Either not authenticated, or the repo name is wrong.
) else (
    echo   RESULT: OK - repository is accessible
)
echo.

echo ============================================
echo  How to fix
echo ============================================
echo.
echo OPTION A: Use Git Credential Manager (easiest if found above)
echo   1. Run this command once:
echo        git config --global credential.helper manager
echo   2. Run git_push.bat again
echo   3. A browser window opens - sign in to GitHub
echo.
echo OPTION B: Use a Personal Access Token (works everywhere)
echo   1. Open https://github.com/settings/tokens
echo   2. Generate new token (classic)
echo      - Note: seoron-dispatch
echo      - Expiration: 90 days
echo      - Scope: check "repo"
echo   3. Copy the token (starts with ghp_)
echo   4. Run this command:
echo        git config --global credential.helper store
echo   5. Run git_push.bat
echo      - Username: kimseoro2026-create
echo      - Password: paste the token (NOT your GitHub password)
echo.
echo NOTE: Option B stores the token in plain text at
echo       C:\Users\%USERNAME%\.git-credentials
echo       On a shared computer, use Option A instead.
echo.
pause
