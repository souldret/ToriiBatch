@echo off
chcp 65001 > nul
title ToriiBatch

cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" (
    echo.
    echo  [HATA] Sanal ortam bulunamadi!
    echo  Lutfen once install.bat calistirin.
    echo.
    pause
    exit /b 1
)

if not exist "main.py" (
    echo.
    echo  [HATA] main.py bulunamadi!
    echo  start.bat ile main.py ayni klasorde olmali.
    echo  Klasor: %~dp0
    echo.
    pause
    exit /b 1
)

call "%~dp0venv\Scripts\activate.bat"

python "%~dp0main.py"

set EXIT_CODE=%ERRORLEVEL%
if %EXIT_CODE% NEQ 0 (
    echo.
    echo  Uygulama bir hata ile kapandi! Cikis kodu: %EXIT_CODE%
    echo  Log: %APPDATA%\ToriiBatch\logs\app.log
    echo.
    pause
)

exit /b %EXIT_CODE%