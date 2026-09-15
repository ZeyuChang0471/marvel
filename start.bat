@echo off
setlocal enabledelayedexpansion
title MARVEL

cd /d "%~dp0"

echo.
echo  ============================================================
echo    MARVEL
echo    LLM: DeepSeek V4 Pro + V4 Flash
echo  ============================================================
echo.

:: ---- check venv ----
if not exist "venv\Scripts\python.exe" (
    echo [ERROR] venv\Scripts\python.exe not found
    echo Run: python -m venv venv ^&^& venv\Scripts\python -m pip install -e .
    pause
    exit /b 1
)

:: ---- check .env ----
if not exist ".env" (
    echo [ERROR] .env file not found
    echo Create .env and set: DEEPSEEK_API_KEY=sk-yourkey
    pause
    exit /b 1
)

set "KEYOK=0"
for /f "usebackq tokens=2 delims==" %%v in (`findstr /b "DEEPSEEK_API_KEY" .env 2^>nul`) do (
    set "V=%%v"
    set "V=!V: =!"
    if not "!V!"=="" set "KEYOK=1"
)
if "!KEYOK!"=="0" (
    echo [ERROR] DEEPSEEK_API_KEY not set in .env
    echo Edit .env: DEEPSEEK_API_KEY=sk-yourkey
    pause
    exit /b 1
)

echo [OK] Environment ready
echo.

:: ---- menu ----
echo  [1] Web UI
echo  [2] Single stock analysis
echo  [3] Batch analysis (from stocks.txt)
echo  [0] Exit
echo.
set /p "MODE=Select (0-3): "

if "%MODE%"=="1" goto :WEB
if "%MODE%"=="2" goto :SINGLE
if "%MODE%"=="3" goto :BATCH
if "%MODE%"=="0" goto :EOF

echo Invalid option: %MODE%
pause
exit /b 1

:: ================================================================
::  Web UI
:: ================================================================
:WEB
echo.
echo Starting Streamlit Web UI...
echo Opening http://localhost:8501
echo Press Ctrl+C to stop
echo.
start "" http://localhost:8501
venv\Scripts\python -m streamlit run web\app.py --server.port 8501
goto :EOF

:: ================================================================
::  Single stock analysis
:: ================================================================
:SINGLE
echo.
set /p "TICKER=Stock code or name (e.g. 600519): "
if "!TICKER!"=="" (
    echo [ERROR] No stock code entered
    pause
    exit /b 1
)

set /p "DATE=Date (YYYY-MM-DD, Enter=today): "
if "!DATE!"=="" (
    for /f "delims=" %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "DATE=%%d"
)

echo.
echo ========================================
echo  Analyzing: !TICKER!  Date: !DATE!
echo ========================================
echo.

venv\Scripts\python run_single.py "!TICKER!" "!DATE!"
if errorlevel 1 (
    echo.
    echo [FAIL] Analysis failed, check errors above
    pause
    exit /b 1
)

echo.
echo [DONE] Results saved to .marvel\results\
pause
goto :EOF

:: ================================================================
::  Batch analysis
:: ================================================================
:BATCH
echo.

if not exist "stocks.txt" (
    echo [INFO] stocks.txt not found, creating sample...
    (
        echo # Batch stock list - one per line
        echo # Lines starting with # are comments
        echo 600519
        echo 000858
        echo 300750
    ) > stocks.txt
    echo [INFO] Created stocks.txt in project folder
    echo   Edit the file, then re-run option [3]
    pause
    exit /b 0
)

:: Count valid lines
set "COUNT=0"
for /f "usebackq eol=# tokens=1 delims= " %%s in ("stocks.txt") do (
    if not "%%s"=="" set /a COUNT+=1
)

if "!COUNT!"=="0" (
    echo [ERROR] No valid stocks in stocks.txt
    pause
    exit /b 1
)

echo [INFO] Stocks to analyze: !COUNT!
echo.

set "CUR=0"
set "OKN=0"
set "BAD=0"

for /f "usebackq eol=# tokens=1 delims= " %%s in ("stocks.txt") do (
    if not "%%s"=="" (
        set /a CUR+=1
        set "T=%%s"

        echo.
        echo === [!CUR!/!COUNT!] !T! ===

        venv\Scripts\python run_single.py "!T!"
        if errorlevel 1 (
            set /a BAD+=1
            echo [FAIL] !T!
        ) else (
            set /a OKN+=1
            echo [OK] !T!
        )
    )
)

echo.
echo ============================================================
echo   Batch complete!
echo   Total: !COUNT!   OK: !OKN!   Failed: !BAD!
echo   Results: .marvel\results\
echo ============================================================
echo.
pause
goto :EOF
