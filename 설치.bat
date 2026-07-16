@echo off
title Discord Karaoke - Setup
echo =====================================
echo  Discord Karaoke - Library Setup
echo =====================================
echo.

where python >nul 2>nul
if errorlevel 1 goto nopython

echo Python OK. Installing libraries...
echo.
python -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto pipfail

echo.
where ffmpeg >nul 2>nul
if errorlevel 1 goto noffmpeg
echo ffmpeg OK.
goto done

:noffmpeg
echo [NOTE] ffmpeg not found. It is needed for YouTube MR download.
echo        Open cmd and run: winget install ffmpeg
goto done

:nopython
echo [ERROR] Python is not installed.
echo Download: https://www.python.org/downloads/
echo IMPORTANT: check "Add python.exe to PATH" during install!
pause
exit /b 1

:pipfail
echo.
echo [ERROR] Library install failed. Check your internet connection.
pause
exit /b 1

:done
echo.
echo =====================================
echo  Setup complete!
echo  If not done yet: install VB-Audio Virtual Cable, then reboot.
echo  https://vb-audio.com/Cable/
echo  After that, double-click the RUN bat file to start the app.
echo =====================================
pause
exit /b 0