@echo off
rem Start the trading cabinet: double-click this file.
rem Only Latin text here on purpose: cmd.exe breaks Cyrillic in .bat files silently.

rem Work from the folder this file lives in, wherever it was launched from.
cd /d "%~dp0"

rem UTF-8 console, otherwise Russian messages from Python turn into garbage.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

rem Open the browser a few seconds later, when the server is already listening.
rem The pause is a ping to ourselves, not "timeout": a plain "timeout" can
rem resolve to the unrelated GNU tool from Git (rejects "/t"), and the Windows
rem one refuses to run without a keyboard ("Input redirection is not
rem supported"). Both broke the test runs; ping works in every case.
start "" /b cmd /c ""%SystemRoot%\System32\PING.EXE" -n 4 127.0.0.1 >nul & start http://localhost:8765"

python webapp.py

rem Keep the window open so the message above can be read. The wording is
rem neutral on purpose: we get here both when the cabinet was stopped and
rem when it refused to start because another copy is already running, and
rem "has stopped" under "already running" read as a contradiction.
echo.
echo Press any key to close this window.
pause >nul
