# -*- coding: utf-8 -*-
r"""콜라보 보드 체험단 후보 자동 수집 (매일 아침, Claude 없이)

하는 일
  1. 모아뷰 지도 데이터에서 7호선(산곡~논현)·신림선 역 근처 방문형 체험단을 고른다
     (역 700m 안 / 블로그·클립만)
  2. 보드에 이미 있는 건 빼고, 새 후보의 사진을 작게 줄인다
  3. 이 폴더 전용 크롬 창으로 콜라보 보드를 열어 새 후보와 사진을 보드에 넣는다
  - 체험단 사이트 로그인이나 신청은 하지 않는다.
  - 결과는 같은 폴더의 log.txt 에 남는다.

사용
  블로그 자동화와 같은 크롬 프로필(C:\blog\chrome_profile, 디버그 포트 9222)을 그대로 쓴다.
  처음 한 번:  처음_로그인.bat  → 그 크롬에 보드 탭이 열림 → claude.ai 로그인이 안 돼 있으면 로그인 → 탭 닫기
  그다음:     설치.bat 이 매일 06:30 에 자동으로 돌린다 (지금 바로 돌리려면 지금_수집.bat)
"""
import base64, gzip, io, json, math, os, re, sys, time, traceback, datetime, urllib.parse
from pathlib import Path

BASE = Path(__file__).resolve().parent
# 블로그 자동화와 같은 크롬(디버그 포트 9222, C:\blog\chrome_profile)을 같이 쓴다. 이미 켜져 있으면 탭만 하나 열었다 닫는다.
PROFILE = Path(os.environ.get("COLLABO_PROFILE", r"C:\blog\chrome_profile"))
PORT = int(os.environ.get("COLLABO_PORT", "9222"))
CHROME = next((c for c in [r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
               os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")] if os.path.exists(c)), "chrome.exe")
LOG = BASE / "log.txt"
BOARD = "https://claude.ai/artifact/157Ly54Be85vAKoMSwiJkN"
STATIONS = [["산곡", 37.5087, 126.70369], ["부평구청", 37.50747, 126.7211], ["굴포천", 37.50702, 126.73134], ["삼산체육관", 37.5065, 126.74235], ["상동", 37.50584, 126.75317], ["부천시청", 37.5046, 126.7634], ["신중동", 37.50304, 126.77623], ["춘의", 37.50373, 126.7872], ["부천종합운동장", 37.50546, 126.79729], ["까치울", 37.50624, 126.81138], ["온수", 37.4923, 126.82353], ["천왕", 37.48637, 126.83892], ["광명사거리", 37.47942, 126.85446], ["철산", 37.476, 126.8676], ["가산디지털단지", 37.48159, 126.88262], ["남구로", 37.48536, 126.88667], ["대림", 37.49344, 126.89493], ["신풍", 37.50017, 126.90888], ["보라매", 37.49993, 126.92038], ["신대방삼거리", 37.49971, 126.92822], ["장승배기", 37.50502, 126.93928], ["상도", 37.50322, 126.94767], ["숭실대입구", 37.49569, 126.95413], ["남성", 37.48435, 126.97189], ["이수", 37.48545, 126.98211], ["내방", 37.48765, 126.99355], ["고속터미널", 37.50504, 127.00492], ["반포", 37.50817, 127.01163], ["논현", 37.51098, 127.02136], ["샛강", 37.51736, 126.92828], ["대방", 37.51336, 126.92648], ["서울지방병무청", 37.50596, 126.92272], ["보라매", 37.49993, 126.92038], ["보라매공원", 37.49529, 126.91819], ["보라매병원", 37.49296, 126.92427], ["당곡", 37.4897, 126.9278], ["신림", 37.48423, 126.92968], ["서원", 37.47821, 126.93301], ["서울대벤처타운", 37.472, 126.93371], ["관악산", 37.46874, 126.94533]]
CAT = {1010: "맛집", 1015: "카페", 1020: "뷰티/건강", 1030: "숙박", 1040: "문화/체험", 1060: "스포츠", 1070: "생활/편의"}
PLAT = {10: "리뷰노트", 20: "레뷰", 30: "강남맛집", 40: "디너의여왕", 50: "미블", 60: "리뷰플레이스", 70: "서울오빠", 80: "링블",
        90: "놀러와체험단", 110: "체험뷰", 130: "포포몬", 140: "아싸뷰", 150: "클라우드리뷰", 160: "티블", 170: "어디야"}
CH = {100: "블로그", 110: "클립"}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def log(*a):
    line = datetime.datetime.now().strftime("%m/%d %H:%M:%S ") + " ".join(str(x) for x in a)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    try:
        print(line)
    except Exception:
        pass


def http(url, referer="https://moaview.co.kr/", binary=False, tries=3):
    import requests
    for t in range(tries):
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Referer": referer, "X-Requested-With": "XMLHttpRequest"}, timeout=40)
            r.raise_for_status()
            return r.content if binary else r
        except Exception as e:
            code = getattr(getattr(e, "response", None), "status_code", 0) or 0
            if 400 <= code < 500 and code != 429:
                return None
            log("다시 시도", url[:70], e)
            time.sleep(2 + t * 2)
    return None


def as_json(r):
    b = r.content
    try:
        return json.loads(gzip.decompress(b))
    except Exception:
        return r.json()


def dist(a, b, c, d):
    k = math.pi / 180
    return 6371000 * math.hypot((d - b) * k * math.cos(37.5 * k), (c - a) * k)


def collect(known_urls):
    r = http("https://moaview.co.kr/Home/GetCampaignData")
    if r is None:
        raise RuntimeError("모아뷰 지도 데이터를 못 받았어요")
    allc = as_json(r)
    if isinstance(allc, dict):
        allc = next(v for v in allc.values() if isinstance(v, list))
    sel = []
    for x in allc:
        if not x.get("Latitude") or x.get("Channel") not in (100, 110):
            continue
        n, stn = min((dist(s[1], s[2], x["Latitude"], x["Longitude"]), s[0]) for s in STATIONS)
        ad = (x.get("Address1") or "") + " " + (x.get("Address2") or "")
        if n <= 700:
            x["_n"] = (n, stn)
            sel.append(x)
    log("모아뷰 전체", len(allc), "/ 조건 맞음", len(sel))
    out, now = [], int(time.time() * 1000)
    for i in range(0, len(sel), 10):
        chunk = sel[i:i + 10]
        r = http("https://moaview.co.kr/Home/GetCampaignDetail?ids=" + ",".join(str(x["CampaignIdx"]) for x in chunk))
        if r is None:
            continue
        det = as_json(r)
        arr = det if isinstance(det, list) else (det.get("data") or det.get("list") or next((v for v in det.values() if isinstance(v, list)), []))
        dm = {str(d.get("CampaignIdx") or d.get("Idx") or d.get("Id")): d for d in arr}
        for x in chunk:
            d = dm.get(str(x["CampaignIdx"])) or {}
            url = d.get("LinkUrl") or ""
            if not url or url in known_urls:
                continue
            n, stn = x["_n"]
            addr = (d.get("Address") or x.get("Address1") or "").strip()
            if re.match(r"^(충남|충북|전남|전북|경남|경북|강원|제주|대전|대구|부산|광주|울산|세종|충청|전라|경상)", addr) or re.match(r"^\s*\[(충남|충북|전남|전북|경남|경북|강원|제주|대전|대구|부산|광주|울산|세종|충청|전라|경상)", d.get("TitleDisplay") or ""):
                log("좌표·주소 안 맞음, 건너뜀", (d.get("TitleDisplay") or "")[:30], addr[:20])
                continue
            cat = CAT.get(x.get("CategoryID"), "기타")
            ad = x.get("Address1") or ""
            raw = str(d.get("ApplyEndDT") or "")
            m = re.search(r"Date\((\d+)", raw)
            if m:
                due = time.strftime("%Y-%m-%d", time.gmtime(int(m.group(1)) / 1000 + 9 * 3600))
            elif raw[:4].isdigit():
                due = raw[:10]
            else:
                due = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 9 * 3600 + 86400 * int(x.get("ApplyDay") or 0)))
            match = [f"{stn}역 {int(n)}m"] if n <= 1000 else []
            if "여의도" in ad + (x.get("Address2") or ""):
                match.append("여의도")
            if "인천 부평" in ad:
                match.append("인천 부평")
            out.append({"id": "mv-" + str(x["CampaignIdx"]), "site": PLAT.get(x.get("PlatformID"), "기타"),
                        "t": d.get("TitleDisplay") or d.get("Title") or "", "url": url, "type": "방문형",
                        "ch": [CH.get(x["Channel"], "블로그")], "region": d.get("Address") or ad,
                        "offer": d.get("Offer") or x.get("Offer") or "", "point": str(x.get("Point") or "") if x.get("Point") else "",
                        "due": due, "match": match,
                        "warn": ["방문 요일·시간(평일 18시 이후/주말) 확인"] + (["얼굴 노출 여부 확인"] if cat == "뷰티/건강" else []),
                        "apply": d.get("Apply", x.get("Apply")), "limit": d.get("Limit", x.get("Limit")), "rate": x.get("ApplyRate"),
                        "lat": x["Latitude"], "lng": x["Longitude"], "nearOK": True, "nearSt": stn, "nearM": int(n),
                        "cat2": cat, "sub": x.get("Category") or "", "st": "new", "found": now, "via": "자동 수집(pyw)",
                        "_img": d.get("ImgUrl") or ""})
        time.sleep(0.3)
    return out


def thumb(url):
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    from PIL import Image
    import urllib.parse
    host = url.split("/")[2] if "//" in url else ""
    for src in (url, "https://wsrv.nl/?url=" + urllib.parse.quote(url, safe="") + "&w=320&output=jpg"):
        b = http(src, referer="https://" + host + "/", binary=True, tries=2)
        if not b:
            continue
        try:
            im = Image.open(io.BytesIO(b)).convert("RGB")
            w, h = im.size
            s = min(w, h)
            im = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s)).resize((160, 160), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=72, optimize=True)
            return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception as e:
            log("사진 변환 실패", url[:60], e)
    return ""


HEAVY_DAYS = 3  # 네이버 별점·방문 시간 읽기는 3일에 한 번 (노트북 부담 줄이기)
NV_MAX = 240  # 3일에 한 번 확인할 가게 수 (네이버에 부담 안 주게)


def nv_name(t):
    t = re.sub(r"^\s*\[[^\]]*\]\s*", "", t or "")
    t = re.sub(r"[_(\[].*$", "", t)
    t = re.sub(r"(체험단|평일|주말|점심|저녁|리뷰|블로그|클립|모집).*$", "", t)
    return t.strip()


def nv_num(txt, pat):
    m = re.search(pat, txt)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except Exception:
        return None


def nv_lookup(page, c):
    """네이버 모바일 검색 → 첫 플레이스 → 홈 화면 글자에서 별점·리뷰 수만 읽는다(로그인 없음)."""
    name = nv_name(c.get("t"))
    if not name:
        return {"ok": False, "at": int(time.time() * 1000)}
    m0 = re.match(r"^\s*\[([^\]]*)\]", c.get("t") or "")
    area = m0.group(1) if m0 else ""
    q = (area.split()[-1] + " " if area else "") + name
    page.goto("https://m.search.naver.com/search.naver?query=" + urllib.parse.quote(q), wait_until="domcontentloaded", timeout=30000)
    time.sleep(1.5)
    pid = None
    for a in page.query_selector_all("a[href*='place.naver.com']"):
        h = a.get_attribute("href") or ""
        m = re.search(r"place\.naver\.com/(?:[a-z]+/)?(\d{5,})", h)
        if m:
            pid = m.group(1)
            break
    if not pid:
        return {"ok": False, "q": q, "at": int(time.time() * 1000)}
    page.goto(f"https://m.place.naver.com/place/{pid}/home", wait_until="domcontentloaded", timeout=30000)
    time.sleep(2)
    txt = page.inner_text("body")[:6000]
    title = (page.title() or "").split(":")[0].strip()
    r = nv_num(txt, r"(?:별점|★)\s*([0-5]\.\d{1,2})")
    vr = nv_num(txt, r"방문자\s*리뷰\s*([\d,]+)")
    br = nv_num(txt, r"블로그\s*리뷰\s*([\d,]+)")
    out = {"ok": True, "pid": pid, "name": title[:40], "at": int(time.time() * 1000)}
    if re.search(r"주차\s*(가능|무료|지원|완비)|발렛|주차장\s*(있|보유|완비)", txt) and not re.search(r"주차\s*(불가|없|어려)", txt):
        out["park"] = True
    if r is not None:
        out["r"] = r
    if vr is not None:
        out["vr"] = int(vr)
    if br is not None:
        out["br"] = int(br)
    return out


def naver_ratings(ctx, fr):
    try:
        need = fr.evaluate(f"() => window.cbNvNeed ? window.cbNvNeed({NV_MAX}) : []")
    except Exception as e:
        log("네이버 평점: 보드가 아직 옛 버전", e)
        return
    if not need:
        log("네이버 평점: 확인할 가게 없음")
        return
    pg = ctx.new_page()
    got = {}
    try:
        for i, c in enumerate(need):
            try:
                got[c["id"]] = nv_lookup(pg, c)
            except Exception as e:
                log("네이버 평점 실패", c.get("t", "")[:30], e)
            time.sleep(1.5 + (i % 3))
    finally:
        try:
            pg.close()
        except Exception:
            pass
    if got:
        n = fr.evaluate("m => window.cbNvSave(m)", got)
        ok = sum(1 for v in got.values() if v.get("ok"))
        log(f"네이버 평점 저장 {n}곳 (찾음 {ok})")


VI_MAX = 180  # 3일에 한 번 방문 시간 안내를 읽을 캠페인 수
VI_KEY = re.compile(r"(평일|주말|요일|오전|오후|저녁|점심|런치|디너|[0-2]?\d\s*시|\d{1,2}:\d{2}|방문\s*가능|체험\s*가능|예약\s*가능|영업)")

VI_HEAD = re.compile(r"^[\[\(<★◇■▶·\-\s]*(방문\s*및\s*예약|방문\s*(정보|안내|가능|일정)|체험\s*(가능|시간|안내|일정|방법)|예약\s*(안내|방법|가능)|이용\s*(시간|가능)|운영\s*시간|영업\s*시간|체험가능)")
VI_STOP = re.compile(r"^[\[\(<★◇■▶·\-\s]*(리뷰|키워드|블로그|가이드|미션|주의\s*사항|유의\s*사항|NOTICE|제공\s*내역|포스팅|콘텐츠|캠페인\s*(정보|미션|일정)|신청|선정|추천\s*캠페인|연관\s*리뷰|플레이스|위젯)")


def vi_extract(txt):
    """캠페인 페이지에서 방문·예약 안내 부분을 손대지 않고 원문 그대로 뽑는다(줄바꿈 유지, 최대 600자)."""
    ls = [l.strip() for l in re.split(r"[\r\n]+", txt) if l.strip()]
    out, seen = [], set()
    i = 0
    while i < len(ls):
        l = ls[i]
        if len(l) <= 40 and VI_HEAD.search(l):
            block = [l]
            for k in range(i + 1, min(len(ls), i + 16)):
                nl = ls[k]
                if len(nl) <= 25 and VI_STOP.search(nl):
                    break
                block.append(nl)
            for b in block:
                if b not in seen and len(b) <= 200:
                    seen.add(b)
                    out.append(b)
            i += len(block)
            continue
        i += 1
    if not out:  # 제목이 없는 사이트: 시간·요일 문장만
        for l in ls:
            if 3 <= len(l) <= 120 and VI_KEY.search(l) and re.search(r"(시간|평일|주말|요일|시~|\d:\d\d|휴무)", l) and l not in seen:
                seen.add(l)
                out.append(l)
    park = [l for l in ls if "주차" in l and len(l) <= 80 and l not in seen][:2]
    t = "\n".join(out + park)
    return t[:600]


def ap_extract(txt):
    """캠페인 페이지에 적힌 실제 신청(모집) 기간 원문. 모아뷰 마감일이 틀릴 때 보드가 이걸로 바로잡는다."""
    ls = [l.strip() for l in re.split(r"[\r\n]+", txt) if l.strip()]
    dre = re.compile(r"\d{1,2}\s*[./월-]\s*\d{1,2}")
    for i, l in enumerate(ls):
        if len(l) <= 60 and re.search(r"(신청|모집|응모)\s*기간", l):
            seg = l if dre.search(l) else (l + " " + ls[i + 1] if i + 1 < len(ls) else l)
            if dre.search(seg):
                return seg[:80]
    return ""


def visit_info(ctx, fr):
    """캠페인 상세 페이지(공개)에서 방문 가능 시간 관련 문장만 짧게 뽑아 보드에 저장. 로그인·신청 없음."""
    try:
        need = fr.evaluate(f"() => window.cbViNeed ? window.cbViNeed({VI_MAX}) : []")
    except Exception as e:
        log("방문 시간: 보드가 아직 옛 버전", e)
        return
    if not need:
        log("방문 시간: 확인할 캠페인 없음")
        return
    pg = ctx.new_page()
    got = {}
    try:
        for i, c in enumerate(need):
            try:
                pg.goto(c["url"], wait_until="domcontentloaded", timeout=30000)
                txt = ""
                for _ in range(8):  # 리뷰노트처럼 늦게 그려지는 페이지는 내용이 찰 때까지 기다림
                    time.sleep(1.5)
                    txt = pg.inner_text("body")
                    if len(txt) > 1500:
                        break
                got[c["id"]] = {"t": vi_extract(txt), "ap": ap_extract(txt), "v": 2, "at": int(time.time() * 1000)}
            except Exception as e:
                log("방문 시간 읽기 실패", c.get("url", "")[:50], e)
            time.sleep(1 + (i % 2))
    finally:
        try:
            pg.close()
        except Exception:
            pass
    if got:
        n = fr.evaluate("m => window.cbViSave(m)", got)
        log(f"방문 시간 저장 {n}곳")


def open_board(page):
    """같은 크롬을 블로그 자동화도 쓰니까, 다른 프로그램이 탭을 다른 주소로 바꿔도 다시 연다."""
    for t in range(6):
        try:
            if "157Ly54Be85vAKoMSwiJkN" not in (page.url or ""):
                page.goto(BOARD, wait_until="domcontentloaded")
            return
        except Exception as e:
            log("보드 다시 여는 중", str(e).splitlines()[0][:80])
            time.sleep(5 + t * 5)
    raise RuntimeError("보드 탭을 계속 다른 프로그램이 가져가요")


def board_frame(page, timeout=180):
    open_board(page)
    end = time.time() + timeout
    while time.time() < end:
        if "157Ly54Be85vAKoMSwiJkN" not in (page.url or ""):
            open_board(page)
        for fr in page.frames:
            try:
                st = fr.evaluate("typeof window.cbKnown==='function' ? window.cbKnown() : null")
            except Exception:
                st = None
            if st and st.get("ready"):
                return fr, st
        time.sleep(2)
    raise RuntimeError("보드를 못 열었어요. 처음_로그인.bat 으로 claude.ai 로그인이 됐는지 확인해 주세요.")


def connect(p):
    """9222 포트로 켜진 크롬에 붙는다. 꺼져 있으면 블로그 자동화와 같은 방식으로 켠다."""
    import subprocess
    url = f"http://127.0.0.1:{PORT}"
    for t in range(2):
        try:
            return p.chromium.connect_over_cdp(url)
        except Exception:
            if t == 0:
                PROFILE.mkdir(parents=True, exist_ok=True)
                subprocess.Popen([CHROME, f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}", "--no-first-run"])
                log("크롬을 켰어요", PROFILE)
                time.sleep(6)
    raise RuntimeError("크롬(9222)에 연결 못 했어요")


def main():
    from playwright.sync_api import sync_playwright
    login = "--login" in sys.argv
    with sync_playwright() as p:
        browser = connect(p)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        if login:
            page.goto(BOARD)
            log("보드 탭을 열었어요. claude.ai 로그인이 안 돼 있으면 그 탭에서 로그인하고, 보드가 보이면 탭만 닫으세요(크롬은 닫지 않아도 돼요).")
            return
        try:
            run(page)
            heavy_file = BASE / "last_heavy.txt"
            try:
                last = float(heavy_file.read_text().strip())
            except Exception:
                last = 0
            if "--heavy" in sys.argv or time.time() - last >= HEAVY_DAYS * 86400 - 3600:
                log(f"무거운 작업 날 ({HEAVY_DAYS}일마다): 네이버 별점·방문 시간·주차")
                try:
                    fr, _ = board_frame(page)
                    naver_ratings(ctx, fr)
                except Exception as e:
                    log("네이버 평점 단계 오류", e)
                try:
                    fr, _ = board_frame(page)
                    visit_info(ctx, fr)
                except Exception as e:
                    log("방문 시간 단계 오류", e)
                try:
                    heavy_file.write_text(str(time.time()))
                except Exception:
                    pass
            else:
                left = HEAVY_DAYS - int((time.time() - last) // 86400)
                log(f"오늘은 새 후보만 받았어요 (별점·방문 시간은 {left}일 뒤)")
        finally:
            try:
                page.close()
            except Exception:
                pass



def run(page):
        fr, st = board_frame(page)
        if not st.get("canWrite"):
            raise RuntimeError("이 계정은 보드에 쓸 권한이 없어요")
        pend = BASE / "pending_import.json"
        if pend.exists():
            try:
                pj = json.loads(pend.read_text(encoding="utf-8"))
                res = fr.evaluate("p => window.cbImport(p)", pj)
                log("지난번에 못 넣은 후보 넣음", res)
                pend.unlink(missing_ok=True)
                fr, st = board_frame(page)
            except Exception as e:
                log("지난번 후보 넣기 실패", str(e).splitlines()[0][:80])
        known = set(st.get("urls") or [])
        new = collect(known)
        known_ids = set(st.get("ids") or [])
        new = [c for c in new if c["id"] not in known_ids]
        log("새 후보", len(new))
        thumbs = {}
        for c in new:
            u = c.pop("_img", "")
            d = thumb(u)
            if d:
                thumbs[c["id"]] = d
        log("사진", len(thumbs))
        if new:
            pend = BASE / "pending_import.json"
            pend.write_text(json.dumps({"cands": new, "thumbs": thumbs}, ensure_ascii=False), encoding="utf-8")
            for t in range(3):
                try:
                    fr, st = board_frame(page)
                    res = fr.evaluate("p => window.cbImport(p)", {"cands": new, "thumbs": thumbs})
                    log("보드에 넣음", res)
                    pend.unlink(missing_ok=True)
                    break
                except Exception as e:
                    log("보드에 넣기 다시 시도", str(e).splitlines()[0][:80])
                    time.sleep(10)
        else:
            log("새 후보 없음")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("오류:", e)
        log(traceback.format_exc())
