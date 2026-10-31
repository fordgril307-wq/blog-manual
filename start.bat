@echo off
chcp 65001 >nul
echo ============================================
echo  블로그 자동화 - 최신 버전 업데이트 및 실행
echo ============================================
echo.

echo GitHub에서 최신 버전 다운로드 중...
curl -fsSL -o "C:\blog\blog_automation_Gemini.pyw" "https://raw.githubusercontent.com/fordgril307-wq/blog-manual/main/blog_automation_Gemini.pyw"

if errorlevel 1 (
    echo [경고] 다운로드 실패 - 기존 파일로 실행합니다.
) else (
    echo 업데이트 완료!
)

echo.
echo 프로그램 실행 중...
cd /d C:\blog
start pythonw.exe blog_automation_Gemini.pyw
exit
