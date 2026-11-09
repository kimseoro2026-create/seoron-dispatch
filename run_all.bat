@echo off
chcp 65001 >nul
cd /d "%~dp0"
title All remaining checks

echo ============================================
echo  Run everything and report in one pass
echo ============================================
echo.

echo [1/4] Full test suite
python -m pytest -q
if errorlevel 1 goto failed
echo.

echo [2/4] Rebuild cancel30 summary
python -m tools.resummarize data/experiments/cancel30-20261109-131309.csv cancel30
echo.

echo [3/4] Stage and commit
git add -A
git commit -F commit_message_cancel30.txt
if errorlevel 1 echo   (nothing new to commit, continuing)
echo.

echo [4/4] Push
git push
if errorlevel 1 goto pushfail

echo.
echo ============================================
echo  ALL DONE
echo ============================================
echo.
echo If tests passed and push succeeded, nothing else is needed.
pause
exit /b 0

:pushfail
echo.
echo [ERROR] Push failed.
echo This usually means you are signed out. Run git_push.bat.
pause
exit /b 1

:failed
echo.
echo [ERROR] Tests failed. See the output above.
echo Do not push until tests pass.
pause
exit /b 1
