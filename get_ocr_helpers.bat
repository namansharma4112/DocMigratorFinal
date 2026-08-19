@echo off
setlocal enabledelayedexpansion
REM ---------------------------------------------------------------------------
REM Downloads Tesseract-OCR + Poppler into .\vendor so the build can bundle them.
REM
REM FIX (2026-08): all URLs and paths are now defined at the TOP, before any
REM if/else block. Previously they were `set` INSIDE an else(...) block and used
REM in that same block, so batch expanded %VAR% at parse time (before the set ran)
REM -> empty -Uri '' / -OutFile '' and "Invalid URI: hostname could not be parsed".
REM ---------------------------------------------------------------------------
set "ROOT=%~dp0"
set "VENDOR=%ROOT%vendor"
set "TESS_URL=https://github.com/UB-Mannheim/tesseract/releases/download/v5.5.0/tesseract-ocr-w64-setup-5.5.0.20241111.exe"
set "TESS_INSTALLER=%TEMP%\tesseract-installer.exe"
set "POPPLER_URL=https://github.com/oschwartz10612/poppler-windows/releases/download/v24.08.0-0/Release-24.08.0-0.zip"
set "POPPLER_ZIP=%TEMP%\poppler.zip"

echo.
echo === Legal Doc Migration - OCR helper downloader ===
echo.
if not exist "%VENDOR%" mkdir "%VENDOR%"

REM ------------------------------------------------------------------ Tesseract
if exist "%VENDOR%\tesseract\tesseract.exe" (
    echo [1/2] Tesseract already present - skipping download.
) else (
    echo [1/2] Downloading Tesseract-OCR installer...
    powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%TESS_URL%' -OutFile '%TESS_INSTALLER%' -UseBasicParsing"
    if exist "%TESS_INSTALLER%" (
        echo       Installing Tesseract silently into vendor\tesseract ...
        "%TESS_INSTALLER%" /S /D=%VENDOR%\tesseract
        timeout /t 5 /nobreak >nul
    ) else (
        echo   FAILED to download Tesseract. Get it manually from
        echo   https://github.com/UB-Mannheim/tesseract/wiki
    )
)

REM -------------------------------------------------------------------- Poppler
if exist "%VENDOR%\poppler\Library\bin\pdftoppm.exe" (
    echo [2/2] Poppler already present - skipping download.
) else (
    echo [2/2] Downloading Poppler for Windows...
    powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%POPPLER_URL%' -OutFile '%POPPLER_ZIP%' -UseBasicParsing"
    if exist "%POPPLER_ZIP%" (
        echo       Extracting Poppler into vendor\poppler ...
        powershell -NoProfile -Command "Expand-Archive -Path '%POPPLER_ZIP%' -DestinationPath '%TEMP%\poppler_extract' -Force"
        for /d %%D in ("%TEMP%\poppler_extract\*") do (
            xcopy /E /I /Y "%%D" "%VENDOR%\poppler" >nul
        )
    ) else (
        echo   FAILED to download Poppler. Get it manually from
        echo   https://github.com/oschwartz10612/poppler-windows/releases
    )
)

echo.
echo Done. Next step: run build_all.bat
echo.
REM Only pause when run interactively; in CI (GitHub sets CI=true) skip the pause.
if "%CI%"=="" pause
exit /b 0
