@echo off
title Push to GitHub
echo.
echo ========================================
echo   Push project to GitHub + Render
echo ========================================
echo.

cd /d "%~dp0"

git add -A
git status --short
echo.

set /p COMMIT_MSG="Commit message (Enter = update): "
if "%COMMIT_MSG%"=="" set COMMIT_MSG=Update project

git commit -m "%COMMIT_MSG%"
git push -u origin main

echo.
echo ========================================
echo   Done! Render will update in 2-3 min.
echo   https://usn-declaration.onrender.com
echo ========================================
echo.
pause
