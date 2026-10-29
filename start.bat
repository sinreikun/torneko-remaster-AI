@echo off
cd /d "%~dp0"
py -3 -m autoplayer
if errorlevel 1 pause
