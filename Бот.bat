@echo off
rem Start the Telegram menu bot: double-click this file.
rem Only Latin text here on purpose: cmd.exe breaks Cyrillic in .bat files silently.

cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

python bot.py

rem Keep the window open so a message above can be read.
echo.
echo Press any key to close this window.
pause >nul
