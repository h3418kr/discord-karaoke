@echo off
title Discord Karaoke
cd /d "%~dp0"
python app.py
if errorlevel 1 goto fail
exit /b 0

:fail
echo.
echo [ERROR] Failed to start the app.
echo If this is the first time, run the SETUP bat file first.
pause
exit /b 1