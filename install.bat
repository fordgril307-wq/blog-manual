@echo off
chcp 65001 >nul
echo ============================================
echo  블로그 자동화 - 패키지 설치
echo ============================================
echo.

echo Python 버전 확인 중...
python --version
if errorlevel 1 (
    echo [오류] Python이 설치되어 있지 않습니다.
    echo https://www.python.org 에서 설치 후 다시 실행하세요.
    pause
    exit /b 1
)

echo.
echo 필수 패키지 설치 중...
pip install selenium webdriver-manager google-genai requests

echo.
echo ============================================
echo  설치 완료!
echo  blog_automation_Gemini.pyw 를 더블클릭하면 시작됩니다.
echo  (chromedriver.exe 도 같은 폴더에 있어야 합니다)
echo ============================================
echo.
pause
