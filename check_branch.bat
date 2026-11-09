@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Branch check

echo ============================================
echo  Branch check
echo ============================================
echo.

echo [1] Local branches
git branch -a
echo.

echo [2] Current branch
git rev-parse --abbrev-ref HEAD
echo.

echo [3] Remote URL
git remote -v
echo.

echo [4] What the remote actually has
git ls-remote --heads origin
if errorlevel 1 (
    echo   FAILED - cannot read remote
    echo   If this says "not found", authentication or access is the problem.
)
echo.

echo ============================================
echo  In Streamlit Cloud you need:
echo    Repository : kimseoro2026-create/seoron-dispatch
echo    Branch     : whatever [4] shows above (main or master)
echo    Main file  : app/main.py
echo ============================================
pause
