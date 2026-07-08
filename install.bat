@echo off
chcp 65001 > nul
title ToriiBatch - Kurulum

cd /d "%~dp0"

echo.
echo  =========================================
echo   ToriiBatch Kurulum Scripti
echo  =========================================
echo.

echo [1/4] Python 3.11+ kontrol ediliyor...

set PYTHON_CMD=

py -3.11 --version > nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set PYTHON_CMD=py -3.11
    goto python_found
)

py --version > nul 2>&1
if %ERRORLEVEL% EQU 0 (
    py -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)" > nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        set PYTHON_CMD=py
        goto python_found
    )
)

python --version > nul 2>&1
if %ERRORLEVEL% EQU 0 (
    python -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)" > nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        set PYTHON_CMD=python
        goto python_found
    )
)

echo.
echo  [HATA] Python 3.11+ bulunamadi!
echo  https://www.python.org/downloads/ adresinden indirin.
echo  Kurulumda "Add python.exe to PATH" secenegini isaretleyin.
echo.
pause
exit /b 1

:python_found
%PYTHON_CMD% --version
echo     Python OK (%PYTHON_CMD%)

echo.
echo [2/4] Sanal ortam kontrol ediliyor...

if exist "%~dp0venv\Scripts\activate.bat" (
    echo     venv zaten mevcut, atlaniyor.
) else (
    echo     venv olusturuluyor...
    %PYTHON_CMD% -m venv "%~dp0venv"
    if %ERRORLEVEL% NEQ 0 (
        echo  [HATA] venv olusturulamadi!
        pause
        exit /b 1
    )
    echo     venv olusturuldu.
)

echo.
echo [3/4] pip guncelleniyor...

call "%~dp0venv\Scripts\activate.bat"
"%~dp0venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
echo     pip OK.

echo.
echo [4/4] Bagimliliklar yukleniyor...
echo     (Birkas dakika surebilir)
echo.

"%~dp0venv\Scripts\pip.exe" install -r "%~dp0requirements.txt"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  [HATA] Kurulum basarisiz!
    echo  Internet baglantinizi kontrol edin.
    echo.
    pause
    exit /b 1
)

echo.
echo  =========================================
echo   Kurulum tamamlandi!
echo   Baslatmak icin: start.bat
echo  =========================================
echo.
pause
exit /b 0