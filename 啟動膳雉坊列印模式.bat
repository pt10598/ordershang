@echo off
set "CHROME_EXE=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" set "CHROME_EXE=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" (
  echo 找不到 Google Chrome，請先安裝 Chrome。
  pause
  exit /b 1
)
echo 請先完全關閉所有 Chrome 視窗，再按任意鍵啟動免確認列印模式。
pause >nul
start "" "%CHROME_EXE%" --kiosk-printing "https://shrouded-taiga-05447-64720945dfdc.herokuapp.com/admin/print-station"
