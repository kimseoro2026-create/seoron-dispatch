@echo off
chcp 65001 >nul
cd /d "%~dp0.."
title SeoroON Care Dispatch

echo ============================================
echo  SeoroON Care Dispatch - Run App
echo ============================================
echo.

REM Headless off = open browser automatically.
REM Usage stats off = no email/onboarding prompt.
set STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

python -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo [1/2] Installing streamlit... this takes 1-2 minutes.
    python -m pip install streamlit
    if errorlevel 1 (
        echo.
        echo [ERROR] Install failed. Run manually:
        echo     python -m pip install streamlit
        goto done
    )
) else (
    echo [1/2] streamlit already installed.
)

echo.
echo [2/2] Starting app on http://localhost:8501
echo        Press Ctrl+C in this window to stop.
echo.

python -m streamlit run app/main.py --server.headless false > streamlit_log.txt 2>&1
set EXITCODE=%errorlevel%

echo.
echo ============================================
echo  App stopped (exit code %EXITCODE%)
echo ============================================
if not "%EXITCODE%"=="0" (
    echo.
    echo Last lines of streamlit_log.txt:
    echo --------------------------------------------
    powershell -NoProfile -Command "Get-Content streamlit_log.txt -Tail 25"
    echo --------------------------------------------
)

:done
echo.
echo This window stays open. Press any key to close.
pause >nul
