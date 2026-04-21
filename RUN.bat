@echo off
chcp 65001 >nul 2>nul
title USN Declaration Server
cd /d "%~dp0"

echo.
echo ============================================================
echo   Nalogovaya deklaratsiya IP na USN 6%%
echo ============================================================
echo.

rem --- Find Python (prefer py launcher which finds correct install) ---
set PY=
py -3.14 --version >nul 2>nul && set PY=py -3.14 && goto :found
py -3.13 --version >nul 2>nul && set PY=py -3.13 && goto :found
py -3.12 --version >nul 2>nul && set PY=py -3.12 && goto :found
py -3.11 --version >nul 2>nul && set PY=py -3.11 && goto :found
py -3.10 --version >nul 2>nul && set PY=py -3.10 && goto :found
py --version >nul 2>nul && set PY=py && goto :found
python --version >nul 2>nul && set PY=python && goto :found

echo.
echo   [!] Python ne nayden.
echo   Skachayte: https://www.python.org/downloads/
echo   Pri ustanovke: [x] Add Python to PATH
echo.
cmd /k
exit /b

:found
echo Python: & %PY% --version
echo.

rem --- Ensure pip is available ---
%PY% -m pip --version >nul 2>nul
if errorlevel 1 (
    echo pip ne nayden, ustanavlivayu...
    %PY% -m ensurepip --upgrade >nul 2>nul
)

rem --- Install packages ---
echo Proverka bibliotek...
%PY% -c "import fastapi, uvicorn, sqlalchemy, openpyxl, pydantic, aiofiles" >nul 2>nul
if errorlevel 1 (
    echo Ustanovka bibliotek...
    %PY% -m pip install --prefer-binary fastapi uvicorn sqlalchemy python-multipart openpyxl aiofiles pydantic
    if errorlevel 1 (
        echo.
        echo   [!] Oshibka ustanovki. Podrobnosti vyshe.
        echo.
        cmd /k
        exit /b
    )
)
echo OK.
echo.

rem --- Free port 8000 ---
for /f "tokens=5" %%P in ('netstat -ano 2^>nul ^| findstr :8000 ^| findstr LISTENING') do (
    taskkill /F /PID %%P >nul 2>nul
)

echo ============================================================
echo   http://localhost:8000
echo ============================================================
echo   Ctrl+C = ostanovit server
echo.

%PY% -u run.py

echo.
echo Server ostanovlen.
cmd /k
