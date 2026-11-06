@echo off
rem 작업 스케줄러가 부르는 예약 실행: 최신 코드 받고 블로그 자동화를 자동 시작 모드로 켬
cd /d C:\blog
git pull -q
start "" pythonw.exe blog_automation_Gemini.pyw --auto
