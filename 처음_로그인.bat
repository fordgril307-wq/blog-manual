@echo off
chcp 65001 >nul
cd /d C:\blog
echo 크롬이 열리면 claude.ai 에 로그인하세요. 보드가 보이면 그 탭만 닫으면 됩니다.
python collabo_daily.pyw --login
pause
