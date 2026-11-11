@echo off
chcp 65001 >nul
cd /d "%~dp0.."

echo ============================================
echo  Hospital list fetch from platform API
echo ============================================
echo.
echo This takes 2 to 5 minutes. It fetches about 4,060 hospitals
echo from the platform API and saves them to data\raw\hospitals.csv
echo.
echo Do NOT close this window while it runs.
echo.

python -m f0 fetch --skip-population
if errorlevel 1 goto failed

echo.
echo ============================================
echo  Fetch finished. Check the count above.
echo ============================================
echo.
echo Then rebuild the region:
echo     python -m f0 generate
echo     python -m f0 matrix
echo.
pause
exit /b 0

:failed
echo.
echo [ERROR] Fetch failed.
echo.
echo If you see HTTP 503: the platform is rate limiting this key.
echo Wait 30 to 60 minutes and run this again.
echo.
echo If you see resultCode errors: check KOREACONNECT_API_USER_KEY_ID in .env
echo.
pause
exit /b 1
