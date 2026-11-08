@echo off
chcp 65001 >nul
echo 예약 시간을 다시 등록합니다 (블로그 자동화: 새벽 3:00 / 12:40 / 19:40, 콜라보 수집: 6:30)
powershell -ExecutionPolicy Bypass -File "C:\blog\schedule_setup.ps1"
pause
