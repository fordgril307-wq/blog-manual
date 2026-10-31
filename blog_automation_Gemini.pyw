import random
import time
import tkinter as tk
from tkinter import messagebox
import threading
import queue
import sys
import io
import os
import json
import requests

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.alert import Alert
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from google import genai
from google.genai import types as genai_types
from urllib.parse import quote

# =============================================
# 전역 상태
# =============================================
gemini_model = None
shared_driver = None   # 재사용 가능한 전역 드라이버
_worker_cfg   = {}     # 현재 작업 설정 (add_neighbor, neighbor_msg 등)

# 미발송 보고서 파일 (드라이버 죽어도 다음 시작 때 발송)
_PENDING_REPORT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pending_report.json")

# ── Chrome debuggerAddress 연결 설정 ──
CHROME_DEBUG_PORT  = 9222
CHROME_PROFILE_DIR = r"C:\blog\chrome_profile"
CHROME_EXE         = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

import subprocess as _sp2
from flask import Flask as _Flask, Response as _Response, jsonify as _jsonify, request as _flask_req
import json as _json_mod
import collections as _collections

def _launch_chrome_if_needed():
    """디버그 포트가 열려있지 않으면 Chrome을 직접 실행"""
    import urllib.request as _ur2
    try:
        _ur2.urlopen(f"http://127.0.0.1:{CHROME_DEBUG_PORT}/json", timeout=2)
        return True   # 이미 실행 중
    except Exception:
        pass
    _sp2.Popen([CHROME_EXE,
                f"--remote-debugging-port={CHROME_DEBUG_PORT}",
                f"--user-data-dir={CHROME_PROFILE_DIR}"])
    time.sleep(3)
    return False

def _clear_wdm_lock():
    """비정상 종료로 남은 webdriver-manager 락 파일 자동 삭제"""
    try:
        import glob as _glob
        wdm_dir = os.path.join(os.path.expanduser("~"), ".wdm")
        for lock_file in _glob.glob(os.path.join(wdm_dir, ".wdm-lock-*")):
            try:
                os.remove(lock_file)
            except Exception:
                pass
    except Exception:
        pass

def _connect_chrome():
    """debuggerAddress로 이미 실행 중인 Chrome에 붙기"""
    _clear_wdm_lock()  # 락 파일 잔재 정리
    opts = webdriver.ChromeOptions()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{CHROME_DEBUG_PORT}")
    opts.add_argument("--disable-background-timer-throttling")
    opts.add_argument("--disable-backgrounding-occluded-windows")
    opts.add_argument("--disable-renderer-backgrounding")
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _local_driver = os.path.join(_script_dir, "chromedriver.exe")
    if os.path.isfile(_local_driver):
        drv = webdriver.Chrome(service=Service(_local_driver), options=opts)
    else:
        drv = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    return drv

def _is_logged_in(driver):
    """NID_AUT 쿠키로 네이버 로그인 상태 확인"""
    try:
        driver.get("https://www.naver.com")
        time.sleep(1)
        cookies = {c['name']: c['value'] for c in driver.get_cookies()}
        return 'NID_AUT' in cookies
    except Exception:
        return False

def _get_my_blog_id(driver):
    """로그인된 네이버 계정의 본인 블로그 ID 추출 (본인 게시글 스킵용)"""
    try:
        driver.get("https://m.blog.naver.com/")
        time.sleep(2)
        # 방법1: LNB 내 블로그 링크에서 blogId= 파라미터 추출
        # 예: href="https://m.blog.naver.com/PostList.naver?blogId=semieeproject"
        try:
            el = driver.find_element(By.XPATH, '//a[contains(@href,"blogId=") and (contains(@data-click-area,"myblog") or contains(@data-nlog-params,"my_blog") or contains(@href,"my_blog"))]')
            href = el.get_attribute('href') or ''
            import re as _re
            m = _re.search(r'blogId=([^&]+)', href)
            if m:
                return m.group(1)
        except: pass
        # 방법2: data-click-area="lnb.myblog" 속성으로 찾기
        try:
            el = driver.find_element(By.XPATH, '//a[@data-click-area="lnb.myblog"]')
            href = el.get_attribute('href') or ''
            import re as _re
            m = _re.search(r'blogId=([^&]+)', href)
            if m:
                return m.group(1)
        except: pass
        # 방법3: 기존 href 경로 방식 (폴백)
        for xp in [
            '//a[contains(@href,"blog.naver.com") and contains(@class,"MobileMyInfo")]',
            '//*[@id="gnb"]//*[contains(@href,"blog.naver.com")]',
            '//a[contains(@href,"/blog.naver.com/") and not(contains(@href,"FeedList")) and not(contains(@href,"Search"))]',
        ]:
            try:
                el = driver.find_element(By.XPATH, xp)
                href = el.get_attribute('href') or ''
                bid = href.rstrip('/').split('/')[-1].split('?')[0]
                if bid and bid not in ('', 'blog.naver.com', 'm.blog.naver.com'):
                    return bid
            except: pass
        # 대안: 네이버 메인에서 내 블로그 링크 추출
        driver.get("https://www.naver.com")
        time.sleep(1)
        for xp in [
            '//a[contains(@href,"blog.naver.com") and contains(text(),"블로그")]',
            '//a[@class="MyView-module__link_my_menu___HaTvB" and contains(@href,"blog.naver.com")]',
            '//*[contains(@class,"MyView")]//a[contains(@href,"blog.naver.com")]',
        ]:
            try:
                el = driver.find_element(By.XPATH, xp)
                href = el.get_attribute('href') or ''
                bid = href.rstrip('/').split('blog.naver.com/')[-1].split('?')[0].strip('/')
                if bid and '.' not in bid and bid not in ('', 'blog.naver.com'):
                    return bid
            except: pass
    except Exception as e:
        glog(f"  [본인 ID 추출 실패] {e}", "dim")
    return None

def _wait_for_login(driver):
    """로그인될 때까지 30초마다 NID_AUT 쿠키 확인 (정지 시 탈출)"""
    glog("  로그인 대기 중 — 크롬에서 네이버 로그인 해주세요.", "warn")
    _tg_send("🔑 네이버 로그인이 필요합니다. 크롬에서 로그인 해주세요.")
    while not stop_event.is_set():
        time.sleep(30)
        if _is_logged_in(driver):
            glog("✅ 로그인 확인 — 작업 재개", "success")
            _tg_send("✅ 로그인 확인 — 작업 재개")
            return
        glog("  아직 로그인 안 됨, 30초 후 재확인...", "dim")

# GUI 큐 (로그 메시지 전달용)
log_queue = queue.Queue()
stats = {"hearts": 0, "comments": 0, "blocks": 0, "skips": 0, "neighbors": 0, "running": False}
_neighbor_limit_reached = False  # 이웃 5000명 한도 도달 시 True → 이후 이웃추가 전부 스킵
_my_blog_id = None  # 로그인된 본인 블로그 ID (본인 게시글 댓글 스킵용)

# =============================================
# 웹 대시보드 전역
# =============================================
_web_log_deque  = _collections.deque(maxlen=500)   # 최근 500줄 로그 보관
_weekly_file    = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".weekly_stats.json")
_flask_app      = _Flask(__name__)
_web_ctrl_queue = queue.Queue()   # 웹 버튼 → tkinter 명령 전달

def _weekly_load():
    try:
        if os.path.exists(_weekly_file):
            with open(_weekly_file, encoding="utf-8") as f:
                return _json_mod.load(f)
    except Exception:
        pass
    return {}

def _weekly_save(data):
    try:
        with open(_weekly_file, "w", encoding="utf-8") as f:
            _json_mod.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _weekly_add(hearts, comments, neighbors):
    data = _weekly_load()
    today = time.strftime("%Y-%m-%d")
    week_key = time.strftime("%Y-W%W")
    if week_key not in data:
        data[week_key] = {}
    day = data[week_key].get(today, {"hearts": 0, "comments": 0, "neighbors": 0})
    day["hearts"]    += hearts
    day["comments"]  += comments
    day["neighbors"] += neighbors
    data[week_key][today] = day
    _weekly_save(data)

def _web_append_log(msg, tag="info"):
    ts = time.strftime("%H:%M:%S")
    _web_log_deque.append({"t": ts, "msg": msg, "tag": tag})

# ── Flask 라우트 ─────────────────────────────────────────────────

@_flask_app.route("/")
def _dash_index():
    return _Response(_DASH_HTML, mimetype="text/html")

@_flask_app.route("/api/stats")
def _api_stats():
    weekly = _weekly_load()
    week_key = time.strftime("%Y-W%W")
    week_data = weekly.get(week_key, {})
    days = sorted(week_data.items())
    return _jsonify({
        "stats": {
            "hearts":    stats["hearts"],
            "comments":  stats["comments"],
            "neighbors": stats["neighbors"],
            "blocks":    stats["blocks"],
            "skips":     stats["skips"],
            "running":   stats["running"],
        },
        "weekly": days,
        "rest_log":  list(_rest_log),
        "block_log": list(_block_log),
    })

@_flask_app.route("/api/logs")
def _api_logs():
    since = int(_flask_req.args.get("since", 0))
    logs = list(_web_log_deque)
    return _jsonify({"logs": logs[since:], "total": len(logs)})

@_flask_app.route("/api/control", methods=["POST"])
def _api_control():
    cmd = (_flask_req.json or {}).get("cmd", "")
    if cmd in ("start", "stop", "resume"):
        _web_ctrl_queue.put(cmd)
        return _jsonify({"ok": True, "cmd": cmd})
    return _jsonify({"ok": False, "error": "unknown cmd"}), 400

# ── 대시보드 HTML ────────────────────────────────────────────────
_SPONSORS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sponsors.json")

def _sponsors_load():
    try:
        with open(_SPONSORS_FILE, encoding="utf-8") as f:
            return _json_mod.load(f)
    except Exception:
        return []

def _sponsors_save(data):
    try:
        with open(_SPONSORS_FILE, "w", encoding="utf-8") as f:
            _json_mod.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

@_flask_app.route("/api/sponsors", methods=["GET"])
def _api_sponsors_get():
    return _jsonify(_sponsors_load())

@_flask_app.route("/api/sponsors", methods=["POST"])
def _api_sponsors_post():
    data = _flask_req.get_json(force=True)
    sponsors = _sponsors_load()
    new_id = str(int(__import__('time').time() * 1000))
    data["id"] = new_id
    sponsors.append(data)
    _sponsors_save(sponsors)
    return _jsonify({"ok": True, "id": new_id})

@_flask_app.route("/api/sponsors/<sid>", methods=["PUT"])
def _api_sponsors_put(sid):
    data = _flask_req.get_json(force=True)
    sponsors = _sponsors_load()
    for i, s in enumerate(sponsors):
        if s.get("id") == sid:
            data["id"] = sid
            sponsors[i] = data
            _sponsors_save(sponsors)
            return _jsonify({"ok": True})
    return _jsonify({"ok": False, "msg": "not found"}), 404

@_flask_app.route("/api/sponsors/<sid>", methods=["DELETE"])
def _api_sponsors_delete(sid):
    sponsors = _sponsors_load()
    sponsors = [s for s in sponsors if s.get("id") != sid]
    _sponsors_save(sponsors)
    return _jsonify({"ok": True})

_DASH_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>블로그봇 대시보드</title>
<style>
  :root{--bg:#f0f4f0;--card:#fff;--border:#d8e8d8;--accent:#4caf50;
    --text:#222;--dim:#888;--log-bg:#1a1f1a;--log-fg:#b8d8b8;--tab-active:#4caf50;--tab-bar:#e8f0e8;}
  @media(prefers-color-scheme:dark){:root{--bg:#111a11;--card:#1e271e;--border:#2a3d2a;--text:#ddd;--dim:#666;--tab-bar:#1a251a;}}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;padding:0 0 40px}
  header{background:var(--card);border-bottom:1px solid var(--border);padding:14px 20px;
    display:flex;align-items:center;gap:10px;position:sticky;top:0;z-index:10}
  header h1{font-size:17px;font-weight:700;color:var(--accent)}
  #sdot{width:10px;height:10px;border-radius:50%;background:#aaa;flex-shrink:0}
  #sdot.running{background:#4caf50;box-shadow:0 0 6px #4caf50}
  #sdot.stopped{background:#e53935}
  #stxt{font-size:12px;color:var(--dim)}
  /* 탭 */
  .tab-bar{background:var(--tab-bar);border-bottom:1px solid var(--border);display:flex;gap:0;position:sticky;top:49px;z-index:9}
  .tab-btn{padding:10px 22px;font-size:13px;font-weight:700;border:none;background:none;cursor:pointer;color:var(--dim);border-bottom:3px solid transparent}
  .tab-btn.active{color:var(--accent);border-bottom-color:var(--accent)}
  .tab-pane{display:none}.tab-pane.active{display:block}
  .main{max-width:1060px;margin:0 auto;padding:18px 14px;display:flex;flex-direction:column;gap:14px}
  .card{background:var(--card);border:1px solid var(--border);border-radius:11px;padding:14px 16px}
  .ctitle{font-size:11px;color:var(--dim);font-weight:700;letter-spacing:.06em;text-transform:uppercase;margin-bottom:10px}
  .row{display:flex;gap:14px;flex-wrap:wrap}
  .stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px}
  .sb{background:var(--card);border:1px solid var(--border);border-radius:9px;padding:12px 14px}
  .si{font-size:20px}.sv{font-size:26px;font-weight:800;color:var(--accent);line-height:1.1}
  .sl{font-size:11px;color:var(--dim)}
  .btn-row{display:flex;gap:9px;flex-wrap:wrap}
  .btn{padding:9px 20px;border:none;border-radius:7px;font-size:13px;font-weight:700;cursor:pointer;transition:.15s}
  .bs{background:#4caf50;color:#fff}.bs:hover{background:#388e3c}
  .bx{background:#e53935;color:#fff}.bx:hover{background:#b71c1c}
  .br{background:#f57c00;color:#fff}.br:hover{background:#e65100}
  .btn-sm{padding:5px 12px;font-size:12px;border-radius:6px}
  .btn-blue{background:#1565c0;color:#fff}.btn-blue:hover{background:#0d47a1}
  .btn-gray{background:#888;color:#fff}.btn-gray:hover{background:#555}
  .btn-red{background:#e53935;color:#fff}.btn-red:hover{background:#b71c1c}
  table{width:100%;border-collapse:collapse;font-size:12px}
  th{text-align:left;padding:6px 8px;color:var(--dim);font-weight:600;border-bottom:2px solid var(--border)}
  td{padding:6px 8px;border-bottom:1px solid var(--border);vertical-align:middle}
  tr:last-child td{border:none}
  .trow td{font-weight:700;color:var(--accent)}
  #log-box{background:var(--log-bg);color:var(--log-fg);border-radius:9px;
    padding:12px;height:340px;overflow-y:auto;font-family:'Consolas','Courier New',monospace;font-size:11.5px;line-height:1.75}
  .lt{color:#446644;margin-right:5px}
  .tag-success{color:#4caf50}.tag-warn{color:#f57c00}.tag-error{color:#ef5350}
  .tag-dim{color:#555}.tag-header{color:#64b5f6;font-weight:700}.tag-info{color:#b8d8b8}
  .mlog{max-height:130px;overflow-y:auto;font-size:12px}
  .mr{display:flex;gap:7px;padding:3px 0;border-bottom:1px solid var(--border)}
  .mr:last-child{border:none}.mt{color:var(--dim);flex-shrink:0}
  /* 협찬 상태 뱃지 */
  .badge{display:inline-block;padding:2px 9px;border-radius:20px;font-size:11px;font-weight:700}
  .badge-active{background:#e8f5e9;color:#2e7d32}
  .badge-done{background:#e3f2fd;color:#1565c0}
  .badge-wait{background:#fff8e1;color:#f57c00}
  /* 협찬 폼 */
  .sp-form{display:flex;flex-direction:column;gap:9px;padding:14px;background:var(--card);
    border:1px solid var(--border);border-radius:11px;margin-bottom:14px}
  .sp-form .fr{display:flex;gap:9px;flex-wrap:wrap}
  .sp-form input,.sp-form select,.sp-form textarea{
    background:var(--bg);border:1px solid var(--border);border-radius:7px;
    padding:7px 10px;font-size:13px;color:var(--text);width:100%}
  .sp-form textarea{height:60px;resize:vertical;font-family:inherit}
  .sp-form label{font-size:11px;color:var(--dim);font-weight:700;margin-bottom:2px}
  .fi{display:flex;flex-direction:column;gap:3px;flex:1;min-width:150px}
  @media(max-width:580px){.sv{font-size:20px}#log-box{height:240px}.tab-btn{padding:9px 12px;font-size:12px}}
</style>
</head>
<body>
<header>
  <div id="sdot"></div>
  <h1>🌿 블로그봇 대시보드</h1>
  <span id="stxt">로딩중...</span>
  <span style="margin-left:auto;font-size:11px;color:var(--dim)" id="upd"></span>
</header>
<div class="tab-bar">
  <button class="tab-btn active" onclick="switchTab('dash')">📊 대시보드</button>
  <button class="tab-btn" onclick="switchTab('sponsor')">🤝 협찬 관리</button>
</div>

<!-- ── 대시보드 탭 ── -->
<div id="tab-dash" class="tab-pane active">
<div class="main">
  <div class="card">
    <div class="ctitle">제어</div>
    <div class="btn-row">
      <button class="btn bs" onclick="ctrl('start')">▶ 시작</button>
      <button class="btn bx" onclick="ctrl('stop')">⏹ 중지</button>
      <button class="btn br" onclick="ctrl('resume')">⏯ 재개</button>
    </div>
    <p style="margin-top:9px;font-size:11px;color:var(--dim)">⚠ 시작은 GUI에서 설정 후 누르거나, 웹 버튼은 이전 설정을 재사용합니다.</p>
  </div>
  <div>
    <div class="ctitle" style="padding:0 4px 7px">현재 세션</div>
    <div class="stat-grid">
      <div class="sb"><div class="si">🩷</div><div class="sv" id="sh">-</div><div class="sl">하트</div></div>
      <div class="sb"><div class="si">💬</div><div class="sv" id="sc">-</div><div class="sl">댓글</div></div>
      <div class="sb"><div class="si">➕</div><div class="sv" id="sn">-</div><div class="sl">이웃추가</div></div>
      <div class="sb"><div class="si">⛔</div><div class="sv" id="sb2">-</div><div class="sl">차단</div></div>
      <div class="sb"><div class="si">⏭</div><div class="sv" id="ss">-</div><div class="sl">스킵</div></div>
    </div>
  </div>
  <div class="row">
    <div class="card" style="flex:1;min-width:240px">
      <div class="ctitle">이번 주 누적</div>
      <table>
        <thead><tr><th>날짜</th><th>🩷</th><th>💬</th><th>➕</th></tr></thead>
        <tbody id="wtb"><tr><td colspan="4" style="color:var(--dim);text-align:center;padding:8px">데이터 없음</td></tr></tbody>
        <tfoot><tr style="font-weight:700;color:var(--accent)">
          <td>합계</td><td id="wh">0</td><td id="wc">0</td><td id="wn">0</td>
        </tr></tfoot>
      </table>
    </div>
    <div class="card" style="flex:1;min-width:200px">
      <div class="ctitle">휴식 기록</div>
      <div class="mlog" id="rlog"><span style="color:var(--dim);font-size:12px">없음</span></div>
    </div>
  </div>
  <div class="card">
    <div class="ctitle" style="display:flex;justify-content:space-between">
      <span>실시간 로그</span>
      <label style="font-size:11px;color:var(--dim);display:flex;align-items:center;gap:4px;font-weight:400;text-transform:none;letter-spacing:0">
        <input type="checkbox" id="asc" checked> 자동 스크롤
      </label>
    </div>
    <div id="log-box"></div>
  </div>
</div>
</div>

<!-- ── 협찬 관리 탭 ── -->
<div id="tab-sponsor" class="tab-pane">
<div class="main">
  <div id="sp-form-wrap" class="sp-form">
    <div class="ctitle" id="sp-form-title">새 협찬 추가</div>
    <input type="hidden" id="sp-edit-id">
    <div class="fr">
      <div class="fi"><label>블로그명 *</label><input id="sp-name" placeholder="ex) 오뷰티"></div>
      <div class="fi"><label>URL</label><input id="sp-url" placeholder="https://blog.naver.com/..."></div>
    </div>
    <div class="fr">
      <div class="fi"><label>날짜</label><input id="sp-date" type="date"></div>
      <div class="fi"><label>상태</label>
        <select id="sp-status">
          <option value="대기">⏳ 대기</option>
          <option value="진행중">🔵 진행중</option>
          <option value="완료">✅ 완료</option>
        </select>
      </div>
    </div>
    <div class="fi"><label>메모</label><textarea id="sp-memo" placeholder="협찬 내용, 조건, 연락처 등"></textarea></div>
    <div class="btn-row">
      <button class="btn bs btn-sm" onclick="spSave()">💾 저장</button>
      <button class="btn btn-gray btn-sm" onclick="spCancelEdit()">취소</button>
    </div>
  </div>
  <div class="card">
    <div class="ctitle" style="display:flex;justify-content:space-between;align-items:center">
      <span>협찬 리스트</span>
      <span id="sp-count" style="font-size:12px;color:var(--dim)"></span>
    </div>
    <div style="overflow-x:auto">
    <table id="sp-table">
      <thead>
        <tr>
          <th>블로그명</th><th>URL</th><th>날짜</th><th>상태</th><th>메모</th><th style="width:100px">작업</th>
        </tr>
      </thead>
      <tbody id="sp-tbody">
        <tr><td colspan="6" style="color:var(--dim);text-align:center;padding:12px">데이터 없음</td></tr>
      </tbody>
    </table>
    </div>
  </div>
</div>
</div>

<script>
/* ── 탭 전환 ── */
function switchTab(name){
  document.querySelectorAll('.tab-btn').forEach((b,i)=>b.classList.toggle('active',['dash','sponsor'][i]===name));
  document.querySelectorAll('.tab-pane').forEach(p=>p.classList.toggle('active',p.id==='tab-'+name));
  if(name==='sponsor') loadSponsors();
}

/* ── 대시보드 폴링 ── */
let off=0;
const today=new Date().toISOString().slice(0,10);
async function pollStats(){
  try{
    const d=await(await fetch('/api/stats')).json();
    const s=d.stats;
    const dot=document.getElementById('sdot');
    dot.className=s.running?'running':'stopped';
    document.getElementById('stxt').textContent=s.running?'실행 중':'대기 / 중지';
    document.getElementById('sh').textContent=s.hearts;
    document.getElementById('sc').textContent=s.comments;
    document.getElementById('sn').textContent=s.neighbors;
    document.getElementById('sb2').textContent=s.blocks;
    document.getElementById('ss').textContent=s.skips;
    const tb=document.getElementById('wtb');
    if(d.weekly&&d.weekly.length>0){
      let wh=0,wc=0,wn=0;
      tb.innerHTML=d.weekly.map(([dt,v])=>{
        wh+=v.hearts||0;wc+=v.comments||0;wn+=v.neighbors||0;
        const it=dt===today;
        return`<tr class="${it?'trow':''}"><td>${dt.slice(5)}${it?' 📍':''}</td><td>${v.hearts||0}</td><td>${v.comments||0}</td><td>${v.neighbors||0}</td></tr>`;
      }).join('');
      document.getElementById('wh').textContent=wh;
      document.getElementById('wc').textContent=wc;
      document.getElementById('wn').textContent=wn;
    }
    const rl=document.getElementById('rlog');
    if(d.rest_log&&d.rest_log.length>0)
      rl.innerHTML=d.rest_log.map(([cnt,mn,t])=>`<div class="mr"><span class="mt">${t}</span><span>💬${cnt}개 후 ☕${mn}분</span></div>`).join('');
    document.getElementById('upd').textContent='업데이트: '+new Date().toLocaleTimeString('ko-KR');
  }catch(e){}
}
async function pollLogs(){
  try{
    const d=await(await fetch('/api/logs?since='+off)).json();
    if(d.logs&&d.logs.length>0){
      const box=document.getElementById('log-box');
      d.logs.forEach(l=>{
        const el=document.createElement('div');
        el.innerHTML=`<span class="lt">${l.t}</span><span class="tag-${l.tag||'info'}">${esc(l.msg)}</span>`;
        box.appendChild(el);
      });
      off=d.total;
      if(document.getElementById('asc').checked) box.scrollTop=box.scrollHeight;
    }
  }catch(e){}
}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
async function ctrl(cmd){
  try{await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cmd})});}
  catch(e){alert('서버 연결 오류');}
}
pollStats();pollLogs();
setInterval(pollStats,3000);setInterval(pollLogs,1500);

/* ── 협찬 CRUD ── */
const BADGE={
  '대기':'<span class="badge badge-wait">⏳ 대기</span>',
  '진행중':'<span class="badge badge-active">🔵 진행중</span>',
  '완료':'<span class="badge badge-done">✅ 완료</span>'
};
let _spData=[];

async function loadSponsors(){
  try{
    _spData=await(await fetch('/api/sponsors')).json();
    renderSponsors();
  }catch(e){}
}
function renderSponsors(){
  const tb=document.getElementById('sp-tbody');
  document.getElementById('sp-count').textContent=_spData.length+'건';
  if(!_spData.length){
    tb.innerHTML='<tr><td colspan="6" style="color:var(--dim);text-align:center;padding:12px">데이터 없음</td></tr>';
    return;
  }
  tb.innerHTML=_spData.map(s=>`
    <tr>
      <td><strong>${esc(s.name||'')}</strong></td>
      <td>${s.url?`<a href="${esc(s.url)}" target="_blank" style="color:var(--accent);font-size:11px">🔗 링크</a>`:'—'}</td>
      <td>${s.date||'—'}</td>
      <td>${BADGE[s.status]||s.status}</td>
      <td style="max-width:200px;white-space:pre-wrap;font-size:11px">${esc(s.memo||'')}</td>
      <td><div class="btn-row"><button class="btn btn-blue btn-sm" onclick="spEdit('${s.id}')">✏ 수정</button>
          <button class="btn btn-red btn-sm" onclick="spDel('${s.id}')">🗑</button></div></td>
    </tr>`).join('');
}
function spEdit(id){
  const s=_spData.find(x=>x.id===id);
  if(!s)return;
  document.getElementById('sp-edit-id').value=id;
  document.getElementById('sp-name').value=s.name||'';
  document.getElementById('sp-url').value=s.url||'';
  document.getElementById('sp-date').value=s.date||'';
  document.getElementById('sp-status').value=s.status||'대기';
  document.getElementById('sp-memo').value=s.memo||'';
  document.getElementById('sp-form-title').textContent='협찬 수정';
  document.getElementById('sp-form-wrap').scrollIntoView({behavior:'smooth'});
}
function spCancelEdit(){
  document.getElementById('sp-edit-id').value='';
  document.getElementById('sp-name').value='';
  document.getElementById('sp-url').value='';
  document.getElementById('sp-date').value='';
  document.getElementById('sp-status').value='대기';
  document.getElementById('sp-memo').value='';
  document.getElementById('sp-form-title').textContent='새 협찬 추가';
}
async function spSave(){
  const nm=document.getElementById('sp-name').value.trim();
  if(!nm){alert('블로그명을 입력해주세요.');return;}
  const payload={
    name:nm,
    url:document.getElementById('sp-url').value.trim(),
    date:document.getElementById('sp-date').value,
    status:document.getElementById('sp-status').value,
    memo:document.getElementById('sp-memo').value.trim()
  };
  const eid=document.getElementById('sp-edit-id').value;
  if(eid){
    await fetch('/api/sponsors/'+eid,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  } else {
    await fetch('/api/sponsors',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  }
  spCancelEdit();
  loadSponsors();
}
async function spDel(id){
  if(!confirm('삭제할까요?'))return;
  await fetch('/api/sponsors/'+id,{method:'DELETE'});
  loadSponsors();
}
// 오늘 날짜 기본값
document.getElementById('sp-date').value=today;
</script>
</body>
</html>
"""

def _web_ctrl_watcher(dashboard_instance):
    while True:
        try:
            cmd = _web_ctrl_queue.get(timeout=1)
            if cmd == "start":
                dashboard_instance.root.after(0, dashboard_instance._on_start)
            elif cmd == "stop":
                dashboard_instance.root.after(0, dashboard_instance._on_stop)
            elif cmd == "resume":
                dashboard_instance.root.after(0, dashboard_instance._on_resume)
        except Exception:
            pass

def _ngrok_start():
    """ngrok으로 5001 포트를 외부에 노출하고 공개 URL을 반환.
    ngrok이 없으면 None 반환 (조용히 실패).
    """
    import subprocess as _ngp
    import time as _time
    try:
        _ngp.Popen(
            ["ngrok", "http", "5001", "--log=stdout"],
            stdout=_ngp.DEVNULL, stderr=_ngp.DEVNULL,
            creationflags=getattr(_ngp, "CREATE_NO_WINDOW", 0)
        )
        # ngrok API가 올라올 때까지 최대 6초 대기
        import urllib.request as _ur
        for _ in range(12):
            _time.sleep(0.5)
            try:
                with _ur.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=1) as r:
                    import json as _j
                    data = _j.loads(r.read())
                    tunnels = data.get("tunnels", [])
                    for t in tunnels:
                        url = t.get("public_url", "")
                        if url.startswith("https://"):
                            return url
            except Exception:
                continue
    except FileNotFoundError:
        print("[대시보드] ngrok 없음 — 로컬에서만 접속 가능")
    except Exception as e:
        print(f"[대시보드] ngrok 오류: {e}")
    return None

def _start_web_dashboard():
    import threading as _wt
    # Flask 먼저 시작
    t = _wt.Thread(
        target=lambda: _flask_app.run(host="0.0.0.0", port=5001, debug=False, use_reloader=False),
        daemon=True, name="WebDash"
    )
    t.start()
    # ngrok 터널 → 텔레그램 알림 (별도 스레드, Flask 시작과 병렬)
    def _notify():
        import time as _time
        _time.sleep(1)  # Flask 바인딩 대기
        pub_url = _ngrok_start()
        if pub_url:
            print(f"[대시보드] 외부 접속: {pub_url}")
            # GitHub에 ngrok 주소 저장 (콜라보 보드에서 fetch용)
            def _push_ngrok_url(url):
                try:
                    import urllib.request as _ur2, json as _json2, base64 as _b64
                    _gh_token = _GH_TOKEN  # 아래 설정값 사용
                    _repo = "fordgril307-wq/blog-manual"
                    _path = "ngrok_url.txt"
                    _api = f"https://api.github.com/repos/{_repo}/contents/{_path}"
                    _headers = {
                        "Authorization": f"token {_gh_token}",
                        "Content-Type": "application/json",
                        "User-Agent": "blog-automation"
                    }
                    # 기존 파일 SHA 조회 (업데이트용)
                    _sha = None
                    try:
                        _req = urllib.request.Request(_api, headers=_headers)
                        _res = urllib.request.urlopen(_req, timeout=5)
                        _sha = _json2.loads(_res.read())["sha"]
                    except Exception:
                        pass
                    # 파일 내용 업데이트
                    _body = {"message": "update ngrok url", "content": _b64.b64encode(url.encode()).decode()}
                    if _sha:
                        _body["sha"] = _sha
                    _req2 = urllib.request.Request(_api, data=_json2.dumps(_body).encode(), headers=_headers, method="PUT")
                    urllib.request.urlopen(_req2, timeout=5)
                    print(f"[대시보드] GitHub ngrok_url.txt 업데이트 완료")
                except Exception as _e:
                    print(f"[대시보드] GitHub 업데이트 실패: {_e}")
            _wt.Thread(target=_push_ngrok_url, args=(pub_url,), daemon=True, name="GhPush").start()
            # 텔레그램 토큰이 아직 주입 전일 수 있으므로 최대 10초 대기
            for _ in range(20):
                if _tg_token and _tg_chat:
                    break
                _time.sleep(0.5)
            _tg_send(f"🌿 블로그봇 대시보드 시작!\n📱 폰에서 접속: {pub_url}")
        else:
            import socket as _sock
            try:
                local_ip = _sock.gethostbyname(_sock.gethostname())
            except Exception:
                local_ip = "localhost"
            print(f"[대시보드] 로컬 접속: http://{local_ip}:5001")
    _wt.Thread(target=_notify, daemon=True, name="NgrokNotify").start()
    # Flask 뜨면 브라우저 자동 열기
    def _open_browser():
        import time as _time, webbrowser as _wb, urllib.request as _ur
        for _ in range(30):  # 최대 15초 대기
            _time.sleep(0.5)
            try:
                _ur.urlopen("http://127.0.0.1:5001", timeout=1)
                _wb.open("http://127.0.0.1:5001")
                break
            except Exception:
                pass
    _wt.Thread(target=_open_browser, daemon=True, name="BrowserOpen").start()

# ── 절전 방지 (Windows) ──
import ctypes
ES_CONTINUOUS       = 0x80000000
ES_SYSTEM_REQUIRED  = 0x00000001
def _prevent_sleep():
    """실행 중 PC 절전 방지 (모니터는 꺼져도 됨)"""
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        )
    except Exception:
        pass
def _allow_sleep():
    """절전 방지 해제"""
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    except Exception:
        pass
_rest_log   = []   # [(댓글수, 휴식분, 시각str), ...]  — 휴식할 때마다 누적
_block_log  = []   # [(시각str, 알럿텍스트, 키워드), ...]  — 차단 발생마다 누적
stop_event  = threading.Event()
pause_event = threading.Event()   # set = 일시정지, clear = 실행 중
pause_event.clear()

# =============================================
# 대시보드 GUI
# =============================================
class BlogDashboard:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("블로그 자동화 대시보드")
        self.root.geometry("1000x780")
        self.root.configure(bg="#F4F6F3")
        self.root.resizable(True, True)
        self.root.minsize(460, 780)
        self.root.maxsize(1400, 1400)

        # ── 폰트 정의 ──
        F_LABEL  = ("Helvetica Neue", 9)
        F_SMALL  = ("Helvetica Neue", 8)
        F_BIG    = ("Helvetica Neue", 28, "bold")
        F_MED    = ("Helvetica Neue", 14, "bold")
        F_HEAD   = ("Helvetica Neue", 18, "bold")
        F_SUB    = ("Helvetica Neue", 10, "bold")

        # ══════════════════════════════════════
        # 헤더
        # ══════════════════════════════════════
        header = tk.Frame(self.root, bg="#FFFFFF", height=78)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        tk.Label(header, text="🌿", font=("Helvetica Neue", 24),
                 bg="#FFFFFF").place(x=20, y=16)
        tk.Label(header, text="블로그 자동화", font=("Helvetica Neue", 20, "bold"),
                 bg="#FFFFFF", fg="#1A1A1A").place(x=58, y=10)
        tk.Label(header, text="Naver Blog Manager", font=F_SUB,
                 bg="#FFFFFF", fg="#8A9B8A").place(x=60, y=46)

        self.status_dot = tk.Label(header, text="●  대기중", font=F_SMALL,
                                   bg="#FFFFFF", fg="#AAAAAA")
        self.status_dot.pack(side=tk.RIGHT, padx=16)

        # ── 창 크기 드롭다운 버튼 ──
        self._size_menu_open = False

        size_btn = tk.Button(
            header, text="⊞ 창 사이즈 ▾",
            font=("Helvetica Neue", 9), bg="#F4F6F3", fg="#555555",
            relief=tk.FLAT, bd=0,
            highlightthickness=1, highlightbackground="#DDDDDD",
            padx=8, pady=4, cursor="hand2"
        )
        size_btn.pack(side=tk.RIGHT, padx=(0, 8))

        def show_size_menu(event=None):
            if self._size_menu_open:
                return
            self._size_menu_open = True
            size_btn.config(bg="#E8EDE8", fg="#3A7A3A")

            popup = tk.Toplevel(self.root)
            popup.overrideredirect(True)
            popup.configure(bg="#FFFFFF")
            popup.attributes("-topmost", True)

            self.root.update_idletasks()
            bx = size_btn.winfo_rootx()
            by = size_btn.winfo_rooty() + size_btn.winfo_height()
            popup.geometry(f"160x128+{bx - 80}+{by + 2}")

            outer = tk.Frame(popup, bg="#CCCCCC", padx=1, pady=1)
            outer.pack(fill=tk.BOTH, expand=True)
            inner = tk.Frame(outer, bg="#FFFFFF")
            inner.pack(fill=tk.BOTH, expand=True)

            options = [
                ("컴팩트  (460 × 780)",   "460x780"),
                ("표준  (1000 × 780)",    "1000x780"),
                ("와이드  (1400 × 1400)", "1400x1400"),
                ("자율 조정  (드래그)",            "free"),
            ]

            def select(size):
                if size == "free":
                    self.root.resizable(True, True)
                    self.root.minsize(460, 780)
                    self.root.maxsize(1400, 1400)
                else:
                    self.root.resizable(False, False)
                    self.root.geometry(size)
                close_popup()

            def close_popup(event=None):
                self._size_menu_open = False
                size_btn.config(bg="#F4F6F3", fg="#555555")
                try:
                    popup.destroy()
                except Exception:
                    pass

            for i, (label, size) in enumerate(options):
                if i == len(options) - 1:
                    tk.Frame(inner, bg="#EEEEEE", height=1).pack(fill=tk.X, padx=8, pady=2)

                item = tk.Label(
                    inner, text=label,
                    font=("Helvetica Neue", 8),
                    bg="#FFFFFF", fg="#333333",
                    anchor="w", padx=14, pady=7,
                    cursor="hand2"
                )
                item.pack(fill=tk.X)

                def on_enter(e, w=item): w.config(bg="#F0F4F0", fg="#3A7A3A")
                def on_leave(e, w=item): w.config(bg="#FFFFFF", fg="#333333")
                def on_click(e, s=size): select(s)

                item.bind("<Enter>",    on_enter)
                item.bind("<Leave>",    on_leave)
                item.bind("<Button-1>", on_click)

            popup.bind("<FocusOut>", close_popup)
            popup.focus_set()

        size_btn.bind("<Button-1>", show_size_menu)

        # ══════════════════════════════════════
        # 메인 컨텐츠 (좌 설정 | 우 로그+통계)
        # ══════════════════════════════════════
        body = tk.Frame(self.root, bg="#F4F6F3")
        body.pack(fill=tk.BOTH, expand=True, padx=16, pady=12)

        # ── 왼쪽 패널 (스크롤 가능) ──
        left_outer = tk.Frame(body, bg="#F4F6F3", width=440)
        left_outer.pack(side=tk.LEFT, fill=tk.Y)
        left_outer.pack_propagate(False)

        left_canvas = tk.Canvas(left_outer, bg="#F4F6F3", bd=0, highlightthickness=0)
        left_scrollbar = tk.Scrollbar(left_outer, orient="vertical", command=left_canvas.yview)
        left_canvas.configure(yscrollcommand=left_scrollbar.set)
        left_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        left = tk.Frame(left_canvas, bg="#F4F6F3")
        left_canvas_window = left_canvas.create_window((0, 0), window=left, anchor="nw")

        def _on_left_configure(e):
            left_canvas.configure(scrollregion=left_canvas.bbox("all"))
        def _on_canvas_resize(e):
            left_canvas.itemconfig(left_canvas_window, width=e.width)

        left.bind("<Configure>", _on_left_configure)
        left_canvas.bind("<Configure>", _on_canvas_resize)

        def _on_mousewheel(e):
            left_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        left_canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # ── 오른쪽 패널 ──
        right = tk.Frame(body, bg="#F4F6F3")
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))

        # ══════════════════════════════════════
        # 왼쪽: 카드들
        # ══════════════════════════════════════
        def card(parent, title, pady_top=0):
            outer = tk.Frame(parent, bg="#F4F6F3")
            outer.pack(fill=tk.X, pady=(pady_top, 8))
            inner = tk.Frame(outer, bg="#FFFFFF", bd=0,
                             highlightthickness=1, highlightbackground="#E8EDE8")
            inner.pack(fill=tk.X)
            lbl = tk.Label(inner, text=title, font=("Helvetica Neue", 10, "bold"),
                           bg="#FFFFFF", fg="#8A9B8A")
            lbl.pack(anchor="w", padx=14, pady=(10, 2))
            inner._outer = outer   # outer 참조 보관
            inner._title_lbl = lbl  # 타이틀 라벨 참조 보관
            return inner

        # ── 카드 1: 인증 ──
        c1 = card(left, "🔐  인증")
        tk.Label(c1, text="비밀번호", font=F_SMALL, bg="#FFFFFF", fg="#555").pack(anchor="w", padx=14)
        self.secret_var = tk.StringVar(value="My_Friend_KJR")
        tk.Entry(c1, textvariable=self.secret_var, show="*", font=F_LABEL,
                 bg="#F7FAF7", relief=tk.FLAT, bd=0,
                 highlightthickness=1, highlightbackground="#D0DDD0"
                 ).pack(fill=tk.X, padx=14, pady=(2, 6), ipady=5)

        tk.Label(c1, text="Gemini API 키", font=F_SMALL,
                 bg="#FFFFFF", fg="#555").pack(anchor="w", padx=14)
        self.api_text = tk.Text(c1, font=("Courier", 8), height=1,
                                bg="#F7FAF7", relief=tk.FLAT, bd=0,
                                highlightthickness=1, highlightbackground="#D0DDD0")
        self.api_text.insert("1.0", "AQ.Ab8RN6K8NupguNFf5N7oEb_kAYR8Ru3i-awbPjIMTYUwJW710g")
        self.api_text.pack(fill=tk.X, padx=14, pady=(2, 8), ipady=4)

        tg_row = tk.Frame(c1, bg="#FFFFFF")
        tg_row.pack(fill=tk.X, padx=14, pady=(2, 12))
        # 봇 토큰
        tg_left = tk.Frame(tg_row, bg="#FFFFFF")
        tg_left.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        tk.Label(tg_left, text="텔레그램 봇 토큰", font=F_SMALL,
                 bg="#FFFFFF", fg="#555").pack(anchor="w")
        self.tg_token_var = tk.StringVar(value="")
        tk.Entry(tg_left, textvariable=self.tg_token_var, show="*", font=("Courier", 8),
                 bg="#F7FAF7", relief=tk.FLAT, bd=0,
                 highlightthickness=1, highlightbackground="#D0DDD0"
                 ).pack(fill=tk.X, ipady=5)
        # 채팅 ID
        tg_right = tk.Frame(tg_row, bg="#FFFFFF")
        tg_right.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(tg_right, text="텔레그램 채팅 ID", font=F_SMALL,
                 bg="#FFFFFF", fg="#555").pack(anchor="w")
        self.tg_chat_var = tk.StringVar(value="")
        tk.Entry(tg_right, textvariable=self.tg_chat_var, font=F_LABEL,
                 bg="#F7FAF7", relief=tk.FLAT, bd=0,
                 highlightthickness=1, highlightbackground="#D0DDD0"
                 ).pack(fill=tk.X, ipady=5)

        # GitHub 토큰 행
        gh_row = tk.Frame(c1, bg="#FFFFFF")
        gh_row.pack(fill=tk.X, padx=14, pady=(0, 12))
        tk.Label(gh_row, text="GitHub 토큰 (ngrok URL 자동 업로드 · 선택)", font=F_SMALL,
                 bg="#FFFFFF", fg="#555").pack(anchor="w")
        self.gh_token_var = tk.StringVar(value="")
        tk.Entry(gh_row, textvariable=self.gh_token_var, show="*", font=("Courier", 8),
                 bg="#F7FAF7", relief=tk.FLAT, bd=0,
                 highlightthickness=1, highlightbackground="#D0DDD0"
                 ).pack(fill=tk.X, ipady=5)

        # ── 카드 2: 모드 선택 ──
        c2 = card(left, "⚙️  실행 모드")
        self.mode_var = tk.StringVar(value="이웃")
        self.add_neighbor_var = tk.BooleanVar(value=True)
        mf = tk.Frame(c2, bg="#FFFFFF")
        mf.pack(fill=tk.X, padx=14, pady=(4, 8))

        # ① 이웃방문  ② 검색어  버튼
        for val, label in [("이웃", "① 이웃 방문"), ("검색", "② 검색어")]:
            rb = tk.Radiobutton(mf, text=label, variable=self.mode_var, value=val,
                                command=self._toggle_mode,
                                bg="#FFFFFF", fg="#1A1A1A",
                                activebackground="#FFFFFF",
                                selectcolor="#A8D8A8",
                                font=F_SMALL, indicatoron=0,
                                relief=tk.FLAT, bd=0,
                                highlightthickness=1, highlightbackground="#D0DDD0",
                                padx=12, pady=6, width=11)
            rb.pack(side=tk.LEFT, padx=(0, 4))

        # 이웃추가 토글 버튼 (맨 오른쪽, 독립 버튼처럼)
        self._nb_btn_colors = {"on": ("#A8D8A8", "#1A1A1A"), "off": ("#FFFFFF", "#888")}
        self.nb_toggle_btn = tk.Button(
            mf, text="＋ 이웃추가",
            font=F_SMALL, relief=tk.FLAT, bd=0,
            highlightthickness=1,
            padx=10, pady=6, cursor="hand2",
            command=self._toggle_add_neighbor
        )
        self.nb_toggle_btn.pack(side=tk.RIGHT)

        # ── 카드 3: 이웃 모드 설정 ──
        self.c3 = card(left, "①  이웃 모드 설정")
        self.c3.config(highlightbackground="#A8D8A8", highlightthickness=2)
        self.c3._title_lbl.config(fg="#4A9A4A")
        self.neighbor_vars = []
        for lbl, default in [("시작 게시글 번호", "1"), ("끝 게시글 번호", "10"), ("그룹 ID", "13")]:
            row = tk.Frame(self.c3, bg="#FFFFFF")
            row.pack(fill=tk.X, padx=14, pady=2)
            tk.Label(row, text=lbl, font=F_SMALL, bg="#FFFFFF", fg="#555",
                     width=16, anchor="w").pack(side=tk.LEFT)
            v = tk.StringVar(value=default)
            self.neighbor_vars.append(v)
            tk.Entry(row, textvariable=v, font=F_LABEL, width=8,
                     bg="#F7FAF7", relief=tk.FLAT, bd=0,
                     highlightthickness=1, highlightbackground="#D0DDD0"
                     ).pack(side=tk.LEFT, padx=4, ipady=4)
        tk.Frame(self.c3, bg="#FFFFFF", height=8).pack()

        # ── 카드 4: 검색 모드 설정 ──
        self.c4 = card(left, "②  검색 모드 설정")
        self.c4.config(highlightbackground="#A8D8A8", highlightthickness=2)
        self.c4._title_lbl.config(fg="#4A9A4A")
        tk.Label(self.c4, text="검색어 (쉼표로 구분, 최대 5개)", font=F_SMALL,
                 bg="#FFFFFF", fg="#555").pack(anchor="w", padx=14)
        self.search_text = tk.Text(self.c4, font=("Courier", 8), height=1,
                                   bg="#F7FAF7", relief=tk.FLAT, bd=0,
                                   highlightthickness=1, highlightbackground="#D0DDD0")
        self.search_text.pack(fill=tk.X, padx=14, pady=(2, 6), ipady=4)

        row4 = tk.Frame(self.c4, bg="#FFFFFF")
        row4.pack(fill=tk.X, padx=14, pady=4)
        tk.Label(row4, text="방문 수", font=F_SMALL, bg="#FFFFFF", fg="#555",
                 width=8, anchor="w").pack(side=tk.LEFT)
        self.visit_var = tk.StringVar(value="20")
        tk.Entry(row4, textvariable=self.visit_var, font=F_LABEL, width=6,
                 bg="#F7FAF7", relief=tk.FLAT, bd=0,
                 highlightthickness=1, highlightbackground="#D0DDD0"
                 ).pack(side=tk.LEFT, padx=4, ipady=4)

        self.order_var = tk.StringVar(value="date")
        for val, lbl in [("date", "최신순"), ("sim", "관련도순")]:
            tk.Radiobutton(row4, text=lbl, variable=self.order_var, value=val,
                           bg="#FFFFFF", fg="#1A1A1A", activebackground="#FFFFFF",
                           selectcolor="#A8D8A8", font=F_SMALL,
                           ).pack(side=tk.LEFT, padx=6)
        tk.Frame(self.c4, bg="#FFFFFF", height=6).pack()

        # ── 카드 5: 이웃추가 메시지 (항상 표시) ──
        self.c5 = card(left, "＋  이웃추가 메시지")
        tk.Label(self.c5, text="방문한 블로그에 이웃신청 시 보낼 메시지", font=F_SMALL,
                 bg="#FFFFFF", fg="#888").pack(anchor="w", padx=14)
        self.neighbor_msg_text = tk.Text(
            self.c5, font=("Courier", 8), height=3,
            bg="#F7FAF7", relief=tk.FLAT, bd=0,
            highlightthickness=1, highlightbackground="#D0DDD0"
        )
        self.neighbor_msg_text.insert("1.0", "안녕하세요! 우연히 블로그에 들렀다가 글들이 제 취향이라 이웃 추가 드려요..!\n올려주신 포스팅들 정독하고 댓글이랑 공감도 남겨두고 갑니다!ㅎㅎ\n앞으로도 새 글 올라오면 1등으로 달려와서 읽고 싶어요. 오늘도 기분 좋은 일 가득한 하루 보내세요!")
        self.neighbor_msg_text.pack(fill=tk.X, padx=14, pady=(4, 6), ipady=4)

        # ── 프리미엄 그룹 조건 ──
        prem_f = tk.Frame(self.c5, bg="#FFFFFF")
        prem_f.pack(fill=tk.X, padx=14, pady=(0, 4))
        tk.Label(prem_f, text="프리미엄 그룹명", font=F_SMALL, bg="#FFFFFF", fg="#555").pack(side=tk.LEFT)
        self.prem_group_var = tk.StringVar(value="이웃만")
        prem_entry = tk.Entry(prem_f, textvariable=self.prem_group_var,
                              font=F_SMALL, width=10, relief=tk.FLAT, bd=0,
                              highlightthickness=1, highlightbackground="#D0DDD0", bg="#F7FAF7")
        prem_entry.pack(side=tk.LEFT, padx=(6, 14))
        tk.Label(prem_f, text="최소 댓글수", font=F_SMALL, bg="#FFFFFF", fg="#555").pack(side=tk.LEFT)
        self.prem_min_var = tk.StringVar(value="100")
        prem_min_entry = tk.Entry(prem_f, textvariable=self.prem_min_var,
                                  font=F_SMALL, width=5, relief=tk.FLAT, bd=0,
                                  highlightthickness=1, highlightbackground="#D0DDD0", bg="#F7FAF7")
        prem_min_entry.pack(side=tk.LEFT, padx=(6, 0))
        tk.Label(prem_f, text="개 이상", font=F_SMALL, bg="#FFFFFF", fg="#888").pack(side=tk.LEFT, padx=(2, 0))

        # ── 조건 미달 시 이웃추가 스킵 ──
        skip_f = tk.Frame(self.c5, bg="#FFFFFF")
        skip_f.pack(fill=tk.X, padx=14, pady=(0, 6))
        self.skip_below_prem_var = tk.BooleanVar(value=False)
        tk.Checkbutton(skip_f, text="최소 댓글수 미달 시 이웃추가 스킵 (조건 충족 블로그만 추가)",
                       variable=self.skip_below_prem_var,
                       font=F_SMALL, bg="#FFFFFF", fg="#555",
                       activebackground="#FFFFFF").pack(side=tk.LEFT)

        # 초기 상태
        self._toggle_mode()
        self._update_nb_btn()

        # ── 시작 버튼 ──
        self.start_btn = tk.Button(
            left, text="▶  자동화 시작", font=("Helvetica Neue", 12, "bold"),
            bg="#7EC87E", fg="#FFFFFF", relief=tk.FLAT, bd=0,
            activebackground="#5CAF5C", activeforeground="#FFFFFF",
            cursor="hand2", pady=12, command=self._on_start
        )
        self.start_btn.pack(fill=tk.X, pady=(4, 0))

        def on_btn_enter(e):
            if stats["running"]:
                self.start_btn.config(
                        text="■  강제 중지", bg="#FF5F57",
                        activebackground="#E04040",
                        command=self._on_stop
                 )

        def on_btn_leave(e):
            if stats["running"]:
                self.start_btn.config(
                        text="실행 중...", bg="#AAAAAA",
                        activebackground="#999999",
                        command=self._on_start
                )

        self.start_btn.bind("<Enter>", on_btn_enter)
        self.start_btn.bind("<Leave>", on_btn_leave)

        # ══════════════════════════════════════
        # 오른쪽: 통계 카드 + 로그
        # ══════════════════════════════════════
        stat_row = tk.Frame(right, bg="#F4F6F3")
        stat_row.pack(fill=tk.X, pady=(0, 10))

        def stat_card(parent, emoji, label, color):
            f = tk.Frame(parent, bg=color, highlightthickness=0)
            f.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8), ipady=10)
            tk.Label(f, text=emoji, font=("Helvetica Neue", 22), bg=color).pack(pady=(12, 0))
            cnt_lbl = tk.Label(f, text="0", font=F_BIG, bg=color, fg="#1A1A1A")
            cnt_lbl.pack()
            tk.Label(f, text=label, font=("Helvetica Neue", 9), bg=color, fg="#666").pack(pady=(0, 12))
            return cnt_lbl

        self.heart_lbl   = stat_card(stat_row, "🩷", "총 하트",  "#D8F0D8")
        self.comment_lbl = stat_card(stat_row, "💬", "총 댓글", "#E8F4E8")

        # 현재 처리중 카드
        prog_row = tk.Frame(right, bg="#F4F6F3")
        prog_row.pack(fill=tk.X, pady=(0, 0))
        self.current_blog_var = tk.StringVar(value="대기중...")
        cur_f = tk.Frame(prog_row, bg="#FFFFFF",
                         highlightthickness=1, highlightbackground="#E8EDE8")
        cur_f.pack(fill=tk.X)

        tk.Label(cur_f, text="현재 처리중", font=("Helvetica Neue", 10, "bold"),
                 bg="#FFFFFF", fg="#8A9B8A").pack(anchor="w", padx=14, pady=(8, 0))
        tk.Label(cur_f, textvariable=self.current_blog_var, font=F_MED,
                 bg="#FFFFFF", fg="#1A1A1A").pack(anchor="w", padx=14, pady=(2, 8))

        # ── 실시간 로그 터미널 ──
        log_header = tk.Frame(right, bg="#1C2B1C")
        log_header.pack(fill=tk.X)
        dots = tk.Frame(log_header, bg="#1C2B1C")
        dots.pack(side=tk.LEFT, padx=10, pady=8)
        for color in ["#FF5F57", "#FEBC2E", "#28C840"]:
            tk.Label(dots, text="●", font=("Helvetica", 10),
                     fg=color, bg="#1C2B1C").pack(side=tk.LEFT, padx=1)
        tk.Label(log_header, text="실시간 로그", font=("Courier", 9, "bold"),
                 bg="#1C2B1C", fg="#7EC87E").pack(side=tk.LEFT, padx=4)

        log_frame = tk.Frame(right, bg="#0D1A0D")
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_box = tk.Text(
            log_frame, font=("Courier", 9),
            bg="#0D1A0D", fg="#A8D8A8",
            relief=tk.FLAT, bd=0,
            insertbackground="#7EC87E",
            selectbackground="#2A4A2A",
            state=tk.DISABLED, wrap=tk.WORD,
            padx=10, pady=8
        )
        self.log_box.pack(fill=tk.BOTH, expand=True)

        sb = tk.Scrollbar(log_frame, command=self.log_box.yview,
                          bg="#0D1A0D", troughcolor="#0D1A0D", activebackground="#2A4A2A")
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_box.tag_config("info",    foreground="#A8D8A8")
        self.log_box.tag_config("success", foreground="#7EC87E")
        self.log_box.tag_config("warn",    foreground="#FEBC2E")
        self.log_box.tag_config("error",   foreground="#FF5F57")
        self.log_box.tag_config("dim",     foreground="#4A6A4A")
        self.log_box.tag_config("bold",    foreground="#FFFFFF", font=("Courier", 9, "bold"))
        self.log_box.tag_config("header",  foreground="#28C840", font=("Courier", 9, "bold"))

        self.log_box.configure(yscrollcommand=sb.set)

        self._log("━━━  블로그 자동화 대시보드 시작  ━━━", "header")
        self._log("설정을 입력하고 시작 버튼을 누르세요.", "dim")

        # 주기적 업데이트
        self.root.after(200, self._poll_queue)

    # ───────────────────────────────────────
    def _toggle_mode(self):
        mode = self.mode_var.get()
        if mode == "이웃":
            self.c3._outer.pack(fill=tk.X, pady=(0, 8), before=self.c5._outer)
            self.c4._outer.pack_forget()
        else:
            self.c4._outer.pack(fill=tk.X, pady=(0, 8), before=self.c5._outer)
            self.c3._outer.pack_forget()

    def _toggle_add_neighbor(self):
        self.add_neighbor_var.set(not self.add_neighbor_var.get())
        self._update_nb_btn()

    def _update_nb_btn(self):
        on = self.add_neighbor_var.get()
        bg, fg = self._nb_btn_colors["on"] if on else self._nb_btn_colors["off"]
        hl = "#A8D8A8" if on else "#CCCCCC"
        self.nb_toggle_btn.config(bg=bg, fg=fg, activebackground=bg,
                                  activeforeground=fg, highlightbackground=hl)
        # 카드5 테두리 및 타이틀 색상으로 ON/OFF 시각적 표시
        if on:
            self.c5.config(highlightbackground="#A8D8A8", highlightthickness=2)
            self.c5._title_lbl.config(fg="#4A9A4A")
        else:
            self.c5.config(highlightbackground="#E8EDE8", highlightthickness=1)
            self.c5._title_lbl.config(fg="#8A9B8A")

    def _toggle_neighbor_msg(self):
        pass  # 더 이상 사용 안 함 (c5 항상 표시)

    def _log(self, msg, tag="info"):
        self.log_box.configure(state=tk.NORMAL)
        ts = time.strftime("%H:%M:%S")
        if tag == "header":
            self.log_box.insert(tk.END, f"\n{msg}\n", tag)
        else:
            self.log_box.insert(tk.END, f"[{ts}] ", "dim")
            self.log_box.insert(tk.END, f"{msg}\n", tag)
        self.log_box.configure(state=tk.DISABLED)
        self.log_box.see(tk.END)

    def _update_stats(self):
        self.heart_lbl.config(text=str(stats["hearts"]))
        self.comment_lbl.config(text=str(stats["comments"]))

    def _poll_queue(self):
         try:
             while True:
                 item = log_queue.get_nowait()
                 if item[0] == "log":
                     self._log(item[1], item[2])
                 elif item[0] == "stats":
                     self._update_stats()
                 elif item[0] == "current":
                     self.current_blog_var.set(item[1])
                 elif item[0] == "tg_cmd" and item[1] == "run":
                     if not stats["running"]:
                         self._on_start()
                 elif item[0] == "pause":
                     reason = item[1] if len(item) > 1 else "오류 발생"
                     self.start_btn.config(
                         state=tk.NORMAL, bg="#FEBC2E", text=f"⚠ {reason} — 재개하려면 클릭",
                         activebackground="#E0A020", command=self._on_resume
                     )
                     self.status_dot.config(text=f"●  일시정지: {reason}", fg="#FEBC2E")
                 elif item[0] == "done":
                     self.start_btn.config(state=tk.NORMAL, bg="#7EC87E", text="▶  자동화 시작",
                                           command=self._on_start)
                     self.status_dot.config(text="●  완료", fg="#28C840")
                     stats["running"] = False
         except queue.Empty:
             pass
         self.root.after(200, self._poll_queue)

    def _on_start(self):
        global _neighbor_limit_reached
        if stats["running"]:
            return

        secret = self.secret_var.get().strip()
        if secret != "My_Friend_KJR":
            messagebox.showerror("오류", "비밀번호가 틀렸습니다!")
            return

        api_key = self.api_text.get("1.0", tk.END).strip()
        if not api_key:
            messagebox.showerror("오류", "Gemini API 키를 입력해주세요!")
            return

        tg_token = self.tg_token_var.get().strip()
        tg_chat  = self.tg_chat_var.get().strip()
        if not tg_token or not tg_chat:
            messagebox.showerror("오류", "텔레그램 봇 토큰과 채팅 ID를 입력해주세요!")
            return

        try:
            prem_min = int(self.prem_min_var.get().strip())
        except ValueError:
            prem_min = 100
        cfg = {"api_key": api_key, "tg_token": tg_token, "tg_chat": tg_chat,
               "gh_token": self.gh_token_var.get().strip(),
               "mode": self.mode_var.get(),
               "add_neighbor": self.add_neighbor_var.get(),
               "neighbor_msg": self.neighbor_msg_text.get("1.0", tk.END).strip() if self.add_neighbor_var.get() else "",
               "prem_group": self.prem_group_var.get().strip(),
               "prem_min_comments": prem_min,
               "skip_below_prem": self.skip_below_prem_var.get()}

        if cfg["mode"] == "이웃":
            try:
                cfg["start"] = int(self.neighbor_vars[0].get())
                cfg["end"]   = int(self.neighbor_vars[1].get())
            except ValueError:
                messagebox.showerror("오류", "게시글 번호는 숫자로!"); return
            cfg["group_id"] = self.neighbor_vars[2].get().strip()
        else:
            kws = [k.strip() for k in self.search_text.get("1.0", tk.END).strip().replace('\n', ',').split(',') if k.strip()]
            if not kws:
                messagebox.showerror("오류", "검색어를 최소 1개 입력해주세요!"); return
            if len(kws) > 5:
                messagebox.showerror("오류", "검색어는 최대 5개!"); return
            try:
                cfg["visit_count"] = int(self.visit_var.get())
            except ValueError:
                messagebox.showerror("오류", "방문 수는 숫자로!"); return
            cfg["keywords"]   = kws
            cfg["order_type"] = self.order_var.get()

        stats["hearts"] = 0
        stats["comments"] = 0
        stats["blocks"] = 0
        stats["skips"] = 0
        stats["neighbors"] = 0
        stats["running"] = True
        _neighbor_limit_reached = False
        _rest_log.clear()
        _block_log.clear()
        self._update_stats()
        stop_event.clear()
        pause_event.clear()
        self.start_btn.config(state=tk.NORMAL, bg="#AAAAAA", text="실행 중...",
                                                           activebackground="#999999", command=self._on_start)
        self.status_dot.config(text="●  실행중", fg="#28C840")

        _prevent_sleep()
        # 텔레그램 원격 제어 polling 스레드 — 싱글턴 (이미 살아있으면 새로 시작 안 함)
        global _tg_poll_thread
        if _tg_poll_thread is None or not _tg_poll_thread.is_alive():
            _tg_poll_thread = threading.Thread(target=_tg_poll_commands, daemon=True)
            _tg_poll_thread.start()
        t = threading.Thread(target=_run_worker, args=(cfg,), daemon=True)
        t.start()
        _tg_send(f"🚀 블로그봇 시작 ({time.strftime('%H:%M')})")

    def _on_resume(self):
        """일시정지 해제 → 자동화 재개"""
        stop_event.clear()   # 혹시 stop_event 남아있으면 초기화
        pause_event.clear()
        self.start_btn.config(
            state=tk.NORMAL, bg="#AAAAAA", text="실행 중...",
            activebackground="#999999", command=self._on_start
        )
        self.status_dot.config(text="●  실행중", fg="#28C840")
        glog("▶ 재개됨", "success")

    def _on_stop(self):
        stop_event.set()
        pause_event.clear()   # 정지 시 일시정지도 해제해서 스레드가 종료 감지
        _allow_sleep()
        self.start_btn.config(
            text="▶  자동화 시작", bg="#7EC87E",
            activebackground="#5CAF5C",
            state=tk.NORMAL, command=self._on_start
        )
        self.status_dot.config(text="●  중지됨", fg="#FF5F57")
        glog("⛔ 강제 중지 요청됨", "error")

    def run(self):
        self.root.mainloop()


# =============================================
# 로그 헬퍼 (스레드 → GUI 큐)
# =============================================
def rnd(a, b):
    """사람처럼 보이기 위한 랜덤 sleep (a~b초)"""
    time.sleep(random.uniform(a, b))

# 네이버 측 제한 — 일시정지 필요 (내 계정/IP 차단)
_BLOCK_KEYWORDS  = ['잠시 후', '이용해주세요', '일시적', '비정상']
# 하루 댓글 한도 초과 — 오늘은 더 이상 댓글 불가, 중단
# 실제 팝업 텍스트: "이미 제한된 개수의 글을 등록하여 더이상 등록할 수 없습니다."
_RATELIMIT_KEYWORDS = ['제한된 개수', '하루', '너무 많', '댓글을 많이', '과다', '일정 횟수']
# 블로그 주인 설정 — 그냥 다음 블로그로 스킵
# 실제 팝업 텍스트: "스팸 차단" 또는 블로그 주인이 댓글 막은 경우
_SKIP_KEYWORDS   = ['스팸 차단', '댓글을 등록할 수 없습니다', '댓글 기능이']

def dismiss_alert(driver):
    """페이지 이동/액션 후 브라우저 alert 체크 — 있으면 텍스트 기록 후 확인 누름.
    네이버 차단 → 'blocked' (일시정지)
    댓글 과다 작성 → 'ratelimit' (대기 후 재시도, 블로그 스킵 X)
    블로그 주인 스팸차단 → 'skip' (다음 블로그로)
    없으면 None."""
    try:
        alert = Alert(driver)
        txt = alert.text
        alert.accept()
        glog(f"  🔔 Alert: {txt}", "warn")
        if any(k in txt for k in _BLOCK_KEYWORDS):
            glog("  ⛔ 네이버 차단 감지 — 일시정지", "error")
            stats["blocks"] = stats.get("blocks", 0) + 1
            _block_log.append((time.strftime('%H:%M'), txt))
            auto_pause("오류 발생")
            wait_if_paused()
            return 'blocked'
        if any(k in txt for k in _RATELIMIT_KEYWORDS):
            glog("  🛑 오늘 댓글 한도 초과 — 댓글 작업 중단 (공감/이웃만 계속)", "warn")
            stats["blocks"] = stats.get("blocks", 0) + 1
            _block_log.append((time.strftime('%H:%M'), txt))
            return 'ratelimit'
        if any(k in txt for k in _SKIP_KEYWORDS):
            glog("  ⏭ 블로그 주인 스팸차단 — 다음 블로그로 스킵", "warn")
            stats["skips"] = stats.get("skips", 0) + 1
            return 'skip'
        # 매칭 키워드 없는 기타 알럿 — 내용 로그 남기고 스킵
        glog(f"  ⚠ 알 수 없는 알럿({txt[:40]}) — 다음 블로그로 스킵", "warn")
        stats["skips"] = stats.get("skips", 0) + 1
        return 'skip'
    except Exception:
        return None

def wait_if_paused():
    """일시정지 상태면 재개 신호 올 때까지 블로킹"""
    while pause_event.is_set():
        if stop_event.is_set():
            return
        time.sleep(0.5)

def auto_pause(reason="오류 발생"):
    """오류 시 자동 일시정지 + GUI에 알림 + 텔레그램 알림"""
    pause_event.set()
    log_queue.put(("pause", reason))
    hearts    = stats.get("hearts", 0)
    comments  = stats.get("comments", 0)
    neighbors = stats.get("neighbors", 0)
    _tg_send(f"⏸ 블로그봇 일시정지\n사유: {reason}\n❤️ 공감 {hearts}개  💬 댓글 {comments}개  ➕ 이웃추가 {neighbors}개\n재개하려면 GUI에서 [재개] 버튼을 누르세요.")

# ─── 작업 세션 추적 ───
_session_start_time = None
_session_log_lines  = []   # glog 호출 시 누적

def _session_start():
    global _session_start_time, _session_log_lines
    _session_start_time = time.time()
    _session_log_lines  = []
    _rest_log.clear()
    _block_log.clear()

def _session_append_log(msg):
    _session_log_lines.append(msg)

def _save_pending_report(end_reason, error_detail=""):
    """보고서를 파일에 저장 — 드라이버 죽어도 다음 시작 때 발송 가능"""
    try:
        report = {
            "start_time": _session_start_time,
            "end_time": time.time(),
            "end_reason": end_reason,
            "error_detail": error_detail,
            "hearts": stats.get("hearts", 0),
            "comments": stats.get("comments", 0),
            "blocks": stats.get("blocks", 0),
            "skips": stats.get("skips", 0),
            "neighbors": stats.get("neighbors", 0),
            "rest_log": list(_rest_log),
            "block_log": list(_block_log),
            "log_lines": list(_session_log_lines),
        }
        with open(_PENDING_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False)
    except Exception:
        pass

def _send_pending_report_if_exists():
    """저장된 미발송 보고서가 있으면 텔레그램으로 재발송 후 파일 삭제"""
    try:
        if not os.path.exists(_PENDING_REPORT_FILE):
            return
        with open(_PENDING_REPORT_FILE, encoding="utf-8") as f:
            r = json.load(f)
        start_str    = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r["start_time"])) if r.get("start_time") else "-"
        end_str      = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r["end_time"])) if r.get("end_time") else "-"
        end_reason   = r.get("end_reason", "알 수 없음")
        error_detail = r.get("error_detail", "")
        hearts     = r.get("hearts", 0)
        comments   = r.get("comments", 0)
        blocks     = r.get("blocks", 0)
        skips      = r.get("skips", 0)
        neighbors  = r.get("neighbors", 0)
        prev_rest  = r.get("rest_log", [])
        prev_block = r.get("block_log", [])
        raw_log_lines = r.get("log_lines", [])
        filtered = _filter_log_lines(raw_log_lines)
        log_file_content = "\n".join(filtered) if filtered else "(해당 로그 없음)"
        log_filename = f"blog_log_prev_{time.strftime('%Y%m%d_%H%M%S')}.txt"

        # 시간 계산
        p_start = r.get("start_time")
        p_end   = r.get("end_time")
        total_sec  = (p_end - p_start) if p_start and p_end else 0
        rest_total = sum(entry[1] for entry in prev_rest if len(entry) >= 2)
        work_sec   = max(total_sec - rest_total * 60, 0)
        speed_work  = round(comments / (work_sec / 3600), 1) if work_sec > 0 else 0
        speed_total = round(comments / (total_sec / 3600), 1) if total_sec > 0 else 0

        def _fmt_dur(sec):
            h, m = int(sec // 3600), int((sec % 3600) // 60)
            return f"{h}시간 {m}분" if h else f"{m}분"

        _KR_DOW = ["월","화","수","목","금","토","일"]
        p_start_ts = time.localtime(p_start) if p_start else time.localtime()
        dow      = _KR_DOW[p_start_ts.tm_wday]
        date_str = time.strftime(f'%Y.%m.%d ({dow})', p_start_ts)
        s_hm     = time.strftime('%H:%M', p_start_ts)
        e_hm     = time.strftime('%H:%M', time.localtime(p_end)) if p_end else "-"

        rest_section = ""
        if prev_rest:
            rest_lines = "\n".join(
                f"└ {entry[2] if len(entry)>2 else '-'}  {entry[0]}개 후 {entry[1]}분"
                for entry in prev_rest
            )
            rest_section = f"\n\n☕️ 휴식 시간 (총 {len(prev_rest)}회 / {rest_total:.1f}분)\n{rest_lines}"

        issue_lines = []
        if blocks:
            for bl in prev_block:
                t2, txt2 = bl[0], bl[1] if len(bl) > 1 else ""
                issue_lines.append(f"└ 차단 {blocks}회  {t2} — {txt2[:30]}")
        if skips:
            issue_lines.append(f"└ 스킵 {skips}개")
        if error_detail:
            issue_lines.append(f"└ ⚠️ {error_detail[:40]}")
        issue_section = ""
        if issue_lines:
            issue_section = "\n\n⛔️ 특이사항\n" + "\n".join(issue_lines)

        msg = f"""📢 블로그봇 미발송 보고서 (이전 세션 강제종료로 지연됨)

Overview
─────────────────────
❤️ 공감 {hearts}개   💬 댓글 {comments}개   ➕ 이웃 {neighbors}명

Details
─────────────────────
✅ 상태 : {end_reason}

⏰ 시간 : {date_str} {s_hm} - {e_hm}
└ 총 {_fmt_dur(total_sec)}  |  실작업 {_fmt_dur(work_sec)}
└ 속도 : 총 {speed_total}개/시간  |  실작업 {speed_work}개/시간{rest_section}{issue_section}"""
        ok_msg  = _tg_send(msg)
        ok_file = _tg_send_file(log_filename, log_file_content, caption="📋 상세 로그 (댓글·공감·휴식 기준)")
        ok = ok_msg or ok_file
        if ok:
            os.remove(_PENDING_REPORT_FILE)
            glog("📬 이전 세션 미발송 보고서 텔레그램 발송 완료!", "success")
        else:
            glog("  ⚠️ 미발송 보고서 재발송 실패", "error")
    except Exception as e:
        glog(f"  ⚠️ 미발송 보고서 재발송 실패: {e}", "error")


# 텔레그램 설정 (런타임에 cfg에서 주입)
_tg_token = ""
_tg_chat  = ""

# GitHub 설정 (ngrok URL 자동 업로드용) — 런타임에 cfg에서 주입
_GH_TOKEN = ""

def _tg_send(text):
    """텔레그램으로 메시지 발송 — 드라이버 없이 HTTP만으로 동작"""
    if not _tg_token or not _tg_chat:
        return False
    try:
        # 텔레그램 메시지 4096자 제한 — 초과 시 분할 발송
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            resp = requests.post(
                f"https://api.telegram.org/bot{_tg_token}/sendMessage",
                json={"chat_id": _tg_chat, "text": chunk},
                timeout=10
            )
            resp.raise_for_status()
        return True
    except Exception as e:
        return False

def _tg_send_file(filename, file_content, caption=""):
    """텔레그램으로 텍스트 파일 첨부 발송 (sendDocument)"""
    if not _tg_token or not _tg_chat:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{_tg_token}/sendDocument",
            data={"chat_id": _tg_chat, "caption": caption},
            files={"document": (filename, file_content.encode("utf-8"), "text/plain")},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False

# ── 텔레그램 원격 제어 ──
_tg_last_update_id = 0
_tg_poll_thread = None   # 싱글턴 — 한 번만 시작

def _tg_poll_commands():
    """텔레그램 getUpdates long-polling — 별도 데몬 스레드에서 실행"""
    global _tg_last_update_id
    while True:
        if not _tg_token or not _tg_chat:
            time.sleep(5)
            continue
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{_tg_token}/getUpdates",
                params={"offset": _tg_last_update_id + 1, "timeout": 30},
                timeout=35
            ).json()
            for upd in r.get("result", []):
                _tg_last_update_id = upd["update_id"]
                msg = upd.get("message", {})
                if str(msg.get("chat", {}).get("id")) != str(_tg_chat):
                    continue
                cmd = msg.get("text", "").strip()
                if cmd == "시작":
                    log_queue.put(("tg_cmd", "run"))
                elif cmd == "정지":
                    pause_event.set()
                    _tg_send("⏸ 일시정지")
                elif cmd == "재개":
                    pause_event.clear()
                    _tg_send("▶ 재개")
                elif cmd == "중지":
                    stop_event.set()
                    pause_event.clear()
                    _tg_send("⛔ 중지")
                elif cmd == "현황":
                    _tg_send(
                        f"📊 현황\n"
                        f"공감: {stats['hearts']}\n"
                        f"댓글: {stats['comments']}\n"
                        f"이웃: {stats['neighbors']}\n"
                        f"실행 중: {'예' if stats['running'] else '아니오'}"
                    )
        except Exception:
            time.sleep(5)

# 로그 필터 키워드 — 댓글 수·휴식 관련 로그만 파일에 포함
_LOG_FILTER_KEYWORDS = [
    "댓글", "공감", "하트", "❤", "💬",
    "휴식", "대기", "sleep", "잠시", "초 대기",
    "차단", "스팸", "일시정지", "⛔", "⏸", "⚠",
    "오류", "에러", "error", "Error",
    "완료", "성공", "✅", "🎯",
    "블로그 방문", "방문",
    "[AI원문]", "[AI댓글]", "폴백", "AI 입력", "제목:",
]

def _filter_log_lines(log_lines):
    """_LOG_FILTER_KEYWORDS에 해당하는 로그 라인만 반환"""
    result = []
    for line in log_lines:
        if any(kw in line for kw in _LOG_FILTER_KEYWORDS):
            result.append(line)
    return result

def send_report(end_reason, error_detail=""):
    """작업 완료/오류/정지 시 텔레그램으로 결과 발송.
    요약 메시지 + 필터된 로그 파일 첨부.
    발송 전 먼저 파일에 저장 → 성공 시 파일 삭제.
    드라이버와 완전히 독립 — 강제 종료돼도 발송됨."""
    # 1) 먼저 파일에 저장
    _save_pending_report(end_reason, error_detail)

    start_str = time.strftime('%Y-%m-%d %H:%M:%S',
                              time.localtime(_session_start_time)) if _session_start_time else "-"
    end_str   = time.strftime('%Y-%m-%d %H:%M:%S')
    hearts    = stats.get("hearts", 0)
    comments  = stats.get("comments", 0)
    blocks    = stats.get("blocks", 0)
    skips     = stats.get("skips", 0)
    neighbors = stats.get("neighbors", 0)

    # 시간 계산
    total_sec  = (time.time() - _session_start_time) if _session_start_time else 0
    rest_total = sum(mins for _, mins, *_ in _rest_log)
    work_sec   = max(total_sec - rest_total * 60, 0)
    speed_work  = round(comments / (work_sec / 3600), 1) if work_sec > 0 else 0
    speed_total = round(comments / (total_sec / 3600), 1) if total_sec > 0 else 0

    def _fmt_dur(sec):
        h, m = int(sec // 3600), int((sec % 3600) // 60)
        return f"{h}시간 {m}분" if h else f"{m}분"

    # 날짜·요일
    _KR_DOW = ["월","화","수","목","금","토","일"]
    start_ts = time.localtime(_session_start_time) if _session_start_time else time.localtime()
    dow      = _KR_DOW[start_ts.tm_wday]
    date_str = time.strftime(f'%Y.%m.%d ({dow})', start_ts)
    s_hm     = time.strftime('%H:%M', start_ts)
    e_hm     = time.strftime('%H:%M')

    # 휴식 내역
    rest_section = ""
    if _rest_log:
        rest_lines = "\n".join(
            f"└ {t}  {cnt}개 후 {mins}분"
            for cnt, mins, t in _rest_log
        )
        rest_section = f"\n\n☕️ 휴식 시간 (총 {len(_rest_log)}회 / {rest_total:.1f}분)\n{rest_lines}"

    # 차단/스킵 특이사항
    issue_lines = []
    if blocks:
        for t, txt in _block_log:
            issue_lines.append(f"└ 차단 {blocks}회  {t} — {txt[:30]}")
    if skips:
        issue_lines.append(f"└ 스킵 {skips}개")
    if error_detail:
        issue_lines.append(f"└ ⚠️ {error_detail[:40]}")
    issue_section = ""
    if issue_lines:
        issue_section = "\n\n⛔️ 특이사항\n" + "\n".join(issue_lines)

    # 2) 요약 메시지
    msg = f"""📊 블로그봇 작업 보고

Overview
─────────────────────
❤️ 공감 {hearts}개   💬 댓글 {comments}개   ➕ 이웃 {neighbors}명

Details
─────────────────────
✅ 상태 : {end_reason}

⏰ 시간 : {date_str} {s_hm} - {e_hm}
└ 총 {_fmt_dur(total_sec)}  |  실작업 {_fmt_dur(work_sec)}
└ 속도 : 총 {speed_total}개/시간  |  실작업 {speed_work}개/시간{rest_section}{issue_section}"""

    # 3) 필터된 로그를 파일로 첨부
    filtered = _filter_log_lines(_session_log_lines)
    log_file_content = "\n".join(filtered) if filtered else "(해당 로그 없음)"
    log_filename = f"blog_log_{time.strftime('%Y%m%d_%H%M%S')}.txt"

    # 4) 요약 메시지 발송
    ok_msg = _tg_send(msg)
    # 5) 로그 파일 첨부 발송
    ok_file = _tg_send_file(log_filename, log_file_content, caption="📋 상세 로그 (댓글·공감·휴식 기준)")

    ok = ok_msg or ok_file
    if ok:
        try:
            if os.path.exists(_PENDING_REPORT_FILE):
                os.remove(_PENDING_REPORT_FILE)
        except Exception:
            pass
        glog("📬 텔레그램 알림 발송 완료!", "success")
    else:
        glog("  ⚠️ 텔레그램 발송 실패 — 다음 시작 때 재발송됩니다", "error")

    # 이번 주 누적에 세션 결과 합산
    _weekly_add(hearts, comments, neighbors)

def glog(msg, tag="info"):
    log_queue.put(("log", msg, tag))
    _session_append_log(msg)
    _web_append_log(msg, tag)   # 웹 대시보드 로그 deque 에도 기록

def gstats():
    log_queue.put(("stats",))

def gcurrent(name):
    log_queue.put(("current", name))




# =============================================
# AI 실패 시 fallback 댓글 풀
# =============================================
FALLBACK_COMMENTS = [
    "너무 좋아보여요~!! 포스팅 잘 봤어요🥰",
    "완전 유익한 포스팅이에요!! 잘 봤어요~🥰",
    "오늘도 좋은 글 감사해요~!! 자주 놀러올게요ㅎㅎ",
    "포스팅 넘 좋아요~!! 다음에 또 올게요🥰",
    "정말 좋은 정보 감사해요!! 잘 봤어요~",
    "너무 도움됐어요~!! 앞으로도 자주 올게요ㅎㅎ",
    "완전 공감돼요~!! 포스팅 잘 봤어요🥰",
    "정성스러운 포스팅 잘 봤어요~!! 감사해요ㅎㅎ",
    "넘 좋은 내용이에요~!! 자주 놀러올게요🥰",
    "유익한 정보 감사해요!! 다음에도 또 와야겠어요~",
]

def get_fallback_comment():
    return random.choice(FALLBACK_COMMENTS)


# =============================================
# 지역 키워드
# =============================================
MY_REGION_KEYWORDS = [
    "부천","중동","상동","송내","역곡","소사","오정","원미",
    "서울 홍대","서울 강남","서울 건대","서울 신촌","서울 명동",
    "서울 이태원","서울 압구정","서울 잠실","서울 판교","서울 사당",
    "서울 신림","서울 종로","서울 마포","서울 성수","서울 여의도",
    "서울 합정","서울 망원","서울 혜화","서울 노원","서울 은평",
    "서울 강서","서울 구로","서울 관악",
    "홍대","강남","건대","신촌","명동","이태원","압구정",
    "잠실","사당","신림","종로","마포","성수","여의도",
    "합정","망원","혜화","노원","은평","강서","구로","관악"
]
NEARBY_KEYWORDS = [
    "인천","수원","성남","고양","용인","안산","남양주","화성",
    "평택","의정부","시흥","파주","김포","광명","하남","군포",
    "안양","의왕","과천","분당","일산","동탄"
]
FAR_REGION_HINTS = [
    "울산","부산","대구","광주","대전","충주","전주","청주",
    "창원","천안","포항","경주","강릉","속초","여수","순천",
    "목포","춘천","원주","군산","익산","구미","진주","통영",
    "거제","양산","밀양","제주","언양","온산","울주"
]
FRANCHISE_KEYWORDS = [
    "맥도날드","버거킹","롯데리아","맘스터치","KFC","파파이스",
    "스타벅스","투썸플레이스","이디야","빽다방","메가커피","컴포즈",
    "엽기떡볶이","엽떡","신전떡볶이","엠제트떡볶이","청년다방",
    "홍콩반점","본죽","본도시락","CU","GS25","세븐일레븐",
    "이마트","홈플러스","성심당","뚜레쥬르","파리바게뜨",
    "던킨","크리스피크림","교촌치킨","BBQ","BHC","굽네치킨",
    "네네치킨","피자헛","도미노","피자알볼로","써브웨이",
    "노브랜드버거","한솥","김밥천국"
]


def detect_region(text):
    if any(k in text for k in MY_REGION_KEYWORDS): return "내지역"
    if any(k in text for k in NEARBY_KEYWORDS):    return "타지역"
    if any(k in text for k in FAR_REGION_HINTS):   return "타지역"
    return "없음"

def is_franchise(text):
    return any(k in text for k in FRANCHISE_KEYWORDS)


# =============================================
# 글 유형 분류
# =============================================
def classify_post_type(title, preview=""):
    t = (title + " " + preview).lower()
    if "가성비" in title.lower(): return "가성비"
    if any(k in t for k in ["레시피","집밥","만들기","요리법","반찬","홈쿡","자취요리"]): return "레시피"
    if any(k in t for k in ["카페","브런치","디저트","베이커리","케이크","마카롱","빵집","커피"]): return "카페"
    if any(k in t for k in ["맛집","식당","라멘","파스타","스테이크","초밥","치킨","떡볶이",
                              "국밥","삼겹살","곱창","막창","오마카세","레스토랑","냉면","쌀국수"]): return "식당"
    if any(k in t for k in ["뷰티","화장품","스킨케어","메이크업","립","파운데이션","선크림",
                              "세럼","앰플","토너","에센스","마스크팩","클렌징","네일","헤어"]): return "뷰티"
    if any(k in t for k in ["호텔","숙소","펜션","리조트","게스트하우스","풀빌라","글램핑"]): return "호텔"
    if any(k in t for k in ["여행","관광","투어","해외","국내여행","공항","비행기","명소"]): return "여행"
    if any(k in t for k in ["방법","팁","정보","추천","비교","리뷰","후기","꿀팁","노하우","가이드"]): return "정보"
    if any(k in t for k in ["강아지","고양이","반려견","반려묘","반려동물","펫","냥이","냥냥",
                              "멍멍","댕댕","포메","말티즈","시바","푸들","골든","래브라도",
                              "집사","냥집사","먹방하는강아지","먹방하는고양이"]): return "펫"
    return "기타"


def generate_restaurant_comment(title, preview="", region="없음"):
    text = title + " " + preview
    food_map = ["조개","소고기","삼겹살","갈비","치킨","피자","파스타","라멘","우동","초밥",
                "국밥","냉면","떡볶이","곱창","스테이크","돈까스","짜장","짬뽕","참치","연어",
                "족발","보쌈","된장찌개","김치찌개","삼계탕","흑돼지","한우"]
    detected = next((f for f in food_map if f in title), None) or next((f for f in food_map if f in preview), None)
    is_far = (region == "타지역")
    base = ["비주얼 대박🤤🤤","너무 맛있어보여요~🤤","진짜 맛있어보여요🤤🤤","완전 맛있어보여요~🤤"]
    if not is_far: base.append("기억해뒀다가 찾아가봐야겠어요!!")
    cond = []
    if not is_franchise(text): cond.append("요기 넘 궁금해요!!")
    if detected: cond.append(f"오랜만에 {detected} 땡기네요~")
    if "푸짐" in text or "양많" in text: cond.append("와 정말 푸짐하고 구성좋은데요?!")
    selected = random.sample(base + cond, min(2, len(base + cond)))
    comment = " ".join(selected)
    if region == "내지역":  comment = "여기 진짜 가보고싶어요~! " + comment
    elif region == "타지역": comment = comment + " 기억해둬야겠어요"
    return comment


def get_style_examples(post_type):
    styles = {
        "레시피": "- 너무 맛있어보여요~🤤\n- 정성 대박이에요😳\n- 저도 해봐야겠어요!!\n- 비주얼 완전 대박이에요🤤🤤",
        "카페":   "- 요기 넘 궁금해요!!\n- 분위기 너무 예쁘네요ㅎㅎ\n- 아기자기하고 좋네요 꼭 가보고싶어요!\n- 비주얼 대박이에요🤤",
        "뷰티":   "- 요거 궁금했는데 도움많이돼요!\n- 저도 써보고싶어요ㅎㅎ\n- 리뷰 너무 도움됐어요!",
        "호텔":   "- 되게 괜찮네요 기억해둬야겠어요~!\n- 여기 진짜 가보고싶어요!\n- 분위기 너무 좋아보여요ㅎㅎ",
        "가성비": "- 우와 진짜 가성비 괜찮은데요?!\n- 되게 괜찮네요~!\n- 이거 완전 득템이네요ㅎㅎ",
        "여행":   "- 너무 즐거워보여요 부러워요~!\n- 저도 꼭 가보고싶어요!\n- 사진이 너무 예뻐요ㅎㅎ",
        "정보":   "- 도움 많이 됐어요!\n- 알고싶었던 정보예요ㅎㅎ\n- 기억해둬야겠어요!\n- 이런 정보 진짜 유용해요~!",
        "펫":     "- 너무 귀여워요ㅎㅎ 보는것만으로도 힐링돼요~!\n- 진짜 너무 사랑스러워요🥰\n- 사진 보는 것만으로도 행복해져요!!\n- 완전 귀여워요😻 에너지 받고가요~!",
    }
    return styles.get(post_type, None)


# =============================================
# Gemini AI 댓글 생성
# =============================================
def generate_comment(title, preview=""):
    global gemini_model
    import re

    post_type = classify_post_type(title, preview)
    glog(f"  유형: {post_type}", "dim")
    region = detect_region(title + " " + preview)
    glog(f"  지역: {region}", "dim")

    # 식당 유형은 패턴 댓글로 바로 처리
    if post_type == "식당":
        c = generate_restaurant_comment(title, preview, region)
        glog(f"  [패턴] {c}", "success")
        return c

    # 나머지 유형은 Gemini AI로 처리
    style = get_style_examples(post_type)
    region_ins = ""
    if region == "내지역":  region_ins = "\n- '여기 진짜 가보고싶어요~!' 표현 포함"
    elif region == "타지역": region_ins = "\n- '감사합니다!' 표현 포함"
    recipe_ins = ""
    if post_type == "레시피":
        recipe_ins = "\n- 장소 방문 표현 절대 금지. 칭찬 표현만."
    if post_type == "펫":
        recipe_ins = "\n- 음식/맛집 표현 절대 금지('맛있어보여요', '비주얼 대박', '먹어보고싶어요' 등). 동물 귀여움/힐링 표현만."

    style_ins = f"\n[예시]\n{style}\n위 느낌으로 새롭게 써줘." if style else ""
    preview_text = preview[:200] if preview and preview.strip() else "없음"

    prompt = (
        f"너는 네이버 블로그 글을 읽은 방문자(이웃)야. 글 작성자가 아님. 절대로 블로그 주인이나 가게 사장 입장으로 쓰지 마. "
        f"글쓴이한테 댓글 다는 독자 입장으로만 써. 귀엽고 발랄한 20대 한국 여성 방문자.\n"
        f"반드시 한국어 존댓말만. 반말 절대 금지.\n"
        f"특히 '~다', '~야', '~구나', '~해', '~당', '~잖아', '~거든' 같은 반말 어미는 "
        f"문장 중간이든 끝이든 절대 사용 금지. 모든 문장은 '~요', '~네요', '~군요', '~어요', '~겠어요' 로 끝낼 것.\n\n"
        f"제목: {title}\n미리보기: {preview_text}\n{style_ins}\n\n"
        f"[댓글 구조 - 반드시 이 순서로 이어서 한 덩어리로 작성]\n"
        f"① 메아리 어구: 상대방 행동/상황을 그대로 받아서 '~하셨군요 / ~가셨네요 / ~드셨군요' 등으로 시작\n"
        f"② 공감/감탄: 글 내용 보고 느낀 감정이나 반응 (맛있어보여요, 분위기 너무 좋아보여요 등)\n"
        f"③ 마무리: '기억해둬야겠어요~!', '지인에게 추천해줘야겠어요!', '나중에 꼭 가보고싶어요~!' 같은 표현. "
        f"절대 금지: '저도 임당인데', '저도 반려견 키우는데', '저도 아이 키우는데', "
        f"'남편이랑 가봐야겠어요', '남편한테 추천해줘야겠어요', "
        f"'저도 결혼 준비 중인데', '웨딩 준비하고 있어서', "
        f"'저도 운전면허 없는데', '저도 차가 없어서' 처럼 "
        f"사실인지 모르는 개인 상황·가족 관계·인생 이벤트·개인 사정을 꾸며내는 표현 사용 금지. "
        f"공감은 글 내용에 대한 감상으로만. 마무리는 '기억해둬야겠어요', '지인에게 추천해줘야겠어요', '꼭 가보고싶어요' 등으로.\n\n"
        f"[예시 형태]\n"
        f"'~하셨군요🥰 [공감 한마디]~ 기억해둬야겠어요~!!'\n\n"
        f"규칙: 한글만, 전체 2~3문장, 존댓말(~요/~네요/~어요로 끝낼 것), 느낌표 '!!' 물결표 '~' 자주 써{region_ins}{recipe_ins}\n댓글만 출력"
    )

    try:
        response = gemini_model.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                temperature=0.85,
                top_p=0.95,
                max_output_tokens=1000,  # thinking 토큰 포함해서 넉넉하게
                thinking_config=genai_types.ThinkingConfig(thinking_budget=0),  # thinking 비활성화 → 댓글에 토큰 집중
            )
        )
        # response.text가 None일 수 있으므로 안전하게 처리
        raw = response.text if response.text else ""
        glog(f"  [AI원문] {repr(raw[:100])}", "dim")  # 디버그: AI 응답 원문 확인

        result = raw.strip()
        result = result.strip('"').strip("'").strip()
        result = re.sub(r'^[\d]+[.\)]\s*', '', result)

        # 줄바꿈이 있을 경우 완결 문장 찾기 (첫 줄만 쓰면 잘릴 수 있음)
        lines = [l.strip() for l in result.split('\n') if l.strip()]
        if lines:
            # 가장 긴 줄 또는 '요'로 끝나는 첫 줄 선택
            complete = next((l for l in lines if re.search(r'[요용ㅎ!~🥰😳]$', l)), None)
            result = complete if complete else lines[0]

        # 프롬프트 역출력 필터 (Gemini가 프롬프트를 그대로 반환한 경우)
        _prompt_leak = ['아래의 규칙', '댓글을 작성해주세요', '귀엽고 발랄한 20대', '규칙에 맞춰서']
        if any(kw in result for kw in _prompt_leak):
            glog("  [!] AI가 프롬프트 역출력 → 랜덤 폴백", "warn")
            return get_fallback_comment()

        # 한자 필터
        for char in result:
            if '一' <= char <= '鿿':
                glog("  [!] 한자 감지 → 랜덤 폴백", "warn")
                return get_fallback_comment()

        # 길이 검사
        if len(result) < 10:
            glog(f"  [!] AI 결과 너무 짧음({result}) → 랜덤 폴백", "warn")
            return get_fallback_comment()

        # 1인칭 교체 — '나' → '저'
        result = re.sub(r'\b나\b', '저', result)         # 나 → 저
        result = re.sub(r'내가', '제가', result)          # 내가 → 제가
        result = re.sub(r'나도', '저도', result)          # 나도 → 저도
        result = re.sub(r'나는', '저는', result)          # 나는 → 저는
        # 문장 중간 반말 감탄 교체 (전체 치환)
        result = re.sub(r'미쳤다(는)', r'미쳤다\1', result)   # 미쳤다는 → 그대로 (보존)
        result = re.sub(r'미쳤다(?!는)', '미쳤네요', result)  # 미쳤다 → 미쳤네요 (단독)
        result = re.sub(r'대박이다(는)', r'대박이다\1', result)
        result = re.sub(r'대박이다(?!는)', '대박이네요', result)
        result = re.sub(r'보인다(는)', r'보인다\1', result)
        result = re.sub(r'보인다(?!는)', '보이네요', result)   # 좋아 보인다 → 좋아 보이네요
        result = re.sub(r'같다(는)', r'같다\1', result)
        result = re.sub(r'같다(?!는)', '같네요', result)       # ~것 같다 → ~것 같네요
        # 끝 어미 교체
        result = re.sub(r'구나(([!~?]{1,2}|[🥰😳])*)$', r'군요\1', result)   # 이거구나 → 이거군요
        result = re.sub(r'[다당](([!~?]{1,2}|[🥰😳])*)$', r'네요\1', result)  # ~다!! → ~네요!!
        result = re.sub(r'해(([!~?]{0,2}|[🥰😳])*)$', r'해요\1', result)      # 궁금해 → 궁금해요
        result = re.sub(r'야(([!~?]{1,2}|[🥰😳])*)$', r'네요\1', result)      # 무슨 일이야!! → 무슨 일이네요!!
        # 감탄사+느낌표 교체 — '대박!!', '실화!!' → '대박이네요!!', '실화네요!!'
        def fix_exclaim(m):
            word, suffix = m.group(2), m.group(3)
            connector = '네요' if word in ('미쳤', '헐', '와우', '우와') else '이네요'
            return m.group(1) + word + connector + suffix
        result = re.sub(r'^(.*)(대박|실화|미쳤|헐|와우|우와)(([!~?]{1,2}|[🥰😳])*)$', fix_exclaim, result)

        # 정보성 글이면 끝에 '감사합니다!' 추가
        if post_type == "정보":
            result = result.rstrip("~! ") + " 감사합니다!"

        # 완결 검증 — 10자 이상이고 비어있지 않으면 OK (이모지 끝도 허용)
        if result and len(result) >= 10:
            glog(f"  [AI댓글] {result}", "success")
            return result

        glog(f"  [!] AI 결과 너무 짧음({result[:40]}) → 랜덤 폴백", "warn")
        return get_fallback_comment()
    except Exception as e:
        glog(f"  Gemini 오류 → 랜덤 폴백: {e}", "warn")
        return get_fallback_comment()


# =============================================
# 이웃 모드: 댓글 닉네임 수집
# =============================================
def get_commenters(driver):
    commenters = []
    seen = set()

    # 피드 댓글 패널 로드 대기 (최대 5초)
    # 피드에서 열리는 댓글창: /html/body/div[1]/div[3]/div[2]/.../div[4]/ul/li[1]
    feed_panel_xp = '/html/body/div[1]/div[3]/div[2]/div/div/div/div[2]/div/div/div/div[4]/ul'
    for _ in range(10):
        try:
            driver.find_element(By.XPATH, feed_panel_xp + '/li[1]')
            break
        except:
            rnd(0.3, 0.5)

    rnd(0.5, 1.0)

    # 피드 댓글 패널에서 li 수집
    li_els = []
    try:
        li_els = driver.find_elements(By.XPATH, feed_panel_xp + '/li')
    except: pass

    # 피드 패널 못 찾으면 naverComment 기반(블로그 포스팅 직접 접근 시) 시도
    if not li_els:
        panel_xp = '//*[contains(@id,"naverComment_") and contains(@id,"_wai_u_cbox_content_wrap_tabpanel")]'
        try:
            li_els = driver.find_elements(By.XPATH, panel_xp + '/ul/li')
        except: pass

    # 그래도 없으면 cbox_list 클래스로 시도
    if not li_els:
        try:
            li_els = driver.find_elements(By.XPATH, '//*[contains(@class,"cbox_list")]/li')
        except: pass

    glog(f"  댓글 li {len(li_els)}개", "dim")

    for li_el in li_els:
        try:
            # 확인된 구조: .//div[1]/div/div[1]/span[1]/a (class="u_cbox_name", href에 blogId=)
            a = li_el.find_element(By.XPATH, './/div[1]/div/div[1]/span[1]/a')
            href = a.get_attribute('href') or ''
            if not href or 'blogId=' not in href:
                continue
            bid = href.split('blogId=')[1].split('&')[0]
            if not bid or bid in seen:
                continue
            # 닉네임: span.u_cbox_nick
            try:
                nick = li_el.find_element(By.XPATH, './/span[@class="u_cbox_nick"]').text.strip()
            except:
                nick = bid
            seen.add(bid)
            commenters.append(bid)
            glog(f"  수집: {nick} → {bid}", "dim")
        except: continue
    # 본인 블로그 ID 필터 (내 게시글에 내가 댓글 달지 않도록)
    if _my_blog_id:
        before = len(commenters)
        commenters = [b for b in commenters if b.lower() != _my_blog_id.lower()]
        if len(commenters) < before:
            glog(f"  [본인 스킵] {_my_blog_id} 제외됨", "dim")
    glog(f"  총 {len(commenters)}명", "info")
    return commenters


# =============================================
# 공통: 블로그 방문 → 정렬 → 좋아요+댓글
# =============================================
def do_like_and_comment(driver, blog_id, content_div):
    # 좋아요 — 카드형/리스트형 레이아웃 무관하게 aria-pressed 속성으로 탐색
    # contentslist_block 안의 첫 번째 또는 두 번째 li 내 좋아요 버튼을 찾음
    liked = False
    like_a = None
    _like_base = f'//*[@id="contentslist_block"]/div[{content_div}]'
    _like_candidates = [
        # 첫 번째 li 안의 공감 버튼 (aria-pressed 기준)
        _like_base + '//ul/li[1]//a[@aria-pressed]',
        # 두 번째 li (카드형에서 첫 포스팅이 li[2]인 경우)
        _like_base + '//ul/li[2]//a[@aria-pressed]',
        # contentslist_block 전체 범위에서 첫 번째 공감 버튼
        '//*[@id="contentslist_block"]//a[@aria-pressed][1]',
    ]
    for _xp in _like_candidates:
        try:
            like_a = driver.find_element(By.XPATH, _xp)
            break
        except Exception:
            pass
    try:
        if like_a is None:
            raise Exception("좋아요 버튼 미발견")
        if like_a.get_attribute('aria-pressed') == 'true':
            glog(f"  [{blog_id}] 이미 좋아요 → 스킵", "dim")
            return False
        try:
            like_a.find_element(By.XPATH, './span/span').click()
        except Exception:
            driver.execute_script("arguments[0].click();", like_a)
        rnd(0.3, 1.2)
        liked = True
        stats["hearts"] += 1
        gstats()
        glog(f"  [{blog_id}] 🩷 좋아요 완료", "success")
    except:
        glog(f"  [{blog_id}] 좋아요 버튼 없음", "dim")

    # 제목/미리보기
    title_text = None; preview_text = ""

    # 제목 XPath 후보: div/div[2]/ul + div[3]/div/div[2]/ul 레이아웃 모두 포함
    _title_xpaths = []
    for _mid in ['div/div[2]', 'div[3]/div/div[2]']:
        for _d1 in ['div[1]', 'div[2]']:
            for _d2 in ['div[2]', 'div[3]']:
                base = (f'//*[@id="contentslist_block"]/div[{content_div}]/{_mid}/ul/li[1]'
                        f'/{_d1}/{_d2}/div[2]/a')
                _title_xpaths += [base + '/strong/span', base + '/strong', base]

    # XPath 순서대로 제목 추출 시도
    for _txp in _title_xpaths:
        if title_text:
            break
        try:
            _t = driver.find_element(By.XPATH, _txp).text.strip().split('\n')[0]
            if _t:
                title_text = _t
        except: pass

    # 최후 수단: 페이지 document.title
    if not title_text:
        try:
            title_text = driver.execute_script("return document.title;").strip()
            glog(f"  제목(페이지타이틀): {title_text[:40]}", "warn")
        except: pass

    if title_text:
        glog(f"  제목: {title_text[:40]}", "dim")
    else:
        glog(f"  [{blog_id}] 제목 추출 전부 실패 → 폴백", "warn")

    # 미리보기 XPath 후보: div/div[2]/ul + div[3]/div/div[2]/ul 레이아웃 모두 포함
    for _mid in ['div/div[2]', 'div[3]/div/div[2]']:
        for _d1 in ['div[1]', 'div[2]']:
            for _d2 in ['div[2]', 'div[3]']:
                if preview_text:
                    break
                try:
                    _p = driver.find_element(By.XPATH,
                        f'//*[@id="contentslist_block"]/div[{content_div}]/{_mid}/ul/li[1]'
                        f'/{_d1}/{_d2}/div[2]/a/span/span').text.strip()
                    if _p:
                        preview_text = _p
                except: pass
    if preview_text:
        glog(f"  미리보기: {preview_text[:40]}…", "dim")

    # 댓글 생성
    comment_text = get_fallback_comment()
    if title_text:
        glog(f"  → AI 입력: 제목=[{title_text[:30]}] 미리보기=[{preview_text[:30] if preview_text else '없음'}]", "dim")
        c = generate_comment(title_text, preview_text)
        if c:
            comment_text = c
        else:
            glog(f"  [{blog_id}] AI 실패 → 랜덤 기본 댓글", "warn")

    # 댓글창 열기
    try:
        # 댓글 버튼 XPath: div/div[2]/ul (기본) + div[3]/div/div[2]/ul (신규 레이아웃)
        _btn_xpaths = []
        for _cdiv in [content_div, content_div + 1]:
            for _mid in ['div/div[2]', 'div[3]/div/div[2]']:
                for _d1 in ['div[1]', 'div[2]']:
                    for _d3 in ['div[3]', 'div[4]', 'div[2]']:
                        _btn_xpaths.append(
                            f'//*[@id="contentslist_block"]/div[{_cdiv}]/{_mid}/ul/li[1]'
                            f'/{_d1}/{_d3}/div[2]/button'
                        )
        # 포괄적 폴백
        _btn_xpaths += [
            f'//*[@id="contentslist_block"]//button[.//i[contains(@class,"u_cnt")]]',
            f'//*[@id="contentslist_block"]//div[contains(@class,"comment")]//button',
        ]
        comment_btn = None
        # 버튼이 나타날 때까지 최대 10회 스크롤하며 대기, 각 XPath 순차 시도
        for _retry in range(10):
            for _xp in _btn_xpaths:
                try:
                    comment_btn = driver.find_element(By.XPATH, _xp)
                    break
                except Exception:
                    pass
            if comment_btn:
                break
            driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.PAGE_DOWN)
            rnd(0.2, 0.4)
        if comment_btn is None:
            glog(f"  [{blog_id}] 댓글창 열기 실패 (버튼 미발견)", "error")
            return liked
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", comment_btn)
        rnd(0.3, 0.6)
        try:
            comment_btn.click()
        except Exception:
            driver.execute_script("arguments[0].click();", comment_btn)
        rnd(3.5, 6.5)  # 댓글창 로딩 대기
    except Exception as e:
        glog(f"  [{blog_id}] 댓글창 열기 실패: {e}", "error")
        return liked

    # 입력 & 등록
    try:
        box = driver.find_element(By.XPATH, '//*[@id="naverComment__write_textarea"]')
        driver.execute_script("arguments[0].click();", box)
        rnd(1.0, 2.5)  # 클릭 후 포커스 대기
        driver.execute_script("arguments[0].innerText = arguments[1];", box, comment_text)
        rnd(1.5, 3.5)  # 입력 후 잠깐 머무는 척
        sub = driver.find_element(By.XPATH,
            '//*[@id="naverComment"]/div/div[7]/div[1]/form/fieldset/div/div/div[6]/button')
        driver.execute_script("arguments[0].click();", sub)
        rnd(0.8, 2.0)  # 등록 후 대기
        result = dismiss_alert(driver)
        if result == 'blocked':
            return 'comment_limit'
        if result == 'ratelimit':
            return 'comment_limit'  # 과다 작성 → 현재 블로그 댓글 포기, 다음으로 (스팸차단 아님)
        if result == 'skip':
            return liked   # 블로그 주인 스팸차단 — 댓글만 스킵, 공감은 이미 완료
        stats["comments"] += 1
        gstats()
        glog(f"  [{blog_id}] 💬 댓글: {comment_text}", "success")
        return True
    except Exception as e:
        glog(f"  [{blog_id}] 댓글 등록 실패: {e}", "error")
        auto_pause("오류 발생")
        wait_if_paused()
        return liked


def _get_second_post_comment_count(driver):
    """두 번째 게시글의 댓글수를 반환. 실패 시 0 반환."""
    COMMENT_XPATH = ("/html/body/div[1]/div[4]/div[6]/div[3]/div/div[2]"
                     "/ul/li[1]/div[2]/div[3]/div[2]/button/span")
    try:
        el = driver.find_element(By.XPATH, COMMENT_XPATH)
        txt = el.text.strip().replace(",", "")
        count = int(txt) if txt.isdigit() else 0
        glog(f"  두번째 게시글 댓글수: {count}", "dim")
        return count
    except Exception:
        return 0


def _select_group_and_confirm(driver, sel, group_name):
    """지정 그룹 선택 후 확인 클릭. 그룹 초과면 False, 성공이면 True, 에러면 None."""
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    ALERT_CLOSE   = '//*[@id="_alertLayerClose"]'
    CONFIRM_XPATH = "/html/body/ui-view/div[2]/a[2]"
    DONE_XPATH    = "/html/body/ui-view/div[2]/div/div[2]/a"

    try:
        sel.select_by_visible_text(group_name)
    except Exception:
        try:
            # 텍스트 매칭 실패 시 value로 시도
            for opt in sel.options:
                if group_name in opt.text:
                    sel.select_by_value(opt.get_attribute("value"))
                    break
            else:
                return None  # 그룹 못 찾음
        except Exception:
            return None
    rnd(0.3, 0.7)

    try:
        confirm_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, CONFIRM_XPATH))
        )
        driver.execute_script("arguments[0].click();", confirm_btn)
        rnd(1.0, 2.0)
    except Exception:
        return None

    # 초과 알럿 확인
    try:
        alert_close = WebDriverWait(driver, 2).until(
            EC.element_to_be_clickable((By.XPATH, ALERT_CLOSE))
        )
        driver.execute_script("arguments[0].click();", alert_close)
        rnd(0.5, 1.0)
        return False  # 초과
    except Exception:
        pass

    # 완료 확인
    try:
        WebDriverWait(driver, 3).until(
            EC.presence_of_element_located((By.XPATH, DONE_XPATH))
        )
        return True  # 성공
    except Exception:
        return None  # 불명확


def try_add_neighbor(driver, neighbor_msg, prem_group="", prem_min=100, skip_below_prem=False):
    """이웃추가 시도.
    - 두번째 게시글 댓글수 >= prem_min → prem_group 우선 시도
    - 그 외 → prem_group 제외 나머지 그룹 순환
    - skip_below_prem=True 이면 댓글수 미달 시 이웃추가 자체 스킵
    이미 이웃이거나 실패 시 조용히 패스."""
    global _neighbor_limit_reached
    if _neighbor_limit_reached:
        return  # 5000명 한도 이미 도달 — 이후 모든 이웃추가 스킵
    try:
        from selenium.webdriver.support.ui import Select as SeleniumSelect
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        # 1) 두번째 게시글 댓글수 확인
        comment_count = _get_second_post_comment_count(driver)
        use_premium = prem_group and (comment_count >= prem_min)

        # 조건 미달 스킵 옵션
        if skip_below_prem and not use_premium:
            glog(f"  댓글수 {comment_count} < {prem_min} — 이웃추가 스킵", "dim")
            return
        if use_premium:
            glog(f"  ⭐ 댓글수 {comment_count} ≥ {prem_min} — 프리미엄 그룹 '{prem_group}' 사용", "info")
        else:
            glog(f"  댓글수 {comment_count} — 일반 그룹 사용", "dim")

        # 2) 이웃추가 버튼 확인
        BTN_XPATH = "/html/body/div[1]/div[3]/div/div[3]/div[1]/button"
        try:
            btn = driver.find_element(By.XPATH, BTN_XPATH)
            btn_text = btn.text.strip()
        except Exception:
            glog("  이웃추가 버튼 없음 — 패스", "dim")
            return

        if btn_text != "이웃추가":
            glog(f"  이웃추가 불가 (버튼: '{btn_text}') — 패스", "dim")
            return

        glog("  ➕ 이웃추가 시도...", "info")
        driver.execute_script("arguments[0].click();", btn)
        rnd(1.5, 3.0)

        # 3-0) 5000명 한도 초과 팝업 감지
        try:
            page_src = driver.page_source
            if "5,000명이 초과되어" in page_src or "이웃을 추가할 수 없습니다" in page_src:
                _neighbor_limit_reached = True
                glog("  ⚠️ 이웃 5000명 한도 도달 — 이후 이웃추가 중단", "warn")
                _tg_send("⚠️ 이웃 5000명 한도 도달 — 이웃추가 기능 자동 중단")
                return
        except Exception:
            pass

        # 3) 서로이웃 라디오 선택
        try:
            radio = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.XPATH, '//*[@id="bothBuddyRadio"]'))
            )
            driver.execute_script("arguments[0].click();", radio)
            rnd(0.5, 1.2)
        except Exception:
            # 라디오 없음 → "서로이웃 신청 진행중" 팝업인지 확인 후 닫기
            PENDING_CLOSE_XPATH = "/html/body/div[1]/div[5]/div[2]/div/div/div/div[2]/button[1]"
            try:
                pending_btn = driver.find_element(By.XPATH, PENDING_CLOSE_XPATH)
                driver.execute_script("arguments[0].click();", pending_btn)
                rnd(1.0, 2.0)
                glog("  서로이웃 신청 진행중 팝업 닫음 — 패스", "dim")
            except Exception:
                glog("  서로이웃 라디오 없음 — 패스", "dim")
            return

        # 4) 메시지 입력
        MSG_XPATH = "/html/body/ui-view/div[3]/form/fieldset/div/div[2]/div[3]/div/textarea"
        try:
            msg_area = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.XPATH, MSG_XPATH))
            )
            msg_area.clear()
            msg_area.send_keys(neighbor_msg)
            rnd(0.5, 1.0)
        except Exception:
            glog("  메시지 textarea 없음 — 메시지 생략", "dim")

        # 5) 그룹 선택 — 프리미엄/일반 분기
        SELECT_XPATH = '//*[@id="buddyGroupSelect"]'
        try:
            sel_el = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.XPATH, SELECT_XPATH))
            )

            # 그룹 선택창 열릴 때 뜨는 팝업 "7일간 보지 않기" 처리
            try:
                POPUP_XPATH = "/html/body/div[6]/div[1]/div[2]/div[2]/div[2]/div[1]/div/div/div[6]/div/div[2]/div/div[2]/label"
                no_show = driver.find_element(By.XPATH, POPUP_XPATH)
                driver.execute_script("arguments[0].click();", no_show)
                rnd(0.5, 1.0)
                glog("  팝업 '7일간 보지 않기' 클릭", "dim")
            except Exception:
                pass

            sel = SeleniumSelect(sel_el)
            all_options = [opt.text.strip() for opt in sel.options]

            if use_premium:
                # 프리미엄 그룹만 시도
                result = _select_group_and_confirm(driver, sel, prem_group)
                if result is True:
                    glog(f"  ✅ 이웃추가 완료! (프리미엄 그룹: {prem_group})", "success")
                    stats["neighbors"] = stats.get("neighbors", 0) + 1
                elif result is False:
                    glog(f"  프리미엄 그룹 '{prem_group}' 초과 — 이웃추가 건너뜀", "warn")
                else:
                    glog(f"  프리미엄 그룹 '{prem_group}' 시도 실패", "dim")
            else:
                # 프리미엄 그룹 제외한 나머지 순환
                normal_options = [name for name in all_options if name != prem_group]
                if not normal_options:
                    normal_options = all_options  # 프리미엄 그룹만 있는 경우 fallback

                for grp_name in normal_options:
                    result = _select_group_and_confirm(driver, sel, grp_name)
                    if result is True:
                        glog(f"  ✅ 이웃추가 완료! (그룹: {grp_name})", "success")
                        stats["neighbors"] = stats.get("neighbors", 0) + 1
                        return
                    elif result is False:
                        glog(f"  그룹 '{grp_name}' 초과 — 다음 그룹 시도", "dim")
                        continue
                    else:
                        return  # 확인 불가

                glog("  모든 일반 그룹이 가득 찼습니다 — 이웃추가 건너뜀", "warn")

        except Exception as e:
            glog(f"  그룹 선택 오류: {e}", "dim")

    except Exception as e:
        glog(f"  이웃추가 예외: {e}", "dim")


def visit_blog_and_interact(driver, blog_id):
    url = f"https://m.blog.naver.com/{blog_id}?tab=1"
    driver.get(url)
    rnd(4.0, 7.0)  # 페이지 로딩 대기
    alert_result = dismiss_alert(driver)
    if alert_result == 'blocked':
        return 'comment_limit'
    if alert_result == 'skip':
        return None

    # ── 이웃추가 (설정된 경우) ──
    if _worker_cfg.get('add_neighbor') and _worker_cfg.get('neighbor_msg'):
        try_add_neighbor(
            driver,
            _worker_cfg['neighbor_msg'],
            prem_group=_worker_cfg.get('prem_group', ''),
            prem_min=_worker_cfg.get('prem_min_comments', 100),
            skip_below_prem=_worker_cfg.get('skip_below_prem', False)
        )
        # 이웃추가 후 원래 블로그로 복귀
        if driver.current_url != url:
            driver.get(url)
            rnd(3.0, 5.0)
        # 복귀 후 "서로이웃 신청 진행중" 팝업이 뜨면 취소 버튼 클릭 (항상 체크)
        try:
            PENDING_CANCEL_XPATH = "/html/body/div[1]/div[5]/div[2]/div/div/div/div[2]/button[1]"
            cancel_btn = driver.find_element(By.XPATH, PENDING_CANCEL_XPATH)
            driver.execute_script("arguments[0].click();", cancel_btn)
            rnd(1.0, 2.0)
            glog("  서로이웃 신청 진행중 팝업 취소 클릭", "dim")
        except Exception:
            pass

    content_div = 3
    for bd, cd in [('div[2]', 3), ('div[1]', 2)]:
        try:
            sb = driver.find_element(By.XPATH, f'//*[@id="contentslist_block"]/{bd}/div/div/button[3]/i')
            driver.execute_script("arguments[0].scrollIntoView();", sb)
            rnd(0.5, 1.5)
            driver.execute_script("arguments[0].click();", sb)
            rnd(1.5, 3.5)
            content_div = cd
            glog(f"  최신순 정렬 완료 (div[{cd}])", "dim")
            break
        except: continue
    else:
        glog(f"  정렬 버튼 없음", "dim")
        rnd(0.7, 1.8)
    return do_like_and_comment(driver, blog_id, content_div)


# =============================================
# 검색 모드: 블로그 ID 수집
# =============================================
def collect_blog_ids_from_search(driver, keyword, order_type, visit_count):
    enc = quote(keyword)
    url = (f"https://m.blog.naver.com/SectionPostSearch.naver"
           f"?orderType={order_type}&pageAccess=option&periodType=year&searchValue={enc}")
    driver.get(url)
    rnd(2.0, 4.5)

    collected = []
    seen = set()
    scroll_attempts = 0
    prev_item_count = 0
    item_idx = 1

    while len(collected) < visit_count:
        while True:
            try:
                nick_el = driver.find_element(By.XPATH,
                    f'//*[@id="root"]/div[2]/div[2]/div[2]/div/div[2]/div[{item_idx}]/div/div/a[2]/span[2]')
                a_el = driver.find_element(By.XPATH,
                    f'//*[@id="root"]/div[2]/div[2]/div[2]/div/div[2]/div[{item_idx}]/div/div/a[2]')
                href = a_el.get_attribute('href') or ''
                blog_id = ''
                if 'blog.naver.com/' in href:
                    blog_id = href.split('blog.naver.com/')[-1].split('?')[0].split('/')[0].strip()
                elif 'blogId=' in href:
                    blog_id = href.split('blogId=')[-1].split('&')[0].strip()

                try:
                    title_hint = driver.find_element(By.XPATH,
                        f'//*[@id="root"]/div[2]/div[2]/div[2]/div/div[2]/div[{item_idx}]/div/div/a[1]'
                    ).text.strip()
                except: title_hint = ""

                if blog_id and blog_id not in seen:
                    seen.add(blog_id)
                    collected.append((blog_id, title_hint))
                    glog(f"  수집 [{len(collected)}] {blog_id} | {title_hint[:20]}", "dim")
                item_idx += 1
            except:
                break

        glog(f"  '{keyword}' 수집 현황: {len(collected)}/{visit_count}", "info")
        if len(collected) >= visit_count:
            break

        next_clicked = False
        for xp in ['//a[contains(@class,"btn_next")]', '//button[contains(@class,"btn_next")]']:
            try:
                btn = driver.find_element(By.XPATH, xp)
                driver.execute_script("arguments[0].click();", btn)
                rnd(2.0, 4.5)
                next_clicked = True
                break
            except: continue

        if not next_clicked:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            rnd(1.8, 3.5)
            scroll_attempts += 1
            cur_count = len(collected)
            if cur_count == prev_item_count:
                if scroll_attempts >= 3:
                    glog(f"  더 이상 결과 없음, 종료", "warn")
                    break
            else:
                scroll_attempts = 0
            prev_item_count = cur_count

    return collected[:visit_count]


# =============================================
# 검색 모드 메인
# =============================================
def batch_rest(batch_count, driver=None):
    """배치 단위 완료 후 긴 휴식 (5~30분 랜덤)
    휴식 중 30초마다 실제 페이지 이동으로 크롬 절전 방지.
    반환값: 'session_dead' = 세션 죽음(재개 후 처음부터 재시작 필요), None = 정상"""
    rest_sec = random.uniform(5 * 60, 30 * 60)
    _rest_log.append((batch_count, round(rest_sec / 60, 1), time.strftime('%H:%M')))
    glog(f"  ☕ 배치 {batch_count}개 완료 — {rest_sec/60:.1f}분 휴식 중...", "warn")
    _tg_send(f"☕ 배치 {batch_count}개 완료 — {rest_sec/60:.1f}분 휴식 시작 (이웃추가 {stats.get('neighbors', 0)}개)")
    deadline = time.time() + rest_sec
    last_keepalive = time.time()
    _base = f"https://m.blog.naver.com/{_my_blog_id}" if _my_blog_id else "https://m.blog.naver.com"
    _keepalive_urls = [
        _base,
        _base + "?tab=1",
        _base + "?tab=2",
    ]
    _keepalive_idx = 0
    while time.time() < deadline:
        if stop_event.is_set():
            return None
        time.sleep(1)
        if driver and time.time() - last_keepalive >= 30:
            try:
                # 내 블로그 탭 돌아다니며 크롬 활성 상태 유지
                url = _keepalive_urls[_keepalive_idx % len(_keepalive_urls)]
                _keepalive_idx += 1
                driver.get(url)
                time.sleep(random.uniform(2.0, 4.0))
                driver.execute_script(f"window.scrollTo(0, {random.randint(100, 400)});")
            except Exception as e:
                err_str = str(e)
                if "invalid session id" in err_str.lower():
                    glog(f"  🔄 휴식 중 세션 끊김 — 재연결 시도...", "warn")
                    _tg_send("🔄 휴식 중 크롬 세션 끊김 — 재연결 시도 중")
                    try:
                        _launch_chrome_if_needed()
                        reconnected = _connect_chrome()
                        global shared_driver
                        shared_driver = reconnected
                        driver = reconnected
                        glog("  ✅ 재연결 성공 — 휴식 계속", "success")
                        _tg_send("✅ 재연결 성공 — 휴식 계속")
                        last_keepalive = time.time()
                        continue
                    except Exception:
                        glog(f"  ⚠️ 재연결 실패 — session_dead 반환", "error")
                        _tg_send("⚠️ 휴식 중 재연결 실패 — 작업 재개 시도")
                        return 'session_dead'
                else:
                    glog(f"  ⚠️ 휴식 중 오류: {e}", "error")
                    auto_pause("오류 발생")
                    wait_if_paused()
                    return 'session_dead'
            last_keepalive = time.time()
    glog("  ▶ 휴식 끝, 재개!", "success")
    _tg_send("▶ 휴식 끝, 작업 재개!")
    return None


def search_and_interact(driver, keywords, visit_count, order_type):
    batch_size = random.randint(20, 50)
    batch_comment_start = stats["comments"]  # 댓글 기준 배치

    for keyword in keywords:
        glog(f"━━  검색어: '{keyword}'  ({'최신순' if order_type=='date' else '관련도순'})  ━━", "header")
        gcurrent(f"검색: {keyword}")
        blog_list = collect_blog_ids_from_search(driver, keyword, order_type, visit_count)
        glog(f"  총 {len(blog_list)}개 방문 예정", "info")

        visited = set()
        restart = False
        for i, (blog_id, title_hint) in enumerate(blog_list):
            if blog_id in visited: continue
            visited.add(blog_id)
            gcurrent(f"{blog_id}")
            glog(f"  [{i+1}/{len(blog_list)}] {blog_id}  ({title_hint[:20]})", "bold")
            if stop_event.is_set():
                glog("⛔ 강제 중지됨", "error")
                return
            result = visit_blog_and_interact(driver, blog_id)
            if result == 'comment_limit':
                glog("⚠️ 댓글 한도 초과! 중단합니다.", "error")
                return
            delay = random.uniform(3.0, 8.0)
            glog(f"  ⏱ {delay:.1f}초 대기", "dim")
            time.sleep(delay)
            wait_if_paused()

            comments_in_batch = stats["comments"] - batch_comment_start
            if comments_in_batch >= batch_size:
                rest_result = batch_rest(comments_in_batch, driver)
                if stop_event.is_set():
                    return
                batch_comment_start = stats["comments"]
                batch_size = random.randint(20, 50)
                glog(f"  다음 배치 목표: 댓글 {batch_size}개", "dim")
                if rest_result == 'session_dead':
                    glog("  🔄 세션 재시작 — 검색 목록 처음부터 재개", "warn")
                    # shared_driver가 갱신됐을 수 있으므로 지역 driver도 동기화
                    if shared_driver and shared_driver is not driver:
                        driver = shared_driver
                    restart = True
                    break
        if restart:
            # 검색 목록 처음부터 다시 수집해서 재시작 (driver 재동기화)
            if shared_driver and shared_driver is not driver:
                driver = shared_driver
            glog(f"━━  검색어 '{keyword}' 재시작  ━━", "header")
            blog_list = collect_blog_ids_from_search(driver, keyword, order_type, visit_count)
            glog(f"  총 {len(blog_list)}개 방문 예정 (재시작)", "info")
            visited = set()
            for i, (blog_id, title_hint) in enumerate(blog_list):
                if blog_id in visited: continue
                visited.add(blog_id)
                gcurrent(f"{blog_id}")
                glog(f"  [{i+1}/{len(blog_list)}] {blog_id}  ({title_hint[:20]})", "bold")
                if stop_event.is_set():
                    glog("⛔ 강제 중지됨", "error")
                    return
                result = visit_blog_and_interact(driver, blog_id)
                if result == 'comment_limit':
                    glog("⚠️ 댓글 한도 초과! 중단합니다.", "error")
                    return
                delay = random.uniform(3.0, 8.0)
                time.sleep(delay)
                wait_if_paused()
                comments_in_batch = stats["comments"] - batch_comment_start
                if comments_in_batch >= batch_size:
                    batch_rest(comments_in_batch, driver)
                    if stop_event.is_set():
                        return
                    batch_comment_start = stats["comments"]
                    batch_size = random.randint(20, 50)

    glog(f"━━  검색 모드 완료  🩷{stats['hearts']}  💬{stats['comments']}  ━━", "header")


# =============================================
# 이웃 모드 메인
# =============================================
def run_neighbor_mode(driver, start, end, feed_url):
    global _my_blog_id
    # 본인 블로그 ID 확인 (아직 모르는 경우에만 추출)
    if not _my_blog_id:
        _my_blog_id = _get_my_blog_id(driver)
        if _my_blog_id:
            glog(f"  [본인 블로그 ID] {_my_blog_id} — 본인 게시글 자동 스킵", "info")
        else:
            glog("  [본인 블로그 ID 추출 실패] 본인 게시글 스킵 비활성", "warn")

    batch_size = random.randint(20, 50)
    batch_comment_start = stats["comments"]  # 댓글 기준 배치

    while True:   # session_dead 시 처음부터 재시작
        visited = set()
        session_restart = False

        # ── 피드 로드 후 댓글 버튼 목록 수집 ──
        wait_if_paused()
        driver.get(feed_url)
        rnd(2.5, 5.0)

        # 피드 로드 시 "7일동안 안보기" 팝업 처리 (div[2], div[6] 두 종류)
        for _popup_xp in [
            "/html/body/div[2]/div[1]/div[2]/div[2]/div[2]/div[2]/div/div/div[2]/div/div[2]/div/div[2]/label",
            "/html/body/div[6]/div[1]/div[2]/div[2]/div[2]/div[1]/div/div/div[6]/div/div[2]/div/div[2]/label",
        ]:
            try:
                _p = driver.find_element(By.XPATH, _popup_xp)
                driver.execute_script("arguments[0].click();", _p)
                rnd(0.3, 0.7)
                glog("  피드 팝업 '7일동안 안보기' 클릭", "dim")
            except Exception:
                pass

        # 스크롤로 end번째 게시글까지 로드
        for _ in range(max(0, end - 3)):
            driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.PAGE_DOWN)
            rnd(0.2, 0.6)

        # 댓글 버튼 전체 수집 (줌/레이아웃 무관하게 패턴으로 찾기)
        all_btns = driver.find_elements(By.XPATH,
            '//ul//div/div/div/div[3]/div[2]/button')
        if not all_btns:
            all_btns = driver.find_elements(By.XPATH,
                '//div[3]/div[2]/button[.//i]')

        glog(f"  댓글 버튼 {len(all_btns)}개 발견", "dim")

        # start~end 범위 슬라이싱 (1-based → 0-based)
        target_btns = all_btns[start-1:end]

        for btn_no, btn in enumerate(target_btns, start=start):
            wait_if_paused()
            glog(f"━━  {btn_no}번 게시글  ━━", "header")
            gcurrent(f"게시글 #{btn_no}")

            driver.get(feed_url)
            rnd(2.0, 4.0)

            # 피드 재로드 시 "7일동안 안보기" 팝업 처리 (div[2], div[6] 두 종류)
            for _popup_xp in [
                "/html/body/div[2]/div[1]/div[2]/div[2]/div[2]/div[2]/div/div/div[2]/div/div[2]/div/div[2]/label",
                "/html/body/div[6]/div[1]/div[2]/div[2]/div[2]/div[1]/div/div/div[6]/div/div[2]/div/div[2]/label",
            ]:
                try:
                    _p = driver.find_element(By.XPATH, _popup_xp)
                    driver.execute_script("arguments[0].click();", _p)
                    rnd(0.3, 0.7)
                    glog("  피드 팝업 '7일동안 안보기' 클릭", "dim")
                except Exception:
                    pass

            try:
                # 필요한 버튼이 보일 때까지만 스크롤 (최대 10회)
                needed_idx = btn_no - start  # 0-based index needed
                fresh_btns = []
                for _extra in range(10):
                    fresh_btns = driver.find_elements(By.XPATH,
                        '//ul//div/div/div/div[3]/div[2]/button')
                    if not fresh_btns:
                        fresh_btns = driver.find_elements(By.XPATH,
                            '//div[3]/div[2]/button[.//i]')
                    if len(fresh_btns) > needed_idx:
                        break  # 충분히 수집됨 → 더 스크롤 안 함
                    driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.PAGE_DOWN)
                    rnd(0.3, 0.6)

                glog(f"  버튼 {len(fresh_btns)}개 수집, 필요 인덱스: {needed_idx}", "dim")
                if not fresh_btns or needed_idx >= len(fresh_btns):
                    glog(f"  댓글 버튼 없음 — 건너뜀 (수집:{len(fresh_btns)}, 필요:{needed_idx})", "warn"); continue
                btn_el = fresh_btns[needed_idx]
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn_el)
                rnd(0.4, 0.8)
                try:
                    btn_el.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", btn_el)
                rnd(2.0, 4.5)
                # 댓글창 열리면 '첫 댓글로' 버튼 클릭 (답글 많으면 아래로 내려가 있음)
                try:
                    _top_btn = driver.find_element(By.XPATH,
                        '/html/body/div[1]/div[3]/div[2]/div/div/div/div[2]/div/div/div/div[6]/a')
                    driver.execute_script("arguments[0].click();", _top_btn)
                    rnd(0.5, 1.0)
                except:
                    try:
                        driver.execute_script("window.scrollTo(0, 0);")
                        rnd(0.3, 0.6)
                    except: pass
            except Exception as e:
                glog(f"  댓글창 열기 실패: {e}", "error"); continue

            commenters = get_commenters(driver)
            if not commenters:
                if stop_event.is_set():
                    glog("⛔ 강제 중지됨", "error"); return
                glog("  댓글 작성자 없음 → 피드 재이동 후 재시도", "warn")
                # 피드리스트로 돌아가서 버튼 다시 클릭
                try:
                    driver.get(feed_url)
                    rnd(2.0, 3.5)
                    if stop_event.is_set():
                        glog("⛔ 강제 중지됨", "error"); return
                    # 팝업 처리
                    for _popup_xp in [
                        "/html/body/div[2]/div[1]/div[2]/div[2]/div[2]/div[2]/div/div/div[2]/div/div[2]/div/div[2]/label",
                        "/html/body/div[6]/div[1]/div[2]/div[2]/div[2]/div[1]/div/div/div[6]/div/div[2]/div/div[2]/label",
                    ]:
                        try:
                            _p = driver.find_element(By.XPATH, _popup_xp)
                            driver.execute_script("arguments[0].click();", _p)
                            rnd(0.3, 0.5)
                        except: pass
                    # 버튼 재수집 후 클릭
                    _retry_idx = btn_no - start
                    _retry_btns = []
                    for _ in range(30):
                        if stop_event.is_set():
                            glog("⛔ 강제 중지됨", "error"); return
                        _retry_btns = driver.find_elements(By.XPATH, '//ul//div/div/div/div[3]/div[2]/button')
                        if not _retry_btns:
                            _retry_btns = driver.find_elements(By.XPATH, '//div[3]/div[2]/button[.//i]')
                        if len(_retry_btns) > _retry_idx:
                            break
                        driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.PAGE_DOWN)
                        rnd(0.15, 0.35)
                    if _retry_btns and _retry_idx < len(_retry_btns):
                        _rb = _retry_btns[_retry_idx]
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", _rb)
                        rnd(0.4, 0.8)
                        try: _rb.click()
                        except: driver.execute_script("arguments[0].click();", _rb)
                        rnd(2.0, 4.0)
                        if stop_event.is_set():
                            glog("⛔ 강제 중지됨", "error"); return
                        commenters = get_commenters(driver)
                        if not commenters:
                            glog("  재시도 후도 없음 → 건너뜀", "warn")
                            continue
                    else:
                        glog("  재시도 버튼 못찾음 → 건너뜀", "warn")
                        continue
                except Exception as _re:
                    glog(f"  재시도 실패: {_re} → 건너뜀", "warn")
                    continue

            for i, blog_id in enumerate(commenters):
                if blog_id in visited:
                    glog(f"  [{blog_id}] 이미 방문", "dim"); continue
                visited.add(blog_id)
                gcurrent(blog_id)
                glog(f"  [{i+1}/{len(commenters)}] {blog_id}", "bold")
                if stop_event.is_set():
                    glog("⛔ 강제 중지됨", "error")
                    return
                result = visit_blog_and_interact(driver, blog_id)
                if result == 'comment_limit':
                    glog("⚠️ 댓글 한도 초과! 중단.", "error"); return
                rnd(1.5, 4.0)
                wait_if_paused()

                comments_in_batch = stats["comments"] - batch_comment_start
                if comments_in_batch >= batch_size:
                    rest_result = batch_rest(comments_in_batch, driver)
                    if stop_event.is_set():
                        return
                    batch_comment_start = stats["comments"]
                    batch_size = random.randint(20, 50)
                    glog(f"  다음 배치 목표: 댓글 {batch_size}개", "dim")
                    if rest_result == 'session_dead':
                        glog("  🔄 세션 재시작 — 피드 목록 처음부터 재개", "warn")
                        session_restart = True
                        break

            if session_restart:
                break   # post_idx 루프도 탈출 → while True로 돌아가 처음부터

        if not session_restart:
            # 정상 완료 → visited 리셋 후 피드 처음부터 재시작
            glog(f"━━  {start}~{end}번 게시글 순회 완료 — 피드 처음부터 재시작  ━━", "warn")
            visited = set()
            # while True 상단으로 돌아가 피드 재로드 후 1번부터 재시작
            # (break 하지 않음)

    glog(f"━━  이웃 모드 완료  🩷{stats['hearts']}  💬{stats['comments']}  ━━", "header")


# =============================================
# 워커 스레드
# =============================================
def _run_worker(cfg):
    global gemini_model, shared_driver, _tg_token, _tg_chat, _worker_cfg, _GH_TOKEN

    _session_start()   # 작업 시작 시간 기록
    _worker_cfg = cfg  # 전역 설정 공유 (visit_blog_and_interact에서 참조)

    # 텔레그램 설정 주입
    _tg_token = cfg.get('tg_token', '')
    _tg_chat  = cfg.get('tg_chat', '')

    # GitHub 설정 주입 (ngrok URL 자동 업로드용)
    _GH_TOKEN = cfg.get('gh_token', '')

    # 미발송 보고서 있으면 먼저 발송
    _send_pending_report_if_exists()

    # Gemini 초기화
    gemini_model = genai.Client(api_key=cfg['api_key'])
    glog("Gemini API 연결 완료", "success")

    # ── 드라이버 확보: debuggerAddress 방식 ──
    # 1) 기존 shared_driver 살아있으면 재사용
    # 2) Chrome 디버그 포트(9222)에 attach
    # 3) Chrome이 꺼져 있으면 직접 실행 후 attach
    driver = None

    if shared_driver is not None:
        try:
            shared_driver.execute_script("return 1;")
            driver = shared_driver
            glog("기존 크롬 세션 재사용", "success")
        except Exception:
            glog("기존 세션 만료 — 새로 연결 시도...", "warn")
            shared_driver = None

    if driver is None:
        glog("Chrome 디버그 포트 연결 중...", "info")
        _launch_chrome_if_needed()
        try:
            driver = _connect_chrome()
            shared_driver = driver
            glog("Chrome 연결 성공", "success")
        except Exception as _e:
            glog(f"Chrome 연결 실패: {_e}", "error")
            _tg_send(f"❌ Chrome 연결 실패: {_e}\n봇을 종료합니다.")
            log_queue.put(("done",))
            _allow_sleep()
            return

    # 로그인 상태 확인
    if not _is_logged_in(driver):
        glog("  네이버 로그인이 필요합니다.", "warn")
        _wait_for_login(driver)
        if stop_event.is_set():
            log_queue.put(("done",))
            _allow_sleep()
            return

    end_reason   = "정상 완료"
    error_detail = ""
    _session_retries = 0
    _MAX_SESSION_RETRIES = 3

    while True:
        try:
            if cfg['mode'] == "이웃":
                feed_url = f"https://m.blog.naver.com/FeedList.naver?groupId={cfg['group_id']}"
                run_neighbor_mode(driver, cfg['start'], cfg['end'], feed_url)
            else:
                search_and_interact(driver, cfg['keywords'], cfg['visit_count'], cfg['order_type'])
            if stop_event.is_set():
                end_reason = "사용자 정지"
            break  # 정상 완료

        except Exception as e:
            err_str = str(e)
            _is_chrome_dead = any(k in err_str.lower() for k in [
                "invalid session id", "no such window", "target window already closed",
                "disconnected", "chrome not reachable", "cannot connect to chrome",
                "session deleted", "unable to connect"
            ])

            if _is_chrome_dead and _session_retries < _MAX_SESSION_RETRIES:
                _session_retries += 1
                glog(f"  🔄 크롬 연결 끊김 — 재연결 시도 ({_session_retries}/{_MAX_SESSION_RETRIES})...", "warn")
                _tg_send(f"🔄 크롬 꺼짐 감지 — 재연결 시도 중 ({_session_retries}/{_MAX_SESSION_RETRIES})")
                rnd(3.0, 6.0)

                # debuggerAddress로 재연결 시도
                try:
                    _launch_chrome_if_needed()
                    driver = _connect_chrome()
                    shared_driver = driver
                    glog("  ✅ 크롬 재연결 성공", "success")
                    _tg_send("✅ 크롬 재연결 성공 — 로그인 확인 중...")
                    if not _is_logged_in(driver):
                        _wait_for_login(driver)
                    if stop_event.is_set():
                        break
                    glog("  ✅ 로그인 확인 — 작업 재개", "success")
                    _tg_send("✅ 로그인 확인 — 작업 재개")
                    continue
                except Exception as e2:
                    glog(f"  ❌ 크롬 재연결 실패: {e2}", "error")
                    error_detail = f"크롬 재연결 실패: {e2}"
                    end_reason   = "오류 발생"
            else:
                error_detail = err_str
                end_reason   = "오류 발생"
                glog(f"오류 발생: {e}", "error")

            send_report(end_reason, error_detail)
            auto_pause("오류 발생")
            wait_if_paused()
            break

    # 정상 완료 / 정지 시 발송
    if end_reason != "오류 발생":
        send_report(end_reason, error_detail)

    _allow_sleep()
    rnd(1.5, 3.0)
    log_queue.put(("done",))
    gcurrent("완료")


# =============================================
# 진입점
# =============================================
if __name__ == '__main__':
    _start_web_dashboard()
    import threading as _t2
    app = BlogDashboard()
    _t2.Thread(target=_web_ctrl_watcher, args=(app,), daemon=True).start()
    app.run()
