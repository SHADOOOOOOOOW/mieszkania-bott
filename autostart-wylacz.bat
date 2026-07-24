@echo off
chcp 65001 >nul
echo Zatrzymuje i usuwam bota z autostartu...
schtasks /End /TN "MieszkaniaBot" 2>nul
schtasks /Delete /TN "MieszkaniaBot" /F 2>nul
echo.
echo Gotowe. Bot nie bedzie sie juz sam wlaczal.
echo (Aby wlaczyc ponownie autostart: uruchom autostart-wlacz.bat)
pause
