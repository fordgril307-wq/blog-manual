"""
test_blog_stats.py — 블로그 통계 수집 단독 테스트
Chrome이 디버그 모드로 켜져 있어야 함.

Chrome 디버그 모드로 실행하는 법:
  chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\blog\chrome_profile"

실행:
  python test_blog_stats.py
"""

import re
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By


CHROME_DEBUG_PORT = 9222

def connect_chrome():
    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{CHROME_DEBUG_PORT}")
    driver = webdriver.Chrome(options=opts)
    print(f"✅ Chrome 연결 성공: {driver.title}")
    return driver


def fetch_blog_daily_stats(driver):
    result = {}
    try:
        print("\n📌 blog.stat.naver.com/m/blog/daily/visit 접속 중...")
        driver.get("https://blog.stat.naver.com/m/blog/daily/visit")
        time.sleep(3)

        def _xpath_int(xp, label=""):
            try:
                el = driver.find_element(By.XPATH, xp)
                txt = el.text.strip().replace(",", "")
                val = int(re.sub(r"[^\d\-]", "", txt))
                print(f"  ✅ {label}: {val}  (원본: '{el.text.strip()}')")
                return val
            except Exception as e:
                print(f"  ❌ {label}: 수집 실패 — {e}")
                return None

        # Step 1: 방문횟수 탭 클릭
        print("\n[Step 1] 방문횟수 탭 클릭...")
        try:
            tab = driver.find_element(By.XPATH,
                "/html/body/div/div/div[2]/div/div[1]/div[2]/div/div[1]/div/nav/div/ul/li[2]/a/span")
            print(f"  탭 텍스트: '{tab.text}'")
            tab.click()
            time.sleep(2)
            print("  ✅ 클릭 완료")
        except Exception as e:
            print(f"  ❌ 탭 클릭 실패: {e}")

        # Step 2: 이전 기간 버튼 클릭
        print("\n[Step 2] 이전 기간 조회 버튼 클릭...")
        try:
            btn = driver.find_element(By.XPATH,
                "/html/body/div/div/div[2]/header/div/div[4]/div/div/div/div/a/span")
            print(f"  버튼 텍스트: '{btn.text}'")
            btn.click()
            time.sleep(2)
            print("  ✅ 클릭 완료")
        except Exception as e:
            print(f"  ❌ 버튼 클릭 실패: {e}")

        # Step 3: 값 수집
        print("\n[Step 3] 전날 확정값 수집...")
        visitors        = _xpath_int("/html/body/div/div/div[2]/div/div[1]/div[1]/div/div/div/div/div[1]/ul/li[1]/a/div/div/strong", "방문자")
        hearts          = _xpath_int("/html/body/div/div/div[2]/div/div[1]/div[1]/div/div/div/div/div[1]/ul/li[2]/a/div/div/strong", "공감")
        comments        = _xpath_int("/html/body/div/div/div[2]/div/div[1]/div[1]/div/div/div/div/div[2]/ul/li[1]/a/div/div/strong", "댓글")
        neighbor_change = _xpath_int("/html/body/div/div/div[2]/div/div[1]/div[1]/div/div/div/div/div[2]/ul/li[2]/a/div/div/strong", "이웃증감")
        views           = _xpath_int("/html/body/div/div/div[2]/div/div[1]/div[2]/div/div[2]/div[2]/div/ul/li/span", "조회수")

        if visitors        is not None: result["visitors"]        = visitors
        if hearts          is not None: result["hearts"]          = hearts
        if comments        is not None: result["comments"]        = comments
        if neighbor_change is not None: result["neighbor_change"] = neighbor_change
        if views           is not None: result["views"]           = views

        # Step 4: 총 이웃수
        print("\n[Step 4] 총 이웃수 수집 (m.blog.naver.com)...")
        try:
            blog_id = "semieeproject"
            driver.get(f"https://m.blog.naver.com/PostList.naver?blogId={blog_id}&tab=1")
            time.sleep(2)
            el = driver.find_element(By.XPATH,
                "/html/body/div[1]/div[3]/div/div[2]/div[2]/div[2]/span[3]")
            m = re.search(r"([\d,]+)명", el.text)
            if m:
                result["neighbor_total"] = int(m.group(1).replace(",", ""))
                print(f"  ✅ 총 이웃수: {result['neighbor_total']}명  (원본: '{el.text.strip()}')")
            else:
                print(f"  ❌ 이웃수 파싱 실패  (원본: '{el.text.strip()}')")
        except Exception as e:
            print(f"  ❌ 총 이웃수 수집 실패: {e}")

    except Exception as e:
        print(f"\n❌ 전체 오류: {e}")

    return result


if __name__ == "__main__":
    print("=" * 50)
    print("블로그 통계 수집 테스트")
    print("=" * 50)

    try:
        driver = connect_chrome()
    except Exception as e:
        print(f"\n❌ Chrome 연결 실패: {e}")
        print("\n👉 Chrome을 디버그 모드로 먼저 실행해주세요:")
        print('   chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\\blog\\chrome_profile"')
        input("\n엔터를 눌러 종료...")
        exit(1)

    result = fetch_blog_daily_stats(driver)

    print("\n" + "=" * 50)
    print("📊 최종 수집 결과:")
    print("=" * 50)
    if result:
        for k, v in result.items():
            print(f"  {k}: {v}")
    else:
        print("  수집된 값 없음")

    input("\n엔터를 눌러 종료...")
