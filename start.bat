@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Uruchamiam bota mieszkaniowego... (zamknij okno, aby zatrzymac)
python bot.py
echo.
echo Bot zakonczyl dzialanie.
pause
