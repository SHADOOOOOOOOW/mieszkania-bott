@echo off
chcp 65001 >nul
set "PY=C:\Users\borys\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe"
if not exist "%PY%" set "PY=pythonw.exe"
schtasks /Create /TN "MieszkaniaBot" /TR "\"%PY%\" \"%~dp0bot.py\"" /SC ONLOGON /RL LIMITED /F
schtasks /Run /TN "MieszkaniaBot"
echo.
echo Gotowe - bot dziala w tle i bedzie startowal przy kazdym logowaniu.
pause
