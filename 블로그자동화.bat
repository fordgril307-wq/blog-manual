@echo off
chcp 65001 >nul
echo GitHub에서 최신 버전 다운로드 중...
curl -fsSL -o "C:\blog\blog_automation_Gemini.pyw" "https://raw.githubusercontent.com/fordgril307-wq/blog-manual/main/blog_automation_Gemini.pyw"
if errorlevel 1 (
    echo [경고] 다운로드 실패 - 기존 파일로 실행합니다.
) else (
    echo 업데이트 완료!
)
cd /d C:\blog
start pythonw.exe blog_automation_Gemini.pyw
exit
