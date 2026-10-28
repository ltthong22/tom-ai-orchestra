"""Tom AI Orchestra — Web UI responsive (mobile-first, dark mode).

Màn hình: dashboard, giao việc mới, chi tiết task, lịch sử,
token usage, skills, cài đặt.
Chạy: python3 web/app.py  ->  http://localhost:8099
"""

# Version hien thi tren giao dien — TANG MOI LAN SHIP BAN MOI.
# Quy tac 26/10/2026 (anh chot): moi thu tao ra deu phai hien version + ngay
# de phan biet ban cu/moi.
ORCHESTRA_VERSION = "2.9"
ORCHESTRA_VERSION_DATE = "28/10/2026"

# ============ Voice (28/10/2026): noi bang giong noi tieng Viet -> task-bus ============
# Trang /voice (Web Speech API) + endpoint Siri /api/voice-task.
# Quy uoc (xem VOICE.md): from luon la 'orchestra-voice'; source phan biet
# kenh vao ('voice-page' | 'siri'). Trang Voice HIEN version + ngay (quy tac 26/10).
VOICE_VERSION = "1.0"
VOICE_VERSION_DATE = "28/10/2026"

import html
import json
import os
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Load .env (local dev) nếu có — Railway dùng Variables riêng
try:
    _env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(_env_path):
        with open(_env_path, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    _k, _v = _k.strip(), _v.strip().strip('"').strip("'")
                    if _k and _k not in os.environ:
                        os.environ[_k] = _v
except Exception:
    pass
from orchestrator import Orchestrator, SubTask
try:
    from orchestrator.board import build_board_html as _build_board_html
except Exception:
    _build_board_html = None
from auth.auth import verify, make_session, check_session, destroy_session, create_user, get_csrf

try:
    from tokens.tracker import get_tracker
    _tracker = get_tracker()
except Exception:
    _tracker = None

try:
    from skills.loader import load_all_skills, search_skills, get_skill
except Exception:
    def load_all_skills(): return {}
    def search_skills(q, limit=5): return []
    def get_skill(n): return None

orch = Orchestrator()
PORT = int(os.environ.get("PORT") or os.environ.get("WEB_PORT", "8099"))

TASK_BUS_DIR = os.path.expanduser("~/workspace/task-bus")


def _voice_now_iso():
    """Thoi gian hien tai dang ISO +07:00, khop format task-bus."""
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone(timedelta(hours=7))).isoformat(timespec="seconds")


def _voice_timestamp():
    """Timestamp dang YYYYMMDD-HHMMSS cho ten file task-bus."""
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone(timedelta(hours=7))).strftime("%Y%m%d-%H%M%S")


def _voice_dests():
    """Side chat dich co the chon: 'main' + slug tu file task-bus cu (from).

    Chi nhan slug dang [a-z0-9-]; bo ten hien thi co khoang trang/dau [].
    'main' luon dung dau (mac dinh, de main chat tu dieu phoi).
    """
    dests = ["main"]
    seen = set()
    for sub in ("pending", "done", "claimed"):
        d = os.path.join(TASK_BUS_DIR, sub)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    j = json.load(f)
                frm = j.get("from", "")
                if (isinstance(frm, str) and frm and frm != "main"
                        and frm != "orchestra-voice"
                        and all(c.islower() or c.isdigit() or c == "-" for c in frm)
                        and frm not in seen):
                    seen.add(frm)
                    dests.append(frm)
            except Exception:
                continue
    return dests


def _voice_find(voice_id):
    """Tim task voice theo voice_id: done -> claimed -> pending.

    Tra ve (status, data, filename) hoac (None, None, None) neu khong thay.
    """
    for sub in ("done", "claimed", "pending"):
        d = os.path.join(TASK_BUS_DIR, sub)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    j = json.load(f)
            except Exception:
                continue
            if j.get("voice_id") == voice_id:
                status = "done" if sub == "done" else j.get("status", "pending")
                return status, j, fn
    return None, None, None


def _voice_write_task(text, dest, source):
    """Ghi task voice vao task-bus pending. Tra ve (voice_id, filename, dest).

    Ghi kieu atomic (tmp + os.replace) de watcher khong doc file do.
    Dest la thi ve 'main' neu khong nam trong danh sach cho phep.
    """
    if dest not in _voice_dests():
        dest = "main"
    ts = _voice_timestamp()
    short = secrets.token_hex(3)
    voice_id = "voice-" + ts.replace("-", "") + "-" + short
    fn = f"{ts}-voice-{short}.json"
    data = {
        "from": "orchestra-voice",
        "to": dest,
        "task": text,
        "created_at": _voice_now_iso(),
        "status": "pending",
        "needs_approval": False,
        "voice_id": voice_id,
        "source": source,
    }
    pend = os.path.join(TASK_BUS_DIR, "pending")
    os.makedirs(pend, exist_ok=True)
    tmp = os.path.join(pend, f".{fn}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, os.path.join(pend, fn))
    return voice_id, fn, dest

# ============ Bảo mật: throttle login ============
# Sau 5 lần đăng nhập sai trong 15 phút -> khóa IP 15 phút
_LOGIN_ATTEMPTS = {}  # ip -> {"fails": int, "first": ts, "locked_until": ts}
_LOGIN_WINDOW = 900   # 15 phút
_LOGIN_MAX_FAILS = 5


def _login_allowed(ip: str) -> bool:
    now = time.time()
    rec = _LOGIN_ATTEMPTS.get(ip)
    if not rec:
        return True
    if rec.get("locked_until", 0) > now:
        return False
    if now - rec.get("first", now) > _LOGIN_WINDOW:
        _LOGIN_ATTEMPTS.pop(ip, None)  # hết cửa sổ -> reset
    return True


def _login_failed(ip: str):
    now = time.time()
    rec = _LOGIN_ATTEMPTS.get(ip)
    if not rec or now - rec.get("first", now) > _LOGIN_WINDOW:
        rec = {"fails": 0, "first": now, "locked_until": 0}
    rec["fails"] += 1
    if rec["fails"] >= _LOGIN_MAX_FAILS:
        rec["locked_until"] = now + _LOGIN_WINDOW
    _LOGIN_ATTEMPTS[ip] = rec


def _login_ok(ip: str):
    _LOGIN_ATTEMPTS.pop(ip, None)

# ================= CSS: mobile-first, dark mode =================
CSS = """
:root{
  --bg:#0d1117; --bg2:#161b22; --bg3:#1c2330; --border:#2d333b;
  --text:#e6edf3; --muted:#8b949e; --accent:#58a6ff; --accent2:#3fb950;
  --warn:#d29922; --danger:#f85149; --radius:12px;
}
*{box-sizing:border-box; -webkit-tap-highlight-color:transparent;}
html{font-size:16px;}
body{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
  background:var(--bg); color:var(--text); margin:0; padding:0;
  line-height:1.55; min-height:100vh;
}
/* ---- header ---- */
.topbar{
  position:sticky; top:0; z-index:50;
  background:rgba(13,17,23,.96); backdrop-filter:blur(8px);
  border-bottom:1px solid var(--border);
  padding:12px 16px; display:flex; align-items:center; gap:12px;
}
.topbar .logo{font-size:1.25rem; font-weight:700;}
.topbar .spacer{flex:1;}
.topbar a{color:var(--muted); text-decoration:none; font-size:.9rem;}
.topbar a.active{color:var(--accent); font-weight:600;}
/* ---- nav pills (mobile scroll) ---- */
.nav{
  display:flex; gap:8px; overflow-x:auto; padding:12px 16px;
  border-bottom:1px solid var(--border); background:var(--bg2);
  scrollbar-width:none;
}
.nav::-webkit-scrollbar{display:none;}
.nav a{
  flex:0 0 auto; padding:10px 18px; border-radius:999px;
  background:var(--bg3); color:var(--text); text-decoration:none;
  font-size:.95rem; border:1px solid var(--border); min-height:44px;
  display:flex; align-items:center;
}
.nav a.active{background:var(--accent); border-color:var(--accent); color:#fff; font-weight:600;}
/* ---- layout ---- */
.wrap{max-width:1100px; margin:0 auto; padding:16px;}
h1{font-size:1.4rem; margin:8px 0 16px;}
h2{font-size:1.15rem; margin:20px 0 12px;}
h3{font-size:1rem; margin:16px 0 8px;}
p.muted, .muted{color:var(--muted);}
/* ---- cards ---- */
.grid{display:grid; gap:12px; grid-template-columns:1fr;}
.card{
  background:var(--bg2); border:1px solid var(--border);
  border-radius:var(--radius); padding:16px;
}
.card h3{margin:0 0 8px; font-size:1rem;}
.stat{font-size:1.8rem; font-weight:700; margin:4px 0;}
.stat small{font-size:.8rem; color:var(--muted); font-weight:400;}
.badge{
  display:inline-block; padding:4px 12px; border-radius:999px;
  font-size:.8rem; font-weight:600;
}
.badge.done{background:rgba(63,185,80,.15); color:var(--accent2);}
.badge.running{background:rgba(88,166,255,.15); color:var(--accent);}
.badge.failed{background:rgba(248,81,73,.15); color:var(--danger);}
.badge.pending{background:rgba(210,153,34,.15); color:var(--warn);}
/* ---- forms ---- */
label{display:block; margin:12px 0 6px; font-weight:600; font-size:.95rem;}
input[type=text],input[type=password],textarea,select{
  width:100%; padding:12px 14px; font-size:1rem; min-height:48px;
  background:var(--bg3); border:1px solid var(--border); color:var(--text);
  border-radius:10px;
}
textarea{min-height:120px; resize:vertical;}
input:focus,textarea:focus,select:focus{outline:2px solid var(--accent); border-color:var(--accent);}
button,.btn{
  display:inline-flex; align-items:center; justify-content:center;
  min-height:48px; padding:12px 24px; font-size:1rem; font-weight:600;
  background:var(--accent); color:#fff; border:none; border-radius:10px;
  cursor:pointer; text-decoration:none; width:100%; margin-top:12px;
}
button:active,.btn:active{transform:scale(.98);}
.btn-ghost{background:var(--bg3); color:var(--text); border:1px solid var(--border);}
.btn-danger{background:var(--danger);}
pre{
  white-space:pre-wrap; word-break:break-word;
  background:var(--bg); border:1px solid var(--border);
  padding:14px; border-radius:10px; font-size:.85rem;
  max-height:400px; overflow:auto;
}
/* ---- table -> cards on mobile ---- */
.tbl{width:100%; border-collapse:collapse; font-size:.9rem;}
.tbl th,.tbl td{padding:10px 8px; border-bottom:1px solid var(--border); text-align:left;}
.tbl th{color:var(--muted); font-weight:600; font-size:.8rem; text-transform:uppercase;}
/* ---- progress ---- */
.progress{height:8px; background:var(--bg3); border-radius:99px; overflow:hidden; margin:8px 0;}
.progress i{display:block; height:100%; background:var(--accent2); border-radius:99px;}
.subtask{border-left:3px solid var(--border); padding:8px 0 8px 14px; margin:12px 0;}
.subtask.ok{border-color:var(--accent2);}
.subtask.fail{border-color:var(--danger);}
.agent-tag{
  display:inline-block; font-size:.75rem; padding:2px 10px; border-radius:99px;
  background:var(--bg3); border:1px solid var(--border); margin-right:6px;
}
.skill-tag{
  display:inline-block; font-size:.75rem; padding:4px 12px; border-radius:99px;
  background:rgba(88,166,255,.12); color:var(--accent); margin:2px 4px 2px 0;
  border:1px solid rgba(88,166,255,.3);
}
/* ---- token bars ---- */
.tbar{display:flex; align-items:center; gap:10px; margin:8px 0; font-size:.9rem;}
.tbar .lbl{flex:0 0 110px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
.tbar .track{flex:1; height:10px; background:var(--bg3); border-radius:99px; overflow:hidden;}
.tbar .fill{height:100%; background:var(--accent); border-radius:99px;}
.tbar .val{flex:0 0 90px; text-align:right; color:var(--muted); font-size:.8rem;}
.warn-box{
  background:rgba(210,153,34,.1); border:1px solid var(--warn);
  border-radius:var(--radius); padding:14px; margin:12px 0;
}
.ok-box{
  background:rgba(63,185,80,.08); border:1px solid rgba(63,185,80,.4);
  border-radius:var(--radius); padding:14px; margin:12px 0;
}
/* ---- desktop: multi-column ---- */
@media(min-width:768px){
  .grid.cols2{grid-template-columns:1fr 1fr;}
  .grid.cols3{grid-template-columns:1fr 1fr 1fr;}
  button,.btn{width:auto;}
  .wrap{padding:24px;}
  h1{font-size:1.7rem;}
  .topbar{padding:14px 24px;}
}
@media(min-width:1024px){
  .grid.cols3{grid-template-columns:1fr 1fr 1fr;}
}
.login-wrap{max-width:400px; margin:60px auto; padding:0 16px;}
.login-wrap .card{padding:28px;}
.tabbar{padding-bottom:80px;}
/* ---- left icon bar ---- */
.iconbar{
  position:fixed; left:0; top:0; bottom:0; width:56px; z-index:100;
  background:rgba(13,17,23,.97); border-right:1px solid var(--border);
  display:flex; flex-direction:column; align-items:center;
  padding:12px 0; gap:8px;
}
.iconbar .ib-logo{font-size:1.3rem; margin-bottom:8px;}
.iconbar button{
  width:40px; height:40px; border-radius:12px; border:1px solid transparent;
  background:transparent; font-size:1.25rem; cursor:pointer;
  display:flex; align-items:center; justify-content:center;
  transition:background .15s, border-color .15s;
}
.iconbar button:hover{background:var(--bg3); border-color:var(--border);}
.iconbar button.active{background:var(--accent); border-color:var(--accent);}
.iconbar .ib-sep{width:24px; border-top:1px solid var(--border); margin:4px 0;}
.iconbar .ib-spacer{flex:1;}
body.has-iconbar{padding-left:56px;}
body.has-iconbar .topbar{padding-left:16px;}
/* ---- modal popup ---- */
.modal-overlay{
  position:fixed; inset:0; z-index:200; background:rgba(0,0,0,.6);
  display:none; align-items:center; justify-content:center; padding:16px;
}
.modal-overlay.open{display:flex;}
.modal{
  background:var(--bg2); border:1px solid var(--border);
  border-radius:16px; max-width:640px; width:100%;
  max-height:85vh; overflow-y:auto; padding:20px;
  position:relative; box-shadow:0 20px 60px rgba(0,0,0,.5);
}
.modal-close{
  position:absolute; top:12px; right:12px; width:32px; height:32px;
  border-radius:99px; border:1px solid var(--border); background:var(--bg3);
  color:var(--text); font-size:1rem; cursor:pointer;
  display:flex; align-items:center; justify-content:center;
}
.modal-close:hover{background:var(--bg); }
.modal h2{margin-top:0;}
@media(max-width:640px){
  .iconbar{width:48px;}
  body.has-iconbar{padding-left:48px;}
  .iconbar button{width:36px; height:36px; font-size:1.1rem;}
}
"""

NAV_ITEMS = [
    ("/", "📊", "Tổng quan"),
    ("/chat", "💬", "Chat AI"),
    ("/new", "➕", "Giao việc"),
    ("/history", "📜", "Lịch sử"),
    ("/tokens", "🎟️", "Token Usage"),
    ("/ai-office", "🏢", "Văn Phòng AI"),
    ("/ai-office-3d", "🌐", "VP 3D"),
    ("/skills", "🧠", "Skills"),
    ("/settings", "⚙️", "Cài đặt"),
]

# ---------- KHO KIẾN THỨC CHUNG: mọi agent đều học ----------
# Quy tắc nghiệp vụ studio (anh Thống chốt) — inject vào mọi prompt agent.
STUDIO_RULES = {
    "hop_dong": "HĐ video mã A1xxx, HĐ photo mã B1xxx.",
    "coc": "Cọc 50%, thanh toán 3 đợt.",
    "nghiem_thu": "Nghiệm thu tự động sau 3 ngày, 2 lần sửa free, lưu file trên Drive 1 năm.",
    "rule_30_30": "Đến sớm 30 phút trước giờ job; job xa >50km thì nghỉ thêm 30 phút.",
    "gio_job": "Job sáng/full-day bắt đầu 7:00–7:30.",
    "cuoi": "Cưới: PS Cưới nhà gái ~7:00 trước nhà trai (~8:30); TT Cưới nhà trai trước.",
    "lich": "Tiêu đề lịch KHÔNG dùng từ 'chụp'; show chưa cọc thêm tiền tố '(Chưa cọc)'.",
    "tien_show": "Tiền show/job về thẳng VCB.",
    "tach_bach": "Thu show và mua gear TÁCH BẠCH TUYỆT ĐỐI — không bù trừ công nợ với mua sắm.",
    "cuoi_tuan": "Thứ 7 + Chủ nhật ưu tiên job CÓ TIỀN, không nhận việc không lương.",
    "xung_ho": "Xưng 'em', gọi user là 'anh'.",
    "phieu": "Phiếu xác nhận: không ghi tên crew, không lộ lương/đơn giá nhân sự.",
}


def studio_rules_text():
    """Render quy tắc studio thành text để inject vào prompt agent."""
    lines = ["[QUY TẮC STUDIO — tuân thủ khi trả lời/làm việc]"]
    for k, v in STUDIO_RULES.items():
        lines.append(f"- {v}")
    return "\n".join(lines)


def page(title, body, active="/", user=""):
    return f"""<!DOCTYPE html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0d1117">
<title>{html.escape(title)} — Tom AI Orchestra</title>
<style>{CSS}</style></head>
<body class="has-iconbar">
<div class="iconbar">
  <div class="ib-logo" title="Tom AI Orchestra">🎼</div>
  <button id="ib-vp" title="Chuyển VP 2D / 3D">🏢</button>
  <button id="ib-voice" title="Voice — nói chuyện bằng giọng nói">🎤</button>
  <div class="ib-sep"></div>
  <button id="ib-sync" title="Đồng bộ skills + quy tắc">🔄</button>
  <div class="ib-spacer"></div>
  <button id="ib-settings" title="Cài đặt">⚙️</button>
</div>
<div class="modal-overlay" id="ib-modal-ov">
  <div class="modal">
    <button class="modal-close" id="ib-modal-x" title="Đóng">✕</button>
    <div id="ib-modal-body"><p class="muted">⏳ Đang tải...</p></div>
  </div>
</div>
<div class="topbar"><span class="logo">🎼 Orchestra</span><span class="spacer"></span>
{f"<span class='muted'>👤 {html.escape(user)}</span>" if user else ""}</div>
<div class="wrap tabbar">{body}</div>
<script>
(function(){{
  var ov = document.getElementById('ib-modal-ov');
  var body = document.getElementById('ib-modal-body');
  function openModal(html){{ body.innerHTML = html; ov.classList.add('open'); }}
  function closeModal(){{ ov.classList.remove('open'); }}
  document.getElementById('ib-modal-x').onclick = closeModal;
  ov.onclick = function(e){{ if(e.target === ov) closeModal(); }};
  document.addEventListener('keydown', function(e){{ if(e.key === 'Escape') closeModal(); }});
  // VP 2D/3D toggle
  document.getElementById('ib-vp').onclick = function(){{
    var p = location.pathname;
    location.href = (p === '/ai-office-3d') ? '/ai-office' : '/ai-office-3d';
  }};
  // highlight VP button khi đang ở trang VP
  if(location.pathname === '/ai-office' || location.pathname === '/ai-office-3d'){{
    document.getElementById('ib-vp').classList.add('active');
  }}
  // Voice
  document.getElementById('ib-voice').onclick = function(){{ location.href = '/voice'; }};
  if(location.pathname === '/voice'){{
    document.getElementById('ib-voice').classList.add('active');
  }}
  // Sync popup
  document.getElementById('ib-sync').onclick = async function(){{
    openModal('<h2>🔄 Đồng bộ</h2><p class="muted">⏳ Đang tải...</p>');
    try{{
      var r = await fetch('/api/popup/sync'); var d = await r.json();
      openModal('<h2>🔄 Đồng bộ</h2>' + d.html);
      wireSyncButtons();
    }}catch(e){{ openModal('<h2>🔄 Đồng bộ</h2><p>❌ Lỗi tải: '+e+'</p>'); }}
  }};
  function wireSyncButtons(){{
    var b1 = document.getElementById('ib-sync-skills');
    if(b1) b1.onclick = async function(){{
      b1.textContent = '⏳ Đang đồng bộ skills...'; b1.disabled = true;
      try{{
        var r = await fetch('/api/kb/reload'); var d = await r.json();
        b1.textContent = d.ok ? ('✅ Đã đồng bộ '+d.skills+' skills') : '❌ Lỗi';
      }}catch(e){{ b1.textContent = '❌ Lỗi'; }}
      b1.disabled = false;
    }};
    var b2 = document.getElementById('ib-sync-rules');
    if(b2) b2.onclick = async function(){{
      b2.textContent = '⏳ Đang đồng bộ quy tắc...'; b2.disabled = true;
      try{{
        var r = await fetch('/api/kb/reload'); var d = await r.json();
        b2.textContent = d.ok ? '✅ Quy tắc đã đồng bộ' : '❌ Lỗi';
      }}catch(e){{ b2.textContent = '❌ Lỗi'; }}
      b2.disabled = false;
    }};
    var b3 = document.getElementById('ib-sync-finance');
    if(b3) b3.onclick = async function(){{
      b3.textContent = '⏳ Đang đồng bộ tài chính...'; b3.disabled = true;
      try{{
        var r = await fetch('/api/sync/finance'); var d = await r.json();
        b3.textContent = d.ok ? '✅ Tài chính đã đồng bộ' : '❌ Lỗi';
        document.getElementById('ib-finance-result').textContent = d.summary || '';
      }}catch(e){{ b3.textContent = '❌ Lỗi'; }}
      b3.disabled = false;
    }};
    var b4 = document.getElementById('ib-sync-library');
    if(b4) b4.onclick = async function(){{
      b4.textContent = '⏳ Đang đồng bộ thư viện...'; b4.disabled = true;
      try{{
        var r = await fetch('/api/sync/library'); var d = await r.json();
        b4.textContent = d.ok ? '✅ Thư viện đã đồng bộ' : '❌ Lỗi';
        document.getElementById('ib-library-result').textContent = d.summary || '';
      }}catch(e){{ b4.textContent = '❌ Lỗi'; }}
      b4.disabled = false;
    }};
  }}
  // Settings popup
  document.getElementById('ib-settings').onclick = async function(){{
    openModal('<h2>⚙️ Cài đặt</h2><p class="muted">⏳ Đang tải...</p>');
    try{{
      var r = await fetch('/api/popup/settings'); var d = await r.json();
      openModal('<h2>⚙️ Cài đặt</h2>' + d.html);
    }}catch(e){{ openModal('<h2>⚙️ Cài đặt</h2><p>❌ Lỗi tải: '+e+'</p>'); }}
  }};
}})();
</script>
</body></html>"""


def fmt_num(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n or 0)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _cookie_token(self):
        for part in self.headers.get("Cookie", "").split(";"):
            if part.strip().startswith("tok="):
                return part.strip()[4:]
        return ""

    def _send(self, body, code=200, ctype="text/html; charset=utf-8", headers=None):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _user(self):
        tok = self._cookie_token()
        return check_session(tok) if tok else None

    def _csrf_token(self):
        """CSRF token của session hiện tại (None nếu chưa đăng nhập)."""
        tok = self._cookie_token()
        return get_csrf(tok) if tok else None

    def _csrf_ok(self, q):
        """Kiểm tra CSRF token gửi kèm POST có khớp session không."""
        want = self._csrf_token()
        got = q.get("csrf", [""])[0]
        return bool(want) and bool(got) and secrets.compare_digest(got, want)

    # ============ GET ============
    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        q = parse_qs(u.query)

        if p == "/login":
            self._send(page("Đăng nhập",
                """<div class="login-wrap"><div class="card">
                <h1>🎼 Tom AI Orchestra</h1>
                <form method="post" action="/login">
                <label>Tên đăng nhập</label><input type="text" name="u" autocomplete="username">
                <label>Mật khẩu</label><input type="password" name="p" autocomplete="current-password">
                <button type="submit">Đăng nhập</button></form></div></div>"""))
            return

        user = self._user()
        if not user:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
            return

        if p == "/":
            self.send_response(302)
            self.send_header("Location", "/ai-office-3d")
            self.end_headers(); return
        if p == "/new":
            self._new_form(user); return
        if p == "/task":
            self._task_detail(user, q.get("id", [""])[0]); return
        if p == "/history":
            self._history(user); return
        if p == "/tokens":
            self._tokens_page(user, q); return
        if p == "/ai-office":
            self._ai_office(user); return
        if p == "/ai-office-3d":
            self._ai_office_3d(user); return
        if p == "/van-phong-ai-song":
            # Đã gộp vào /ai-office-3d — link cũ tự chuyển về trang chính.
            self.send_response(302)
            self.send_header("Location", "/ai-office-3d")
            self.end_headers()
            return
        if p == "/api/office-chat":
            self._api_office_chat(q); return
        if p == "/accounting-room":
            self.send_response(302)
            self.send_header("Location", "/ai-office")
            self.end_headers()
            return
        if p == "/skills":
            self._skills_page(user, q.get("q", [""])[0], q.get("name", [""])[0]); return
        if p == "/settings":
            self._settings(user); return
        if p == "/api/router":
            self._api_router(); return
        if p == "/api/office3d/state":
            self._api_office3d_state(); return
        if p == "/chat":
            self._chat_page(user); return
        if p == "/voice":
            self._voice_page(user); return
        if p == "/api/voice/poll":
            self._voice_poll(q); return
        if p == "/api/chat/history":
            self._api_chat_history(); return
        if p == "/api/kb/reload":
            import json as _json
            try:
                from data_providers.knowledge import force_reload
                kb = force_reload()
                body = _json.dumps({"ok": True,
                                    "skills": kb["counts"].get("skills", 0),
                                    "loaded_at": kb.get("loaded_at", "")}).encode()
            except Exception as e:
                body = _json.dumps({"ok": False, "error": str(e)}).encode()
            self._send(body, 200, "application/json; charset=utf-8"); return
        if p == "/api/kb/approve":
            import json as _json, os as _os, shutil as _sh
            fn = q.get("f", [""])[0].replace("/", "").replace("\\", "")
            try:
                src = _os.path.join(_os.path.expanduser("~/workspace/task-bus/pending"), fn)
                dst = _os.path.join(_os.path.expanduser("~/workspace/task-bus/done"), fn)
                _os.makedirs(_os.path.dirname(dst), exist_ok=True)
                _sh.move(src, dst)
                body = _json.dumps({"ok": True}).encode()
            except Exception as e:
                body = _json.dumps({"ok": False, "error": str(e)}).encode()
            self._send(body, 200, "application/json; charset=utf-8"); return
        if p == "/api/popup/sync":
            import json as _json
            try:
                from data_providers.knowledge import status as _kb_status
                kb = _kb_status()
                n_skills = kb.get("counts", {}).get("skills", 0)
                loaded = kb.get("loaded_at", "")
            except Exception:
                n_skills, loaded = 0, ""
            h = (f"<div class='card'><h3>🧠 Skills</h3>"
                 f"<p class='muted'>{n_skills} skills đã index"
                 + (f" · cập nhật {html.escape(str(loaded))}" if loaded else "") + "</p>"
                 f"<button id='ib-sync-skills' class='btn'>🔄 Đồng bộ skills</button></div>"
                 f"<div class='card' style='margin-top:12px'><h3>📏 Quy tắc studio</h3>"
                 f"<p class='muted'>Nạp lại quy tắc + kiến thức chung cho mọi AI.</p>"
                 f"<button id='ib-sync-rules' class='btn'>🔄 Đồng bộ quy tắc</button></div>"
                 f"<div class='card' style='margin-top:12px'><h3>💰 Media Finance</h3>"
                 f"<p class='muted'>Làm mới số liệu tài chính từ Finance Manager.</p>"
                 f"<button id='ib-sync-finance' class='btn'>🔄 Đồng bộ tài chính</button>"
                 f"<div id='ib-finance-result' class='muted' style='margin-top:8px'></div></div>"
                 f"<div class='card' style='margin-top:12px'><h3>📚 Thư viện Muse</h3>"
                 f"<p class='muted'>Đồng bộ toàn bộ thư viện Muse trên Drive.</p>"
                 f"<button id='ib-sync-library' class='btn'>🔄 Đồng bộ thư viện</button>"
                 f"<div id='ib-library-result' class='muted' style='margin-top:8px'></div></div>")
            body = _json.dumps({"ok": True, "html": h}).encode()
            self._send(body, 200, "application/json; charset=utf-8"); return
        if p == "/api/sync/finance":
            import json as _json
            try:
                from data_providers.finance_data import fetch_dashboard, format_summary
                d = fetch_dashboard()
                ok = d is not None
                summary = format_summary() if ok else "Không kết nối được Finance Manager"
            except Exception as e:
                ok, summary = False, f"Lỗi: {e}"
            body = _json.dumps({"ok": ok, "summary": summary}).encode()
            self._send(body, 200, "application/json; charset=utf-8"); return
        if p == "/api/sync/library":
            import json as _json
            try:
                from data_providers.drive_index import index_status
                st = index_status()
                ok = True
                summary = f"Thư viện Drive: {st.get('count', 0)} files đã index"
            except Exception as e:
                ok, summary = False, f"Lỗi: {e}"
            body = _json.dumps({"ok": ok, "summary": summary}).encode()
            self._send(body, 200, "application/json; charset=utf-8"); return
        if p == "/api/popup/settings":
            import json as _json
            h = self._settings_html(user)
            body = _json.dumps({"ok": True, "html": h}).encode()
            self._send(body, 200, "application/json; charset=utf-8"); return
        if p == "/logout":
            destroy_session(self._cookie_token())
            self.send_response(302)
            self.send_header("Set-Cookie", "tok=; Path=/; Max-Age=0")
            self.send_header("Location", "/login")
            self.end_headers()
            return
        self._send(page("404", "<h1>Không tìm thấy</h1>", user=user), 404)

    def _api_router(self):
        """JSON live status: slots + latency từng agent, task queue, kinds."""
        import json as _json
        try:
            from orchestrator.router import get_router
            agents = get_router().all_stats()
        except Exception as e:
            agents = []
        # task stats: đếm theo status + kind đang chạy
        try:
            tasks = orch.list_tasks(50)
            by_status = {}
            for t in tasks:
                by_status[t["status"]] = by_status.get(t["status"], 0) + 1
        except Exception:
            by_status = {}
        # in-flight per agent từ router
        in_flight_total = sum(a.get("in_flight", 0) for a in agents)
        calls_per_min = sum(a.get("calls", 0) for a in agents)
        data = {
            "agents": agents,
            "tasks_by_status": by_status,
            "in_flight": in_flight_total,
            "total_calls": calls_per_min,
        }
        body = _json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------- Chat AI (Messenger-style) ----------
    def _api_chat_history(self):
        import json as _json
        try:
            from orchestrator.chat import get_history
            data = {"messages": get_history(50)}
        except Exception as e:
            import traceback
            traceback.print_exc()  # log chi tiết ở server
            data = {"error": "Lỗi máy chủ, thử lại sau", "messages": []}
        body = _json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _data_context(self, msg: str) -> str:
        """Phát hiện intent về dữ liệu thật, trả về context để inject vào prompt."""
        low = msg.lower()
        parts = []

        def has(*words):
            return any(w in low for w in words)

        # 1. Token usage
        if has("token", "xài bao nhiêu", "tốn bao nhiêu", "usage"):
            try:
                from data_providers import token_stats
                if has("hôm nay", "today"):
                    parts.append(token_stats.get_today())
                elif has("7 ngày", "tuần"):
                    parts.append(token_stats.format_stats(7))
                else:
                    parts.append(token_stats.format_stats(30))
            except Exception:
                pass

        # 2. Tài chính
        if has("tài chính", "tài sản", "công nợ", "thu", " chi ", "số dư",
               "doanh thu", "nợ", "tiền", "finance"):
            try:
                from data_providers import finance_data
                parts.append(finance_data.format_summary())
            except Exception:
                pass

        # 3. Drive — tìm file/folder
        if has("tìm", "drive", "folder", "file", "ảnh", "ở đâu", "hình"):
            try:
                from data_providers import drive_index
                # trích từ khóa: bỏ stopword đơn giản
                q = msg
                for sw in ["tìm", "giúp", "cho", "anh", "em", "cái", "trong",
                           "drive", "ở đâu", "?", "ạ", "nhé", "đi"]:
                    q = q.replace(sw, " ")
                q = " ".join(q.split())
                if len(q) >= 2:
                    parts.append(drive_index.format_search(q))
            except Exception:
                pass

        # 4. Lịch
        if has("lịch", "sự kiện", "event", "hôm nay có gì", "mai có gì",
               "tuần này", "calendar"):
            try:
                from data_providers import calendar_data
                parts.append(calendar_data.format_upcoming(7))
            except Exception:
                pass

        return "\n\n".join(p for p in parts if p)

    def _api_chat_send(self, q):
        """Nhận tin nhắn user -> orchestrator chọn AI -> trả lời."""
        import json as _json
        msg = q.get("msg", [""])[0].strip()
        agent_want = q.get("agent", ["auto"])[0].strip() or "auto"
        if not msg:
            body = _json.dumps({"error": "Tin nhắn trống"}).encode()
        else:
            try:
                from orchestrator.chat import save_message
                from orchestrator.core import SubTask
                # lưu tin user
                save_message("user", msg)
                # inject dữ liệu thật khi câu hỏi liên quan (data_providers)
                # + KHO KIẾN THỨC CHUNG (Drive) — mọi agent đều học
                prompt = msg
                try:
                    try:
                        from data_providers.knowledge import agent_context
                        kb_ctx = agent_context()
                    except Exception:
                        kb_ctx = studio_rules_text()
                    ctx = self._data_context(msg)
                    parts = [kb_ctx]
                    if ctx:
                        parts.append(f"[DỮ LIỆU THẬT — dùng để trả lời, không bịa số]\n{ctx}")
                    parts.append(f"[CÂU HỎI]\n{msg}")
                    prompt = "\n\n".join(parts)
                except Exception:
                    pass
                # chọn agent: auto -> dùng pick_agent, hoặc ép agent cụ thể
                if agent_want != "auto":
                    agent_name = agent_want
                else:
                    agent_name = orch.pick_agent("quick_qa")
                st = SubTask(id="st-chat", kind="quick_qa", prompt=prompt,
                             agent_name=agent_name if agent_want != "auto" else None)
                r = orch._run_subtask(st)
                agent_used = r.get("agent", agent_name)
                reply = r["output"] if r["success"] and r["output"] else (
                    f"⚠️ {agent_used} lỗi: {r.get('error', 'không rõ')}")
                saved = save_message("ai", reply, agent=agent_used)
                body = _json.dumps({"ok": True, "message": saved},
                                   ensure_ascii=False).encode("utf-8")
            except Exception as e:
                import traceback
                traceback.print_exc()  # log chi tiết ở server
                body = _json.dumps({"error": "Lỗi máy chủ, thử lại sau"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _chat_page(self, user):
        csrf = self._csrf_token() or ""
        _body = """
<h1>💬 Chat AI <a href="/ai-office" style="font-size:14px;font-weight:600;color:#4aa8ff;text-decoration:none;border:1px solid #4aa8ff;border-radius:20px;padding:4px 14px;">🏢 Văn Phòng</a></h1>
<p class="muted">Nhắn như Messenger — mỗi AI có màu + avatar riêng, thấy rõ ai đang trả lời.</p>

<div class="chat-wrap">
  <div id="chat-msgs" class="chat-msgs"></div>
  <div id="typing" class="typing" style="display:none">
    <span class="dot-ava" id="typing-ava">🤖</span>
    <span class="tdots"><i></i><i></i><i></i></span>
    <span id="typing-name" class="muted" style="font-size:.8rem"></span>
  </div>
  <div class="chat-input">
    <select id="agent-sel" class="agent-sel">
      <option value="auto">🤖 Tự động</option>
      <option value="gemini">🔵 Gemini</option>
      <option value="muse">🟣 Muse</option>
      <option value="lmstudio">🟢 LM Studio</option>
      <option value="deepseek">🔷 DeepSeek</option>
    </select>
    <input id="chat-text" type="text" placeholder="Nhắn tin..." autocomplete="off">
    <button id="chat-send" class="send-btn">➤</button>
  </div>
</div>

<style>
.chat-wrap{max-width:640px;margin:0 auto;display:flex;flex-direction:column;
  height:calc(100vh - 260px);min-height:380px;background:#0d1117;
  border:1px solid var(--border);border-radius:16px;overflow:hidden}
.chat-msgs{flex:1;overflow-y:auto;padding:1rem;display:flex;flex-direction:column;gap:.6rem}
.msg{max-width:82%;padding:.6rem .9rem;border-radius:16px;font-size:.92rem;
  line-height:1.45;word-break:break-word;white-space:pre-wrap}
.msg.user{align-self:flex-end;background:var(--accent);color:#fff;
  border-bottom-right-radius:4px}
.msg.ai{align-self:flex-start;background:#161b22;border:1px solid var(--border);
  border-bottom-left-radius:4px;color:var(--text)}
.msg .who{display:flex;align-items:center;gap:.4rem;font-size:.75rem;
  font-weight:700;margin-bottom:.3rem}
.ava{width:22px;height:22px;border-radius:99px;display:inline-flex;align-items:center;
  justify-content:center;font-size:.8rem;flex:0 0 22px}
.ava.gemini{background:#1a73e8} .ava.muse{background:#7c3aed}
.msg .md{line-height:1.55;font-size:.85rem}
.msg .md h1{font-size:1.05rem;margin:.5rem 0 .3rem}
.msg .md h2{font-size:.98rem;margin:.5rem 0 .3rem}
.msg .md h3{font-size:.92rem;margin:.45rem 0 .25rem}
.msg .md h4{font-size:.88rem;margin:.4rem 0 .2rem}
.msg .md ul,.msg .md ol{margin:.3rem 0 .3rem 1.1rem;padding:0}
.msg .md li{margin:.15rem 0}
.msg .md pre{background:#0d1117;border:1px solid var(--border);border-radius:6px;
  padding:.5rem;overflow-x:auto;margin:.4rem 0;font-size:.78rem}
.msg .md code{background:#0d1117;border:1px solid var(--border);border-radius:4px;
  padding:.05rem .3rem;font-size:.78rem}
.msg .md pre code{background:none;border:none;padding:0}
.msg .md hr{border:none;border-top:1px solid var(--border);margin:.5rem 0}
.msg .md b{color:#fff}
.ava.lmstudio{background:#16a34a} .ava.deepseek{background:#0ea5e9}
.ava.claude{background:#ea580c}
.who.gemini{color:#8ab4f8} .who.muse{color:#c4b5fd}
.who.lmstudio{color:#86efac} .who.deepseek{color:#7dd3fc} .who.claude{color:#fdba74}
.msg .time{font-size:.68rem;color:var(--muted);margin-top:.3rem;text-align:right}
.typing{display:flex;align-items:center;gap:.5rem;padding:.4rem 1rem}
.dot-ava{width:26px;height:26px;border-radius:99px;background:var(--bg3);
  display:inline-flex;align-items:center;justify-content:center}
.tdots i{display:inline-block;width:7px;height:7px;border-radius:99px;
  background:var(--muted);margin:0 2px;animation:blink 1.2s infinite}
.tdots i:nth-child(2){animation-delay:.2s} .tdots i:nth-child(3){animation-delay:.4s}
@keyframes blink{0%,100%{opacity:.25}50%{opacity:1}}
.chat-input{display:flex;gap:.5rem;padding:.7rem;border-top:1px solid var(--border);
  background:#0d1117;align-items:center}
.chat-input input{flex:1;background:var(--bg3);border:1px solid var(--border);
  border-radius:99px;padding:.65rem 1rem;color:var(--text);font-size:.95rem;min-width:0}
.agent-sel{background:var(--bg3);border:1px solid var(--border);color:var(--text);
  border-radius:99px;padding:.55rem .6rem;font-size:.85rem;max-width:130px}
.send-btn{width:44px;height:44px;min-height:44px;border-radius:99px;padding:0;
  margin:0;flex:0 0 44px;font-size:1.1rem}
</style>

<script>
const AGENT_META = {
  gemini:{label:'Gemini', icon:'🔵', cls:'gemini'},
  muse:{label:'Muse', icon:'🟣', cls:'muse'},
  lmstudio:{label:'LM Studio', icon:'🟢', cls:'lmstudio'},
  deepseek:{label:'DeepSeek', icon:'🔷', cls:'deepseek'},
};
const box = document.getElementById('chat-msgs');
const typing = document.getElementById('typing');
let lastId = '';

function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

function md(s){
  // escape trước, rồi render markdown cơ bản
  let h = esc(s);
  // code block ```...```
  h = h.replace(/```(\\w*)\\n([\\s\\S]*?)```/g, '<pre><code>$2</code></pre>');
  // inline code `...`
  h = h.replace(/`([^`\\n]+)`/g, '<code>$1</code>');
  // headers ### / ## / #
  h = h.replace(/^#### (.*)$/gm, '<h4>$1</h4>');
  h = h.replace(/^### (.*)$/gm, '<h3>$1</h3>');
  h = h.replace(/^## (.*)$/gm, '<h2>$1</h2>');
  h = h.replace(/^# (.*)$/gm, '<h1>$1</h1>');
  // bold **...** và __...__
  h = h.replace(/\\*\\*([^*]+)\\*\\*/g, '<b>$1</b>');
  h = h.replace(/__([^_]+)__/g, '<b>$1</b>');
  // italic *...* (tránh nhầm với list)
  h = h.replace(/(^|[^*\\w])\\*([^*\\n]+)\\*/g, '$1<i>$2</i>');
  // unordered list
  h = h.replace(/^(?:[-*] .*(?:\\n[-*] .*)*)/gm, function(m){
    return '<ul>' + m.replace(/^[-*] (.*)$/gm, '<li>$1</li>') + '</ul>';
  });
  // ordered list
  h = h.replace(/^(?:\\d+\\. .*(?:\\n\\d+\\. .*)*)/gm, function(m){
    return '<ol>' + m.replace(/^\\d+\\. (.*)$/gm, '<li>$1</li>') + '</ol>';
  });
  // horizontal rule
  h = h.replace(/^---+$/gm, '<hr>');
  // xuống dòng
  h = h.replace(/\n/g, '<br>');
  return h;
}

function renderMsg(m){
  const div = document.createElement('div');
  if(m.role === 'user'){
    div.className = 'msg user';
    div.innerHTML = esc(m.content) + `<div class="time">${(m.created_at||'').slice(11,16)}</div>`;
  } else {
    const meta = AGENT_META[m.agent] || {label:m.agent||'AI', icon:'🤖', cls:'muse'};
    div.className = 'msg ai';
    div.innerHTML = `<div class="who ${meta.cls}"><span class="ava ${meta.cls}">${meta.icon}</span>${meta.label}</div>`
      + `<div class="md">` + md(m.content) + `</div>` + `<div class="time">${(m.created_at||'').slice(11,16)}</div>`;
  }
  div.dataset.id = m.id;
  return div;
}

async function loadHistory(){
  try{
    const r = await fetch('/api/chat/history');
    const d = await r.json();
    box.innerHTML = '';
    (d.messages||[]).forEach(m=>{ box.appendChild(renderMsg(m)); lastId = m.id; });
    box.scrollTop = box.scrollHeight;
  }catch(e){}
}

async function send(){
  const inp = document.getElementById('chat-text');
  const text = inp.value.trim();
  if(!text) return;
  const agent = document.getElementById('agent-sel').value;
  inp.value = '';
  // hiện tin user ngay
  box.appendChild(renderMsg({id:'tmp', role:'user', content:text, created_at:new Date().toISOString()}));
  box.scrollTop = box.scrollHeight;
  // hiện typing
  const meta = agent==='auto' ? {label:'AI', icon:'🤖'} : AGENT_META[agent];
  document.getElementById('typing-ava').textContent = meta.icon;
  document.getElementById('typing-name').textContent = agent==='auto' ? 'AI đang nghĩ...' : meta.label + ' đang nhập...';
  typing.style.display = 'flex';
  box.scrollTop = box.scrollHeight;
  try{
    const r = await fetch('/api/chat/send', {
      method:'POST',
      headers:{'Content-Type':'application/x-www-form-urlencoded'},
      body:'msg='+encodeURIComponent(text)+'&agent='+encodeURIComponent(agent)+'&csrf='+encodeURIComponent(CSRF),
    });
    const d = await r.json();
    typing.style.display = 'none';
    if(d.message){
      box.appendChild(renderMsg(d.message));
      box.scrollTop = box.scrollHeight;
    } else if(d.error){
      box.appendChild(renderMsg({id:'e', role:'ai', agent:'muse', content:'⚠️ '+d.error, created_at:''}));
    }
  }catch(e){
    typing.style.display = 'none';
  }
}

document.getElementById('chat-send').onclick = send;
document.getElementById('chat-text').addEventListener('keydown', e=>{
  if(e.key === 'Enter') send();
});
loadHistory();
setInterval(async ()=>{
  // poll tin mới (đơn giản: reload nếu có tin lạ)
  try{
    const r = await fetch('/api/chat/history');
    const d = await r.json();
    const msgs = d.messages||[];
    if(msgs.length && msgs[msgs.length-1].id !== lastId){
      const seen = new Set([...box.children].map(c=>c.dataset.id));
      msgs.forEach(m=>{ if(!seen.has(m.id)){ box.appendChild(renderMsg(m)); } });
      lastId = msgs[msgs.length-1].id;
      box.scrollTop = box.scrollHeight;
    }
  }catch(e){}
}, 3000);
</script>
"""
        _body = _body.replace("<script>",
                              "<script>\nconst CSRF=" + json.dumps(csrf) + ";", 1)
        self._send(page("Chat AI", _body, active="/chat", user=user))

    def _dashboard(self, user):
        tasks = orch.list_tasks(10)
        cards = ""
        for t in tasks:
            cards += (f"<div class='card'><h3><a href='/task?id={t['id']}' "
                      f"style='color:var(--text)'>{html.escape(t['title'][:60])}</a></h3>"
                      f"<span class='badge {html.escape(t['status'])}'>{html.escape(t['status'])}</span> "
                      f"<span class='muted' style='font-size:.8rem'>{t['created_at']}</span></div>")
        if not cards:
            cards = "<div class='card'><p class='muted'>Chưa có task nào. <a href='/new' style='color:var(--accent)'>Giao việc mới →</a></p></div>"

        self._send(page("Tổng quan", f"""
<h1 style="display:flex;align-items:center;gap:.5rem">
<span style="color:var(--accent)">↗</span> Live activity
<span id="live-stats" class="muted" style="margin-left:auto;font-size:.85rem;font-weight:400"></span>
</h1>

<div class="live-wrap">
<svg id="flow-svg" class="flow-svg" preserveAspectRatio="none"></svg>
<div class="live-cols">
  <div class="live-col">
    <div class="live-head">WORK</div>
    <div id="work-cards"></div>
  </div>
  <div class="live-col">
    <div class="live-head">AI FEATURES</div>
    <div id="kind-cards"></div>
  </div>
  <div class="live-col">
    <div class="live-head">MACHINES &amp; PROVIDERS</div>
    <div id="agent-cards"></div>
  </div>
</div>
</div>

<h2 style="margin-top:1.5rem">Task gần đây</h2>
<div class="grid">{cards}</div>
<div style="margin:1rem 0"><a href="/new" class="btn">➕ Giao việc mới</a></div>

<style>
.live-wrap{{position:relative;background:#0d1117;border:1px solid var(--border);
  border-radius:16px;padding:1.2rem .8rem;overflow:hidden;margin:1rem 0}}
.flow-svg{{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:0}}
.live-cols{{position:relative;z-index:1;display:grid;grid-template-columns:1fr;gap:1rem}}
@media(min-width:768px){{.live-cols{{grid-template-columns:1fr 1.2fr 1.2fr}}}}
.live-head{{font-size:.75rem;letter-spacing:.12em;color:var(--muted);margin-bottom:.6rem}}
.live-card{{background:#161b22;border:1px solid var(--border);border-radius:12px;
  padding:.8rem .9rem;margin-bottom:.7rem;position:relative;transition:border-color .3s}}
.live-card.active{{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}}
.live-card .t{{font-weight:600;font-size:.95rem;display:flex;align-items:center;gap:.5rem}}
.live-card .s{{font-size:.8rem;color:var(--muted);margin-top:.25rem}}
.live-card .badge-n{{position:absolute;top:-8px;right:-8px;background:var(--accent);color:#fff;
  font-size:.75rem;font-weight:700;min-width:22px;height:22px;border-radius:99px;
  display:flex;align-items:center;justify-content:center;padding:0 6px}}
.slotbar{{height:5px;background:var(--bg3);border-radius:99px;margin-top:.5rem;overflow:hidden}}
.slotbar i{{display:block;height:100%;border-radius:99px;background:var(--accent);transition:width .5s}}
.dot{{width:9px;height:9px;border-radius:99px;display:inline-block;flex:0 0 9px}}
.dot.g{{background:#3fb950}} .dot.y{{background:#d29922}} .dot.r{{background:#f85149}} .dot.b{{background:#58a6ff}}
.flow-line{{fill:none;stroke-width:2;opacity:.55}}
.flow-line.hot{{stroke:#a371f7;stroke-dasharray:6 6;animation:dash 1s linear infinite}}
.flow-line.warm{{stroke:#d29922;stroke-dasharray:5 7;opacity:.4}}
.flow-line.cold{{stroke:#484f58;opacity:.25}}
.flow-line.fast{{stroke:#58a6ff;opacity:.5}}
@keyframes dash{{to{{stroke-dashoffset:-24}}}}
.flow-lbl{{font-size:11px;fill:var(--muted)}}
</style>

<script>
const KIND_ICON = {{"summarize":"📝","classify":"🏷️","code":"💻","reasoning":"🧠","verify":"✅",
  "extract":"📤","translate":"🌐","quick_qa":"💬","plan":"📋","decide":"🎯","browse":"🌐","execute":"⚙️","orchestrate":"🎼"}};
const AGENT_COLOR = {{"gemini":"b","lmstudio":"g","muse":"y","deepseek":"r"}};
const AGENT_LABEL = {{"gemini":"Gemini","lmstudio":"LM Studio","muse":"Muse","deepseek":"DeepSeek"}};

async function loadLive(){{
  try{{
    const r = await fetch('/api/router'); const d = await r.json();
    const agents = d.agents || [];
    const inFlight = d.in_flight || 0;
    const totalCalls = d.total_calls || 0;
    const hosts = agents.length;

    // header stats
    let failed = 0, calls = 0;
    agents.forEach(a=>{{ failed += a.fails||0; calls += a.calls||0; }});
    const failPct = calls ? (failed/calls*100).toFixed(1) : '0';
    document.getElementById('live-stats').textContent =
      `${{hosts}} hosts · ${{inFlight}} in flight · ${{calls}} calls · ${{failPct}}% failed`;

    // WORK column: task queue
    const st = d.tasks_by_status || {{}};
    const pending = st.pending||0, running = st.running||0;
    document.getElementById('work-cards').innerHTML =
      `<div class="live-card ${{running?'active':''}}">`
      + `<div class="t">📚 Task Queue</div>`
      + `<div class="s">${{running}} đang chạy · ${{pending}} chờ</div></div>`;

    // AI FEATURES: kinds từ agent stats (tạm dùng agents làm features)
    // -> hiển thị theo kind phổ biến: lấy từ tasks gần đây thì cần API khác, tạm hardcode các kind chính
    const kinds = [
      {{k:'summarize', label:'Summarize & Classify'}},
      {{k:'code', label:'Code & Reasoning'}},
      {{k:'verify', label:'Verify'}},
    ];
    document.getElementById('kind-cards').innerHTML = kinds.map((kd,i)=>{{
      const active = inFlight>0 && i===1;
      return `<div class="live-card ${{active?'active':''}}" data-kind="${{kd.k}}">`
        + `<div class="t">${{KIND_ICON[kd.k]||'⚡'}} ${{kd.label}}</div>`
        + `<div class="s">${{active? inFlight+' in flight' : 'idle'}}</div>`
        + (active?`<span class="badge-n">${{inFlight}}</span>`:'')
        + `</div>`;
    }}).join('');

    // MACHINES & PROVIDERS
    const order = ['gemini','lmstudio','muse','deepseek'];
    const sorted = agents.slice().sort((a,b)=> order.indexOf(a.agent)-order.indexOf(b.agent));
    document.getElementById('agent-cards').innerHTML = sorted.map(a=>{{
      const pct = a.max_slots ? Math.round(a.in_flight/a.max_slots*100) : 0;
      const lat = a.avg_ms ? (a.avg_ms>=1000 ? (a.avg_ms/1000).toFixed(1)+' s' : a.avg_ms+' ms') : '—';
      const dot = a.in_flight>0 ? 'g' : (a.calls>0 ? 'y' : 'b');
      const col = AGENT_COLOR[a.agent]||'b';
      return `<div class="live-card ${{a.in_flight>0?'active':''}}" data-agent="${{a.agent}}">`
        + `<div class="t"><span class="dot ${{dot}}"></span> ${{AGENT_LABEL[a.agent]||a.agent}}</div>`
        + `<div class="s">${{a.in_flight}}/${{a.max_slots}} slots · ${{lat}}</div>`
        + `<div class="slotbar"><i style="width:${{pct}}%"></i></div></div>`;
    }}).join('');

    drawFlows(agents);
  }}catch(e){{}}
}}

function drawFlows(agents){{
  const svg = document.getElementById('flow-svg');
  const wrap = svg.parentElement;
  const W = wrap.clientWidth, H = wrap.clientHeight;
  svg.setAttribute('viewBox', `0 0 ${{W}} ${{H}}`);
  const workEl = document.querySelector('#work-cards .live-card');
  const kindEls = document.querySelectorAll('#kind-cards .live-card');
  const agentEls = document.querySelectorAll('#agent-cards .live-card');
  if(!workEl || !kindEls.length || !agentEls.length) return;
  const wr = wrap.getBoundingClientRect();
  const cx = el => {{ const r = el.getBoundingClientRect();
    return {{x: r.left - wr.left + r.width/2, y: r.top - wr.top + r.height/2, w: r.width}}; }};
  const wp = cx(workEl);
  let html = '';
  kindEls.forEach((ke,ki)=>{{
    const kp = cx(ke);
    // work -> kind
    const hot = ke.classList.contains('active');
    html += `<path class="flow-line ${{hot?'hot':'cold'}}" d="M ${{wp.x+wp.w/2}} ${{wp.y}} C ${{wp.x+wp.w/2+60}} ${{wp.y}}, ${{kp.x-kp.w/2-60}} ${{kp.y}}, ${{kp.x-kp.w/2}} ${{kp.y}}"/>`;
    // kind -> agents (round-robin theo index)
    agentEls.forEach((ae,ai)=>{{
      if((ki+ai)%2!==0) return;
      const ap = cx(ae);
      const busy = ae.classList.contains('active');
      const cls = busy ? 'warm' : (ai===0 ? 'fast' : 'cold');
      html += `<path class="flow-line ${{cls}}" d="M ${{kp.x+kp.w/2}} ${{kp.y}} C ${{kp.x+kp.w/2+60}} ${{kp.y}}, ${{ap.x-ap.w/2-60}} ${{ap.y}}, ${{ap.x-ap.w/2-60}} ${{ap.y}}"/>`;
    }});
  }});
  svg.innerHTML = html;
}}

loadLive(); setInterval(loadLive, 4000);
window.addEventListener('resize', ()=>{{ /* redraw on next tick */ }});
</script>
""",
            active="/", user=user))

    def _new_form(self, user):
        csrf = self._csrf_token() or ""
        self._send(page("Giao việc mới",
            f"""<h1>➕ Giao việc mới</h1>
            <div class="card"><form method="post" action="/run">
            <input type="hidden" name="csrf" value="{csrf}">
            <label>Tiêu đề task</label>
            <input type="text" name="title" placeholder="VD: Tóm tắt báo cáo tuần" required>
            <label>Mô tả chi tiết (mỗi dòng 1 subtask: kind | nội dung)</label>
            <textarea name="subs" placeholder="summarize | Tóm tắt file báo cáo đính kèm&#10;verify | Kiểm tra số liệu có khớp không&#10;&#10;kind hỗ trợ: code, reasoning, plan, summarize, classify, extract, translate, verify, quick_qa"></textarea>
            <p class="muted" style="font-size:.85rem">💡 Hệ thống tự gợi ý skill phù hợp và ước lượng token trước khi chạy.</p>
            <button type="submit">🚀 Chạy task</button>
            </form></div>""", active="/new", user=user))

    def _task_detail(self, user, tid):
        t = orch.get_task(tid)
        if not t:
            self._send(page("404", "<h1>Không tìm thấy task</h1>", user=user), 404)
            return
        done = sum(1 for s in t["subtasks"] if s["success"])
        total = len(t["subtasks"])
        pct = int(done / total * 100) if total else 0

        subs = ""
        for s in t["subtasks"]:
            cls = "ok" if s["success"] else "fail"
            out = (s["output"] or s["error"] or "")[:3000]
            subs += (f"<div class='subtask {cls}'>"
                     f"<span class='agent-tag'>🤖 {html.escape(s['agent'] or '?')}</span>"
                     f"<span class='agent-tag'>{html.escape(s['kind'])}</span>"
                     f"<strong>{'✅' if s['success'] else '❌'}</strong>"
                     f"<pre>{html.escape(out)}</pre></div>")

        # token cho task này
        tok_html = ""
        if _tracker:
            rows = _tracker.summary_by_task(50)
            mine = next((r for r in rows if r["task_id"] == tid), None)
            if mine:
                tok_html = (f"<div class='card'><h3>🎟️ Token đã dùng</h3>"
                            f"<p>Input: <b>{fmt_num(mine['tin'])}</b> · Output: <b>{fmt_num(mine['tout'])}</b> · "
                            f"Calls: <b>{mine['calls']}</b></p></div>")

        final = f"<div class='card'><h3>📦 Kết quả tổng hợp</h3><pre>{html.escape((t['final'] or '')[:5000])}</pre></div>" if t.get("final") else ""

        self._send(page(t["title"][:30],
            f"<p><a href='/' style='color:var(--accent)'>← Tổng quan</a></p>"
            f"<h1>{html.escape(t['title'])}</h1>"
            f"<p><span class='badge {html.escape(t['status'])}'>{html.escape(t['status'])}</span> "
            f"<span class='muted'>{t['created_at']}</span></p>"
            f"<div class='progress'><i style='width:{pct}%'></i></div>"
            f"<p class='muted'>{done}/{total} subtask xong</p>"
            f"{tok_html}{final}<h2>Chi tiết subtask</h2>{subs}",
            active="/", user=user))

    def _history(self, user):
        tasks = orch.list_tasks(50)
        rows = "".join(
            f"<tr><td><a href='/task?id={t['id']}' style='color:var(--accent)'>{t['id']}</a></td>"
            f"<td>{html.escape(t['title'][:45])}</td>"
            f"<td><span class='badge {html.escape(t['status'])}'>{html.escape(t['status'])}</span></td>"
            f"<td class='muted'>{t['created_at'][:16]}</td></tr>"
            for t in tasks)
        self._send(page("Lịch sử",
            f"<h1>📜 Lịch sử task</h1><div class='card' style='overflow-x:auto'>"
            f"<table class='tbl'><tr><th>ID</th><th>Tiêu đề</th><th>TT</th><th>Tạo lúc</th></tr>{rows}</table></div>",
            active="/history", user=user))

    def _tokens_page(self, user, q):
        # --- Token Usage Dashboard: chỉ đọc dữ liệu, không sửa logic nào ---
        AGENT_META = [
            ("gemini", "🔵", "Gemini", "#4285F4"),
            ("muse", "🟣", "Muse", "#A855F7"),
            ("lmstudio", "🟢", "LM Studio", "#22C55E"),
            ("deepseek", "🔷", "DeepSeek", "#0EA5E9"),
        ]
        aemoji = {a: e for a, e, _, _ in AGENT_META}
        aname = {a: n for a, _, n, _ in AGENT_META}
        acolor = {a: c for a, _, _, c in AGENT_META}

        days = q.get("days", ["30"])[0]
        days = int(days) if days in ("7", "14", "30") else 30
        agent_f = q.get("agent", ["all"])[0]
        if agent_f not in [a for a, _, _, _ in AGENT_META]:
            agent_f = "all"

        if not _tracker:
            self._send(page("Token Usage",
                "<h1>🎟️ Token Usage</h1><p class='muted'>Chưa có dữ liệu.</p>",
                active="/tokens", user=user))
            return

        import sqlite3
        from tokens.tracker import estimate_cost
        con = sqlite3.connect(_tracker.db_path)
        con.row_factory = sqlite3.Row

        awhere = "" if agent_f == "all" else "AND agent = ?"
        aparams = [] if agent_f == "all" else [agent_f]

        def _tot(where, params):
            r = con.execute(
                "SELECT COALESCE(SUM(input_tokens),0) AS tin,"
                " COALESCE(SUM(output_tokens),0) AS tout, COUNT(*) AS calls"
                f" FROM token_usage WHERE {where}", params).fetchone()
            return (r["tin"] or 0, r["tout"] or 0, r["calls"] or 0)

        t_today = _tot("substr(ts,1,10)=substr(datetime('now'),1,10)" + awhere, aparams)
        t_7 = _tot("ts >= datetime('now','-7 days')" + awhere, aparams)
        t_30 = _tot("ts >= datetime('now','-30 days')" + awhere, aparams)

        # Theo AI trong khoảng đã chọn
        rows = con.execute(
            "SELECT agent, model, SUM(input_tokens) AS tin,"
            " SUM(output_tokens) AS tout, COUNT(*) AS calls"
            " FROM token_usage WHERE ts >= datetime('now',?)" + awhere +
            " GROUP BY agent, model ORDER BY tin DESC",
            (f"-{days} days",) + tuple(aparams)).fetchall()

        by_agent = {}
        for r in rows:
            a = r["agent"] or "?"
            d = by_agent.setdefault(a, {"tin": 0, "tout": 0, "calls": 0, "cost": 0.0})
            d["tin"] += r["tin"] or 0
            d["tout"] += r["tout"] or 0
            d["calls"] += r["calls"] or 0
            d["cost"] += estimate_cost(r["model"] or "", r["tin"] or 0, r["tout"] or 0)
        by_agent = sorted(by_agent.items(), key=lambda kv: kv[1]["tin"], reverse=True)
        period_cost = sum(d["cost"] for _, d in by_agent)

        # Ma trận ngày x AI
        mrows = con.execute(
            "SELECT substr(ts,1,10) AS day, agent,"
            " SUM(input_tokens) AS tin, SUM(output_tokens) AS tout, COUNT(*) AS calls"
            " FROM token_usage WHERE ts >= datetime('now',?)" + awhere +
            " GROUP BY day, agent ORDER BY day DESC, agent",
            (f"-{days} days",) + tuple(aparams)).fetchall()
        con.close()

        # --- Bộ lọc ---
        def _pill(href, label, on):
            return (f"<a href='{href}' class='pill{' on' if on else ''}'>{label}</a>")
        day_pills = "".join(
            _pill(f"/tokens?days={d}&agent={agent_f}", f"{d} ngày", d == days)
            for d in (7, 14, 30))
        agent_pills = _pill(f"/tokens?days={days}&agent=all", "Tất cả",
                            agent_f == "all") + "".join(
            _pill(f"/tokens?days={days}&agent={a}", f"{e} {aname[a]}", a == agent_f)
            for a, e, _, _ in AGENT_META)

        # --- Thẻ tổng quan ---
        def _card(v_in, v_out, calls, label):
            return (f"<div class='card'><div class='stat'>{fmt_num(v_in + v_out)}</div>"
                    f"<p class='muted'>{label}<br><small>{fmt_num(v_in)} in · "
                    f"{fmt_num(v_out)} out · {calls} calls</small></p></div>")
        overview = (_card(*t_today, "Hôm nay") + _card(*t_7, "7 ngày qua")
                    + _card(*t_30, "30 ngày qua"))

        # --- Theo từng AI ---
        max_tok = max([d["tin"] for _, d in by_agent] + [1])
        agent_cards = ""
        for a, d in by_agent:
            c = acolor.get(a, "#888")
            e = aemoji.get(a, "⚪")
            n = aname.get(a, a)
            w = int(d["tin"] / max_tok * 100)
            agent_cards += (
                f"<div class='card'><h3>{e} {html.escape(n)}</h3>"
                f"<div class='stat'>{fmt_num(d['tin'] + d['tout'])}</div>"
                f"<p class='muted'>{fmt_num(d['tin'])} in · {fmt_num(d['tout'])} out<br>"
                f"{d['calls']} calls · ~${d['cost']:.4f}</p>"
                f"<div class='tbar'><span class='track'>"
                f"<span class='fill' style='width:{w}%;background:{c}'></span>"
                f"</span></div></div>")
        if not agent_cards:
            agent_cards = "<p class='muted'>Chưa có dữ liệu trong khoảng này.</p>"

        # --- Biểu đồ cột chồng SVG: token/ngày theo AI ---
        day_list = sorted({r["day"] for r in mrows})
        per_day = {}
        for r in mrows:
            per_day.setdefault(r["day"], {})[r["agent"]] = (r["tin"] or 0) + (r["tout"] or 0)
        agents_in_chart = sorted({a for d in per_day.values() for a in d})
        W, H, ML, MB, MT = 620, 230, 46, 30, 12
        n = max(len(day_list), 1)
        bw = min(34, (W - ML - 14) / n * 0.62)
        step = (W - ML - 14) / n
        vmax = max([sum(d.values()) for d in per_day.values()] + [1])
        svg = [f"<svg viewBox='0 0 {W} {H}' style='width:100%;height:auto' role='img'>"]
        # lưới ngang
        for i in range(5):
            y = MT + (H - MT - MB) * i / 4
            val = vmax * (1 - i / 4)
            svg.append(f"<line x1='{ML}' y1='{y:.0f}' x2='{W-8}' y2='{y:.0f}' "
                       f"stroke='var(--border)' stroke-width='1'/>")
            svg.append(f"<text x='{ML-6}' y='{y+4:.0f}' text-anchor='end' font-size='10' "
                       f"fill='var(--muted)'>{fmt_num(int(val))}</text>")
        for i, day in enumerate(day_list):
            x = ML + step * i + (step - bw) / 2
            y0 = H - MB
            for a in agents_in_chart:
                v = per_day[day].get(a, 0)
                if not v:
                    continue
                h = (H - MT - MB) * v / vmax
                y0 -= h
                svg.append(f"<rect x='{x:.0f}' y='{y0:.0f}' width='{bw:.0f}' height='{h:.0f}' "
                           f"fill='{acolor.get(a, '#888')}' rx='2'>"
                           f"<title>{day} · {aname.get(a, a)}: {fmt_num(v)}</title></rect>")
            if n <= 31 and (n <= 14 or i % 2 == 0):
                svg.append(f"<text x='{x + bw/2:.0f}' y='{H-10}' text-anchor='middle' "
                           f"font-size='10' fill='var(--muted)'>{day[5:]}</text>")
        svg.append("</svg>")
        legend = " ".join(
            f"<span style='display:inline-flex;align-items:center;gap:4px;margin-right:10px'>"
            f"<span style='width:10px;height:10px;border-radius:3px;background:{acolor.get(a, '#888')};"
            f"display:inline-block'></span>{aemoji.get(a, '')} {aname.get(a, a)}</span>"
            for a in agents_in_chart)
        chart_html = ("<div class='card'>" + "".join(svg)
                      + f"<p class='muted' style='margin-top:8px'>{legend}</p></div>"
                      if day_list else "<p class='muted'>Chưa có dữ liệu.</p>")

        # --- Bảng chi tiết ngày x AI ---
        detail_rows = "".join(
            f"<tr><td>{r['day']}</td>"
            f"<td>{aemoji.get(r['agent'], '')} {html.escape(aname.get(r['agent'], r['agent'] or '?'))}</td>"
            f"<td>{r['calls']}</td><td>{fmt_num(r['tin'])}</td>"
            f"<td>{fmt_num(r['tout'])}</td>"
            f"<td><b>{fmt_num((r['tin'] or 0) + (r['tout'] or 0))}</b></td></tr>"
            for r in mrows) or ("<tr><td colspan='6' class='muted'>Chưa có dữ liệu.</td></tr>")

        css_extra = ("<style>.pill{display:inline-block;padding:6px 12px;margin:0 6px 6px 0;"
                     "border:1px solid var(--border);border-radius:99px;color:var(--text);"
                     "text-decoration:none;font-size:.85rem;background:var(--bg2)}"
                     ".pill.on{background:var(--accent);border-color:var(--accent);"
                     "color:#fff;font-weight:600}</style>")

        self._send(page("Token Usage", css_extra +
            f"<h1>🎟️ Token Usage</h1>"
            f"<div style='margin-bottom:10px'><span class='muted'>Khoảng: </span>{day_pills}</div>"
            f"<div style='margin-bottom:14px'><span class='muted'>AI: </span>{agent_pills}</div>"
            f"<div class='grid cols3'>{overview}</div>"
            f"<div class='card' style='margin-top:12px'><div class='stat'>~${period_cost:.4f}</div>"
            f"<p class='muted'>Chi phí ước tính ({days} ngày, theo bảng giá public)</p></div>"
            f"<h2>Theo từng AI ({days} ngày)</h2><div class='grid cols3'>{agent_cards}</div>"
            f"<h2>Biểu đồ theo ngày</h2>{chart_html}"
            f"<h2>Chi tiết ngày × AI</h2><div class='card' style='overflow-x:auto'>"
            f"<table class='tbl'><tr><th>Ngày</th><th>AI</th><th>Calls</th>"
            f"<th>Input</th><th>Output</th><th>Tổng</th></tr>{detail_rows}</table></div>"
            f"<p class='muted' style='margin-top:12px'>Số liệu từ bảng token_usage · "
            f"LM Studio chạy local nên miễn phí · một số dòng là ước lượng (estimated=1).</p>",
            active="/tokens", user=user))

    def _accounting_room(self, user):
        """🏦 Phòng Kế Toán AI — 5 agent, mỗi con 1 bàn + nhiệm vụ kế toán riêng."""
        # --- số liệu tài chính ---
        fin_stats = {}
        try:
            from data_providers.finance_data import fetch_dashboard, is_configured
            if is_configured():
                d = fetch_dashboard()
                if d:
                    fin_stats = d.get("stats", {})
        except Exception:
            fin_stats = {}
        # --- token hôm nay ---
        today_tokens = {}
        try:
            from data_providers.token_stats import get_stats
            s = get_stats(1)
            for a, v in s.get("by_ai", {}).items():
                today_tokens[a] = {"tokens": v["in"] + v["out"], "calls": v["calls"]}
        except Exception:
            pass
        # --- trạng thái busy/idle từ router ---
        inflight = {}
        try:
            from orchestrator.router import get_router
            for a in get_router().all_stats():
                inflight[a["agent"]] = a.get("in_flight", 0)
        except Exception:
            pass
        # ---------- kho kiến thức chung (Drive) ----------
        kb_status = {"skills": 0, "rules": 0, "loaded_at": "?"}
        kb_link = "https://drive.google.com/drive/folders/1GYwsn2uiQ817qYRlZuFsCLUC9eJBk-nO"
        try:
            from data_providers.knowledge import status as kb_status_fn
            kb_status = kb_status_fn()
            kb_link = kb_status.get("drive_link", kb_link)
        except Exception:
            pass
        n_events = None
        try:
            from data_providers.calendar_data import upcoming
            n_events = len(upcoming(7))
        except Exception:
            pass
        drive_n, drive_built = None, None
        try:
            from data_providers.drive_index import index_status
            st = index_status()
            drive_n, drive_built = st.get("count", 0), st.get("last_build")
        except Exception:
            pass
        inflight = {}
        try:
            from orchestrator.router import get_router
            for a in get_router().all_stats():
                inflight[a["agent"]] = a.get("in_flight", 0)
        except Exception:
            pass

        AGENTS = [
            ("gemini",   "🦊", "Gemini",    "#4aa8ff", "📊 Phân tích chi tiêu"),
            ("muse",     "🦉", "Muse",      "#b07bff", "✅ Điều phối + Duyệt chi"),
            ("deepseek", "🦈", "DeepSeek",  "#39c5ff", "🔍 Soi công nợ"),
            ("lmstudio", "🐸", "LM Studio", "#3ddc84", "⚡ Trả lời nhanh"),
        ]
        desks = []
        for key, char, name, color, role in AGENTS:
            busy = inflight.get(key, 0) > 0
            t = today_tokens.get(key)
            if busy:
                badge = "<span class='ar-badge ar-busy'>🔄 ĐANG LÀM</span>"
            else:
                badge = "<span class='ar-badge ar-idle'>💤 RẢNH</span>"
            tok_txt = f"{fmt_num(t['tokens'])} token · {t['calls']} calls" if t else "chưa xài token"
            desks.append(
                f"<div class='ar-desk'>"
                f"<div class='ar-char'>{char}</div>"
                f"<div class='ar-name' style='border-color:{color}'>"
                f"<span class='ar-dot' style='background:{color}'></span>{name}</div>"
                f"<div class='ar-role'>{role}</div>"
                f"{badge}"
                f"<div class='ar-tok'>🎟️ {tok_txt}</div>"
                f"</div>")

        fin_cards = ""
        if fin_stats:
            for k, v in list(fin_stats.items())[:6]:
                fin_cards += (f"<div class='ar-fin'><div class='ar-fin-k'>{html.escape(k)}</div>"
                              f"<div class='ar-fin-v'>{html.escape(v)}</div></div>")
        else:
            fin_cards = ("<div class='ar-fin'><div class='ar-fin-k'>Finance Manager</div>"
                         "<div class='ar-fin-v'>chưa nối</div></div>")

        css = """
        .ar-live{color:#3ddc84;font-weight:600;}
        .ar-fin-strip{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px;}
        .ar-fin{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:8px 10px;}
        .ar-fin-k{font-size:11px;color:var(--muted);}
        .ar-fin-v{font-size:15px;font-weight:700;}
        .ar-room{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;background:var(--card);
          border:1px solid var(--border);border-radius:16px;padding:14px;
          background-image:radial-gradient(circle at 50% 0%,rgba(74,168,255,.08),transparent 70%);}
        .ar-desk{background:rgba(255,255,255,.03);border:1px solid var(--border);border-radius:12px;
          padding:10px 6px;text-align:center;}
        .ar-char{font-size:44px;line-height:1.2;filter:drop-shadow(0 2px 6px rgba(0,0,0,.4));}
        .ar-name{font-weight:700;margin-top:6px;padding:4px;border-bottom:3px solid;border-radius:6px;}
        .ar-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;}
        .ar-role{font-size:12px;color:var(--muted);margin:6px 0;}
        .ar-badge{display:inline-block;font-size:11px;font-weight:700;padding:3px 10px;border-radius:20px;}
        .ar-busy{background:#123f2b;color:#3ddc84;animation:ar-pulse 1.6s infinite;}
        .ar-idle{background:#3a3f47;color:#9aa0a6;}
        .ar-tok{font-size:11px;color:var(--muted);margin-top:6px;}
        @keyframes ar-pulse{0%,100%{opacity:1}50%{opacity:.55}}
        @media(max-width:700px){.ar-room{grid-template-columns:repeat(2,1fr);}.ar-fin-strip{grid-template-columns:repeat(2,1fr);}}
        """
        body = (f"<style>{css}</style>"
                f"<h1>🏦 Phòng Kế Toán AI <span class='ar-live'>● LIVE</span></h1>"
                f"<p class='muted'>Mỗi AI 1 bàn, 1 nhiệm vụ kế toán · tự refresh 30s</p>"
                f"<div class='ar-fin-strip'>{fin_cards}</div>"
                f"<div class='ar-room'>{''.join(desks)}</div>"
                f"<script>setTimeout(()=>location.reload(),30000)</script>")
        self._send(page("Phòng Kế Toán AI", body, active="/accounting-room", user=user))

    def _office3d_state_data(self):
        """Dữ liệu live cho Văn Phòng 3D: agents, finance, tokens, EC, drives."""
        import json as _j, os as _o
        AG = [
            ("gemini", "\U0001F98A", "Gemini", "#4aa8ff",
             "\U0001F4CA Phân tích chi tiêu", "ketoan"),
            ("muse", "\U0001F989", "Muse", "#b07bff",
             "✅ Điều phối + Duyệt chi", "dieuhanh"),
            ("deepseek", "\U0001F988", "DeepSeek", "#39c5ff",
             "\U0001F50D Soi công nợ", "dulieu"),
            ("lmstudio", "\U0001F438", "LM Studio", "#3ddc84",
             "⚡ Trả lời nhanh", "app"),
        ]
        rs = {}
        try:
            from orchestrator.router import get_router
            for s in get_router().all_stats():
                rs[s["agent"]] = s
        except Exception:
            pass
        rpg = {}
        try:
            _rp = _o.path.join(_o.path.dirname(_o.path.dirname(
                _o.path.abspath(__file__))), "data", "agent_rpg.json")
            rpg = _j.load(open(_rp, encoding="utf-8"))
        except Exception:
            pass
        tok = {}
        try:
            from data_providers.token_stats import get_stats
            for days in (1, 7, 30):
                s = get_stats(days)
                for a, v in s.get("by_ai", {}).items():
                    tok.setdefault(a, {})[days] = {
                        "tokens": v["in"] + v["out"],
                        "calls": v["calls"],
                        "cost": round(v.get("cost_usd", 0), 4),
                    }
        except Exception:
            pass
        agents = []
        for key, char, name, color, role, dept in AG:
            st = rs.get(key, {})
            inflight = st.get("in_flight", 0)
            calls = st.get("calls", 0)
            fails = st.get("fails", 0)
            ok = round((calls - fails) * 100 / calls, 1) if calls else 100.0
            if inflight > 0:
                status = "busy"
            else:
                try:
                    from data_providers.provider_health import provider_health
                    healthy, _ = provider_health(key)
                    status = "ready" if healthy else "error"
                except Exception:
                    status = "ready" if st.get("updated_at") else "stale"
                if calls and fails * 2 >= calls:
                    status = "error"
            rd = rpg.get("agents", {}).get(key, {})
            agents.append({
                "key": key, "char": char, "name": name, "color": color,
                "role": role, "dept": dept, "status": status,
                "in_flight": inflight, "rank": rd.get("rank", 1),
                "ok_rate": ok, "calls": calls,
                "tokens": tok.get(key, {}),
            })
        # Muse — trợ lý chính (thực thể thứ 6, data từ ai-manager)
        _muse_tok = {}
        try:
            _mp = _o.path.expanduser("~/workspace/ai-manager/token-muse-main.json")
            if _o.path.exists(_mp):
                _md = _j.load(open(_mp, encoding="utf-8"))
                _c30 = _md.get("cost_usd_30d", 0)
                _muse_tok = {
                    1: {"tokens": _md.get("tokens_1d", 0), "calls": 0,
                        "cost": round(_c30 / 30, 4) if _c30 else 0},
                    7: {"tokens": _md.get("tokens_7d", 0), "calls": 0,
                        "cost": round(_c30 / 30 * 7, 4) if _c30 else 0},
                    30: {"tokens": _md.get("tokens_30d", 0), "calls": 0,
                         "cost": round(_c30, 4)},
                }
        except Exception:
            pass
        agents.append({
            "key": "muse_main", "char": "🤖", "name": "Muse",
            "color": "#e879f9",
            "role": "🎯 Trợ lý chính của anh", "dept": "dieuhanh",
            "status": "ready" if _muse_tok else "stale",
            "in_flight": 0, "rank": 6, "ok_rate": 100.0, "calls": 0,
            "tokens": _muse_tok, "is_main": True,
        })
        # DeepSeek: prepaid (không refill) — đánh dấu để frontend hiện
        for _a in agents:
            if _a["key"] == "deepseek":
                _a["prepaid"] = True
        # --- Hiển thị VP Sống (CHỈ ĐỌC, không đổi luật) ---
        # Loi thoai + trang thai hien thi suy ra tu du lieu that san co:
        # router in_flight/provider health (status), feed vong chat (speakers/topic),
        # so file task-bus pending (viec cho anh duyet, chi dem, khong doi trang thai file).
        # Khong doi vong chat, task-bus, dashboard hay provider nao.
        vp = {"vp_topic": "", "vp_speakers": [], "vp_pending_count": 0,
              "vp_pending_by_agent": {}, "vp_pending_has_agent": False,
              "vp_pending_titles": [],
              "vp_last_round": 0, "vp_last_error": "", "vp_loop_started": False}
        try:
            import office_chat as _oc
            vp = _oc.get_vp_summary()
        except Exception:
            pass
        _disp = {"busy": "running", "ready": "waiting",
                 "stale": "resting", "error": "error"}
        _speakers = vp.get("vp_speakers") or []
        _topic = vp.get("vp_topic") or ""
        _pend = vp.get("vp_pending_count") or 0
        for _a in agents:
            _a["display_status"] = _disp.get(_a["status"], "resting")
            _st, _key, _nm = _a["status"], _a["key"], _a["name"]
            if _st == "error":
                _a["speech"] = "Gặp sự cố — kiểm tra provider"
            elif _st == "busy":
                _a["speech"] = "Đang chạy task"
            elif _st == "stale":
                _a["speech"] = "Nghỉ"
            elif _key in ("muse", "muse_main") and _pend > 0:
                _a["speech"] = "Chờ anh duyệt: %d việc đang chờ" % _pend
            elif _nm in _speakers and _topic:
                _a["speech"] = "Đang bàn: " + _topic
            elif _nm in _speakers:
                _a["speech"] = "Đang bàn trong vòng chat mới nhất"
            elif _key in ("gemini", "deepseek"):
                _a["speech"] = "Nghỉ — chờ vòng chat sau"
            else:
                _a["speech"] = "Sẵn sàng — chờ việc mới"
            # (a) So viec cho theo tung agent — CHI khi task-bus co truong gan
            # agent that (vp_pending_by_agent). Khong co -> 0, giao dien giu
            # nguyen tong o dai trang thai, khong bia phan bo tu from/to.
            _by = vp.get("vp_pending_by_agent") or {}
            _by_low = {str(k).strip().lower(): v for k, v in _by.items()}
            _a["pending_count"] = _by_low.get(_key.lower(),
                                              _by_low.get(_nm.lower(), 0))
        fin = {}
        try:
            from data_providers.finance_data import fetch_dashboard, is_configured
            if is_configured():
                d = fetch_dashboard()
                if d:
                    fin = d.get("stats", {})
        except Exception:
            pass
        drives = {}
        try:
            _c = "/tmp/ofc-drive-cache.json"
            if _o.path.exists(_c):
                drives = _j.load(open(_c, encoding="utf-8"))
        except Exception:
            pass
        ec = [
            {"id": "B1", "char": "\U0001F422", "name": "INSTA 360 (81GB)",
             "route": "G: → J:", "status": "done", "pct": 100,
             "detail": "81GB → J: xong"},
            {"id": "B2", "char": "\U0001F41C", "name": "Work (70GB)",
             "route": "G: → H:\\Work-G-temp", "status": "done", "pct": 100,
             "detail": "70GB → H: xong"},
            {"id": "B3", "char": "\U0001F9A5", "name": "Vertex/Pic/Fonts/2023 (26GB)",
             "route": "G: → H:", "status": "done", "pct": 100,
             "detail": "26GB → H: xong"},
            {"id": "B4", "char": "\U0001F9A3", "name": "EC Team (295GB)",
             "route": "H: → G:", "status": "done", "pct": 100,
             "detail": "295GB → G: xong"},
        ]
        rep = rpg.get("company", {}).get("reputation", 82)
        avg_ok = (round(sum(a["ok_rate"] for a in agents) / len(agents), 1)
                  if agents else 100.0)
        return {"agents": agents, "finance": fin, "ec": ec,
                "drives": drives, "vp": vp,
                "company": {"reputation": rep, "avg_ok": avg_ok}}

    def _api_office3d_state(self):
        """JSON live cho Văn Phòng 3D (poll 15/30/60s)."""
        import json as _j
        body = _j.dumps(self._office3d_state_data(),
                        ensure_ascii=False).encode("utf-8")
        self._send(body, 200, "application/json; charset=utf-8")


    def _van_phong_ai_song(self, user):
        """💼 Văn Phòng AI Sống — hội thoại nhóm nhiều agent + agent di chuyển, luồng khép kín về main chat."""
        path = os.path.join(os.path.dirname(__file__), "van-phong-ai-song.html")
        try:
            with open(path, "r", encoding="utf-8") as f:
                html_doc = f.read()
        except FileNotFoundError:
            html_doc = "<h1>💼 VP Sống</h1><p>Trang đang được cập nhật, anh quay lại sau ít phút.</p>"
        self._send(html_doc)

    def _api_office_chat(self, q):
        """Feed chat tu dong cua VP Song — chi doc, da qua cong dang nhap."""
        import json as _json
        try:
            import office_chat
            since_raw = q.get("since", ["0"])[0]
            try:
                since = int(since_raw)
            except ValueError:
                since = 0
            init = q.get("init", ["0"])[0] == "1"
            data = office_chat.get_messages(since=since, init=init)
            data.update(office_chat.get_status())
            data.update(office_chat.get_vp_summary())
            body = _json.dumps(data, ensure_ascii=False).encode()
            self._send(body, 200, "application/json; charset=utf-8")
        except Exception as e:
            body = _json.dumps({"messages": [], "latest_id": 0,
                                "error": str(e)}, ensure_ascii=False).encode()
            self._send(body, 200, "application/json; charset=utf-8")

    def _ai_office_3d(self, user):
        """🌐 Văn Phòng AI 3D — nhân vật di chuyển, tất cả trong 1 màn hình."""
        import json as _j, html as _h
        state = self._office3d_state_data()
        state_json = _j.dumps(state, ensure_ascii=False)
        csrf = self._csrf_token() or ""
        body = """<!DOCTYPE html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Văn Phòng AI 3D — Tom AI Orchestra</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;overflow:hidden;background:#070b12;color:#e8eef5;
 font-family:'Segoe UI',system-ui,Arial,sans-serif;font-size:13px}
#scene{position:fixed;inset:0}
#scene canvas{display:block}
.hud{position:fixed;z-index:10;pointer-events:none}
.hud .card{pointer-events:auto;background:rgba(13,20,32,.82);border:1px solid rgba(120,160,220,.22);
 border-radius:12px;padding:10px 12px;backdrop-filter:blur(8px);
 box-shadow:0 4px 18px rgba(0,0,0,.45)}
#hud-top{top:10px;left:10px;right:10px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
#hud-top .title{font-size:16px;font-weight:700;white-space:nowrap}
#hud-top .stats{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:#9fb2c8}
#hud-top .stats b{color:#fff}
#hud-top .navlinks{margin-left:auto;display:flex;gap:8px}
#hud-top .navlinks a{color:#7cc4ff;text-decoration:none;font-size:12px;
 border:1px solid #2a3d5c;border-radius:16px;padding:4px 12px;background:rgba(20,32,52,.7)}
#ec-strip{display:flex;gap:8px;align-items:center;font-size:12px}
.ec-mini{padding:3px 10px;border-radius:14px;font-weight:600;white-space:nowrap}
.ec-done{background:#14532d;color:#86efac}.ec-run{background:#78350f;color:#fcd34d}
.ec-wait{background:#1e293b;color:#94a3b8}
#hud-left{top:76px;left:66px;width:228px;max-height:calc(100vh - 280px);overflow-y:auto}
#hud-right{top:76px;right:10px;width:228px;max-height:calc(100vh - 280px);overflow-y:auto}
#hud-bottom{bottom:10px;left:10px;right:78px}
.sec-t{font-size:12px;text-transform:uppercase;letter-spacing:1px;color:#a8b8cc;
 margin:8px 0 6px;font-weight:700}
.sec-t:first-child{margin-top:0}
.agent-row{display:flex;align-items:center;gap:8px;padding:6px 4px;
 border-bottom:1px solid rgba(148,163,184,.14);font-size:12px;cursor:pointer}
.agent-row.selected{background:rgba(74,168,255,.16);border-radius:10px}
.agent-row:last-child{border-bottom:none}
.agent-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.agent-row .an{font-weight:700;white-space:nowrap}
.agent-row .st{margin-left:auto;font-size:11px;white-space:nowrap}
.tok{color:#a8b8cc;font-size:12px;margin-top:2px}
.st-busy{color:#fbbf24}.st-ready{color:#4ade80}.st-stale{color:#64748b}.st-error{color:#f87171}
.fin-row{display:flex;justify-content:space-between;font-size:12px;padding:3px 0;
 border-bottom:1px solid #16202f}
.fin-row:last-child{border:none}
.fin-row .v{color:#fff;font-weight:600;text-align:right}
.dbar{background:#0b1220;border-radius:6px;height:8px;overflow:hidden;margin:2px 0 6px}
.dbar>div{height:100%;border-radius:6px}
#ec-steps{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.ec-step{background:rgba(13,20,32,.82);border:1px solid #223148;border-radius:10px;
 padding:8px 10px;pointer-events:auto}
.ec-step .eid{font-weight:700;font-size:12px}
.ec-step .enm{font-size:11px;color:#9fb2c8;margin:2px 0}
#vp-live-box{max-height:150px;overflow-y:auto;display:flex;flex-direction:column;gap:6px;font-size:12px}
.vp-live-msg{background:rgba(13,20,32,.82);border:1px solid rgba(120,160,220,.22);border-radius:10px;padding:6px 10px}
.vp-live-msg.vp-grouped{margin-top:-4px;border-top-left-radius:6px;border-top-right-radius:6px}
.vp-live-msg b{color:#4aa8ff}
.vp-live-msg.sys{border-color:#16a34a}
.vp-live-msg.sys b{color:#4ade80}
/* ---- VP Song v2.5: chi hien thi, khong doi luat ---- */
.vp-strip{display:flex;gap:10px;flex-wrap:wrap;font-size:12px;color:#9fb2c8;
 background:rgba(11,18,32,.7);border:1px solid #223148;border-radius:10px;padding:7px 10px;margin-bottom:8px}
.vp-strip b{color:#fff}
.agent-row{border-left:4px solid #475569;border-radius:6px;padding-left:8px}
.agent-row.br-running{border-left-color:#22c55e}
.agent-row.br-waiting{border-left-color:#f59e0b}
.agent-row.br-error{border-left-color:#ef4444}
.agent-row.br-resting{border-left-color:#64748b}
.agent-speech{color:#c4d4e8;font-style:italic}
.vp-empty{color:#9fb2c8;font-size:12px;line-height:1.5}
.vp-empty b{color:#e8eef5}
#vp-flow{display:flex;gap:6px;align-items:stretch;flex-wrap:wrap;font-size:12px;margin-top:8px}
.flow-step{flex:1;min-width:120px;background:rgba(11,18,32,.7);border:1px solid #223148;
 border-radius:10px;padding:7px 9px}
.flow-step b{display:block;color:#fff;margin-bottom:2px}
.flow-step span{color:#9fb2c8}
.flow-arrow{align-self:center;color:#4aa8ff;font-weight:700;font-size:16px}
#vp-view-toggle{color:#7cc4ff;text-decoration:none;font-size:12px;cursor:pointer;
 border:1px solid #2a3d5c;border-radius:16px;padding:4px 12px;background:rgba(20,32,52,.7)}
#simple-view{display:none;position:fixed;inset:0;left:56px;overflow-y:auto;
 padding:16px;z-index:9;background:#070b12}
#simple-view .sv-card{background:rgba(13,20,32,.9);border:1px solid #223148;border-radius:12px;
 padding:12px 14px;margin-bottom:12px}
#simple-view h2{font-size:15px;margin-bottom:8px}
#simple-view .sv-row{display:flex;gap:10px;align-items:center;padding:8px 4px;
 border-bottom:1px solid #1a2636;font-size:13px}
#simple-view .sv-row:last-child{border-bottom:none}
body.simple-view{overflow:auto;height:auto}
body.simple-view #scene,body.simple-view .hud{display:none !important}
body.simple-view #simple-view{display:block}
body.simple-view #hud-top{display:none !important}
/* ---- v2.6 Liquid Glass (chi hien thi): 3 cot + search/tab + chon mem + hover-action + badge/nhan + divider pill + link card ---- */
.sv-search{width:100%;background:rgba(255,255,255,.06);border:1px solid rgba(120,160,220,.25);
 border-radius:20px;color:#fff;padding:8px 14px;font-size:13px;margin:6px 0;backdrop-filter:blur(8px)}
.sv-search::placeholder{color:#a8b8cc}
.sv-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:2px 0 8px}
.sv-tabs button{background:rgba(255,255,255,.06);border:1px solid rgba(120,160,220,.22);
 color:#c4d4e8;border-radius:16px;padding:4px 11px;font-size:12px;cursor:pointer}
.sv-tabs button.on{background:rgba(74,168,255,.28);border-color:#4aa8ff;color:#fff;font-weight:700}
.row-action{opacity:0;margin-left:auto;flex-shrink:0;background:rgba(74,168,255,.2);
 border:1px solid rgba(74,168,255,.5);color:#fff;border-radius:14px;padding:3px 10px;
 font-size:12px;cursor:pointer;transition:opacity .15s;white-space:nowrap}
.agent-row:hover .row-action,.task-row:hover .row-action,
.agent-row:focus-within .row-action,.task-row:focus-within .row-action{opacity:1}
@media(hover:none){.row-action{opacity:1}}
.sv-row.selected,.task-row.selected{background:rgba(74,168,255,.16);border-radius:10px}
.task-row{display:flex;gap:10px;align-items:center;padding:8px 4px;
 border-bottom:1px solid rgba(148,163,184,.14);font-size:13px;cursor:pointer}
.unread-badge{display:inline-block;background:#ef4444;color:#fff;border-radius:12px;
 padding:1px 9px;font-size:12px;font-weight:700;min-width:22px;text-align:center;vertical-align:middle}
.cls-pill{font-size:12px;font-weight:700;padding:2px 9px;border-radius:12px;white-space:nowrap;flex-shrink:0}
.cls-wait{background:#92400e;color:#fef3c7}.cls-run{background:#14532d;color:#dcfce7}
.cls-err{background:#991b1b;color:#fecaca}.cls-rest{background:#334155;color:#e2e8f0}
.cls-chat{background:#1e3a5f;color:#bfdbfe}
.vp-date-pill{align-self:center;background:rgba(30,41,59,.92);
 border:1px solid rgba(120,160,220,.25);border-radius:16px;padding:3px 14px;
 font-size:12px;color:#e2e8f0;margin:6px 0 2px}
.vp-sender{display:inline-block;background:rgba(74,168,255,.18);color:#bfdbfe;
 border-radius:10px;padding:1px 8px;font-size:11px;font-weight:700;margin-right:6px}
.vp-time{display:none;font-size:11px;color:#a8b8cc;margin-left:6px}
.vp-live-msg:hover .vp-time{display:inline}
.vp-linkcard{display:inline-block;background:rgba(255,255,255,.08);
 border:1px solid rgba(120,160,220,.3);border-radius:14px;padding:4px 10px;
 color:#7cc4ff;text-decoration:none;font-size:12px;word-break:break-all}
.vp-linkcard:hover{text-decoration:underline}
body.simple-view #simple-view{display:grid;grid-template-columns:300px 300px 1fr;gap:12px;align-items:start}
#simple-view .sv-head{grid-column:1/-1}
@media(max-width:1100px){body.simple-view #simple-view{grid-template-columns:300px 1fr}}
@media(max-width:700px){body.simple-view #simple-view{grid-template-columns:1fr}}
.pbar{background:#0b1220;border-radius:8px;height:10px;overflow:hidden;margin-top:6px}
.pfill{height:100%;border-radius:8px;transition:width .6s}
.pmeta{font-size:12px;color:#a8b8cc;margin-top:4px}
.stripes{background:repeating-linear-gradient(45deg,#b45309,#b45309 8px,#d97706 8px,#d97706 16px);
 animation:slide 1s linear infinite;background-size:28px 28px}
@keyframes slide{to{background-position:28px 0}}
#chat-bubble{position:fixed;right:14px;bottom:14px;z-index:30;width:52px;height:52px;
 border-radius:50%;background:linear-gradient(135deg,#4aa8ff,#b07bff);border:none;
 font-size:24px;cursor:pointer;box-shadow:0 4px 16px rgba(74,168,255,.5)}
#chat-panel{position:fixed;right:14px;bottom:76px;z-index:30;width:320px;max-height:440px;
 display:none;flex-direction:column;background:rgba(13,20,32,.95);border:1px solid #2a3d5c;
 border-radius:14px;overflow:hidden;box-shadow:0 8px 30px rgba(0,0,0,.6)}
#chat-panel.open{display:flex}
#chat-msgs{flex:1;overflow-y:auto;padding:10px;display:flex;flex-direction:column;gap:8px;min-height:200px}
.msg{max-width:85%;padding:8px 10px;border-radius:12px;font-size:12px;line-height:1.45}
.msg.user{align-self:flex-end;background:#1d4ed8;color:#fff}
.msg.ai{align-self:flex-start;background:#1e293b;border:1px solid #2a3d5c}
.msg .who{font-size:10px;color:#7c8da3;margin-bottom:2px}
#chat-form{display:flex;gap:6px;padding:8px;border-top:1px solid #223148}
#chat-form input{flex:1;background:#0b1220;border:1px solid #2a3d5c;border-radius:8px;
 color:#fff;padding:8px;font-size:12px}
#chat-form button{background:#4aa8ff;border:none;border-radius:8px;color:#fff;
 padding:0 16px;cursor:pointer;font-size:16px}
#chat-agent{width:100%;background:#0b1220;color:#fff;border:none;border-bottom:1px solid #223148;
 padding:8px;font-size:12px}
#settings-btn{position:fixed;top:76px;right:248px;z-index:30;width:40px;height:40px;
 border-radius:50%;background:rgba(13,20,32,.9);border:1px solid #2a3d5c;color:#fff;
 font-size:18px;cursor:pointer}
#settings-panel{position:fixed;top:124px;right:248px;z-index:30;width:250px;display:none;
 background:rgba(13,20,32,.95);border:1px solid #2a3d5c;border-radius:12px;padding:12px}
#settings-panel.open{display:block}
.set-row{display:flex;justify-content:space-between;align-items:center;
 padding:8px 0;border-bottom:1px solid #1a2636;font-size:12px}
.set-row:last-child{border:none}
.set-row select{background:#0b1220;color:#fff;border:1px solid #2a3d5c;border-radius:6px;padding:4px}
.switch{position:relative;width:38px;height:20px;background:#334155;border-radius:12px;cursor:pointer;flex-shrink:0}
.switch.on{background:#22c55e}
.switch::after{content:'';position:absolute;top:2px;left:2px;width:16px;height:16px;
 border-radius:50%;background:#fff;transition:left .2s}
.switch.on::after{left:20px}
#toast{position:fixed;bottom:80px;left:50%;transform:translateX(-50%);z-index:40;
 display:none;background:rgba(13,20,32,.95);border:1px solid #4aa8ff;border-radius:10px;
 padding:10px 18px;font-size:13px}
#loading{position:fixed;inset:0;z-index:50;display:flex;align-items:center;justify-content:center;
 background:#070b12;font-size:15px;color:#7c8da3}
@media(max-width:900px){
 #hud-left,#hud-right{display:none}
 #ec-steps{grid-template-columns:repeat(2,1fr)}
 #hud-bottom{right:10px}
 #settings-btn{right:14px}#settings-panel{right:14px}
}
/* ---- left icon bar (3D) ---- */
.iconbar3d{
 position:fixed;left:0;top:0;bottom:0;width:56px;z-index:50;
 background:rgba(10,15,25,.95);border-right:1px solid #223148;
 display:flex;flex-direction:column;align-items:center;padding:12px 0;gap:8px;
}
.iconbar3d .ib-logo{font-size:1.3rem;margin-bottom:8px}
.iconbar3d button{
 width:40px;height:40px;border-radius:12px;border:1px solid transparent;
 background:transparent;font-size:1.25rem;cursor:pointer;
 display:flex;align-items:center;justify-content:center;color:#e8eef5;
}
.iconbar3d button:hover{background:#1a2436;border-color:#223148}
.iconbar3d button.active{background:#2563eb;border-color:#2563eb}
.iconbar3d .ib-sep{width:24px;border-top:1px solid #223148;margin:4px 0}
.iconbar3d .ib-spacer{flex:1}
#scene{left:56px !important}
#hud-top{left:66px !important}
#hud-bottom{left:66px !important}
/* ---- modal (3D) ---- */
.modal3d-overlay{
 position:fixed;inset:0;z-index:100;background:rgba(0,0,0,.65);
 display:none;align-items:center;justify-content:center;padding:16px;
}
.modal3d-overlay.open{display:flex}
.modal3d{
 background:#131a28;border:1px solid #223148;border-radius:16px;
 max-width:640px;width:100%;max-height:85vh;overflow-y:auto;
 padding:20px;position:relative;color:#e8eef5;
 box-shadow:0 20px 60px rgba(0,0,0,.5);
}
.modal3d .card{background:#1a2436;border:1px solid #223148;border-radius:12px;padding:14px;margin-bottom:0}
.modal3d h2{margin:0 0 12px;font-size:1.1rem}
.modal3d h3{margin:0 0 8px;font-size:.95rem}
.modal3d .muted{color:#9fb2c8}
.modal3d .btn{
 background:#2563eb;color:#fff;border:none;border-radius:8px;
 padding:10px 18px;font-size:.9rem;cursor:pointer;margin-top:8px;
}
.modal3d .btn:disabled{opacity:.6}
.modal3d-close{
 position:absolute;top:12px;right:12px;width:32px;height:32px;border-radius:99px;
 border:1px solid #223148;background:#1a2436;color:#e8eef5;
 font-size:1rem;cursor:pointer;display:flex;align-items:center;justify-content:center;
}

/* ---- v2.9 Don trang 3D cho de nhin: 3D lam trong tam, panel phu thu gon mac dinh (chi hien thi) ---- */
#hud-left,#hud-right{display:none}
body.show-left #hud-left{display:block}
body.show-right #hud-right{display:block}
#hud-left-toggle{top:76px;left:66px}
#hud-right-toggle{top:76px;right:10px}
.hud-toggle-btn{pointer-events:auto;background:rgba(13,20,32,.92);border:1px solid rgba(120,160,220,.4);
 color:#e8eef5;border-radius:16px;padding:7px 14px;font-size:12px;cursor:pointer;
 box-shadow:0 4px 18px rgba(0,0,0,.45);white-space:nowrap}
.hud-toggle-btn.on{background:rgba(74,168,255,.28);border-color:#4aa8ff;font-weight:700}
#settings-btn{right:14px}
#settings-panel{right:14px}
body.show-right #settings-btn{right:248px}
body.show-right #settings-panel{right:248px}
.vp-collapse{border:1px solid #223148;border-radius:10px;padding:0 10px;margin-top:8px;background:rgba(11,18,32,.5)}
.vp-collapse>summary{cursor:pointer;list-style:none;padding:8px 0;font-size:12px;font-weight:700;
 color:#e8eef5;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.vp-collapse>summary::-webkit-details-marker{display:none}
.vp-collapse>summary::before{content:'▶';font-size:10px;color:#7cc4ff;flex-shrink:0}
.vp-collapse[open]>summary::before{content:'▼'}
.vp-collapse .vp-open-hint{margin-left:auto;font-weight:400;color:#7c8da3;font-size:11px}
.vp-collapse .vp-strip{margin:8px 0}
#vp-live-box{max-height:220px}
#vp-live-box .vp-live-msg > span:not(.vp-time){display:-webkit-box;-webkit-line-clamp:3;
 -webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
</style></head>
<body>
<div class="iconbar3d">
 <div class="ib-logo" title="Tom AI Orchestra">🎼</div>
 <button id="ib3d-vp" title="Chuyển VP 2D / 3D" class="active">🏢</button>
 <div class="ib-sep"></div>
 <button id="ib3d-sync" title="Đồng bộ skills + quy tắc">🔄</button>
 <div class="ib-spacer"></div>
 <button id="ib3d-settings" title="Cài đặt">⚙️</button>
</div>
<div class="modal3d-overlay" id="ib3d-modal-ov">
 <div class="modal3d">
  <button class="modal3d-close" id="ib3d-modal-x" title="Đóng">✕</button>
  <div id="ib3d-modal-body"><p class="muted">⏳ Đang tải...</p></div>
 </div>
</div>
<script>
(function(){
  var ov=document.getElementById('ib3d-modal-ov'),bd=document.getElementById('ib3d-modal-body');
  function openM(h){bd.innerHTML=h;ov.classList.add('open');}
  function closeM(){ov.classList.remove('open');}
  document.getElementById('ib3d-modal-x').onclick=closeM;
  ov.onclick=function(e){if(e.target===ov)closeM();};
  document.addEventListener('keydown',function(e){if(e.key==='Escape')closeM();});
  document.getElementById('ib3d-vp').onclick=function(){location.href='/ai-office';};
  document.getElementById('ib3d-sync').onclick=async function(){
    openM('<h2>🔄 Đồng bộ</h2><p class="muted">⏳ Đang tải...</p>');
    try{
      var r=await fetch('/api/popup/sync');var d=await r.json();
      openM('<h2>🔄 Đồng bộ</h2>'+d.html);wireSync();
    }catch(e){openM('<h2>🔄 Đồng bộ</h2><p>❌ Lỗi: '+e+'</p>');}
  };
  function wireSync(){
    var b1=document.getElementById('ib-sync-skills');
    if(b1)b1.onclick=async function(){
      b1.textContent='⏳ Đang đồng bộ skills...';b1.disabled=true;
      try{var r=await fetch('/api/kb/reload');var d=await r.json();
        b1.textContent=d.ok?('✅ Đã đồng bộ '+d.skills+' skills'):'❌ Lỗi';
      }catch(e){b1.textContent='❌ Lỗi';}b1.disabled=false;
    };
    var b2=document.getElementById('ib-sync-rules');
    if(b2)b2.onclick=async function(){
      b2.textContent='⏳ Đang đồng bộ quy tắc...';b2.disabled=true;
      try{var r=await fetch('/api/kb/reload');var d=await r.json();
        b2.textContent=d.ok?'✅ Quy tắc đã đồng bộ':'❌ Lỗi';
      }catch(e){b2.textContent='❌ Lỗi';}b2.disabled=false;
    };
  }
  document.getElementById('ib3d-settings').onclick=async function(){
    openM('<h2>⚙️ Cài đặt</h2><p class="muted">⏳ Đang tải...</p>');
    try{
      var r=await fetch('/api/popup/settings');var d=await r.json();
      openM('<h2>⚙️ Cài đặt</h2>'+d.html);
    }catch(e){openM('<h2>⚙️ Cài đặt</h2><p>❌ Lỗi: '+e+'</p>');}
  };
})();
</script>
<div id="loading">🌐 Đang dựng văn phòng 3D…</div>
<div id="scene"></div>

<div class="hud" id="hud-top"><div class="card" style="display:flex;gap:14px;align-items:center;flex-wrap:wrap;width:100%">
 <span class="title">🌐 Văn Phòng AI 3D</span>
 <span style="font-size:12px;color:#7cc4ff;border:1px solid #2a3d5c;border-radius:12px;padding:2px 10px">v__VERSION__ · __VERSION_DATE__</span>
 <button id="vp-view-toggle" title="Chuyển sang dạng danh sách khô, không cần bản đồ 3D">📋 Đơn giản</button>
 <span class="stats" id="company-stats"></span>
 <span id="ec-strip"></span>
</div></div>

<div class="hud" id="hud-left-toggle"><button class="hud-toggle-btn" id="btn-left-toggle" title="Hiện/ẩn panel nhân sự">🤖 Nhân sự · Token</button></div>
<div class="hud" id="hud-right-toggle"><button class="hud-toggle-btn" id="btn-right-toggle" title="Hiện/ẩn panel tài chính">💰 Tài chính · Ổ cứng</button></div>
<div class="hud" id="hud-left"><div class="card">
 <div class="sec-t">🤖 Nhân sự · Token</div>
 <input id="agent-search" class="sv-search" placeholder="🔍 Tìm agent…" autocomplete="off">
 <div class="sv-tabs" id="agent-tabs"><button data-f="all" class="on">Tất cả</button><button data-f="running">Đang chạy</button><button data-f="waiting">Chờ việc</button><button data-f="resting">Nghỉ</button><button data-f="error">Lỗi</button></div>
 <div id="agent-list"></div>
</div></div>

<div class="hud" id="hud-right"><div class="card">
 <div class="sec-t">💰 Tài chính</div><div id="finance-box"></div>
 <div class="sec-t">💽 Ổ cứng</div><div id="drive-box"></div>
</div></div>

<div class="hud" id="hud-bottom"><div class="card">
 <div class="sec-t">💾 Tác vụ chuyển dữ liệu ổ cứng <span style="opacity:.6;font-weight:400">(Kỹ thuật · Dữ liệu)</span></div>
 <div id="ec-steps"></div>
 <details class="vp-collapse" id="vp-live-details">
 <summary><span>🔴 VP Sống · Trực tiếp</span> <span style="opacity:.6;font-weight:400">(vòng chat tự động mỗi giờ)</span> <span id="vp-live-status" style="text-transform:none;letter-spacing:0;color:#7cc4ff"></span> <span id="vp-unread" class="unread-badge" style="display:none" title="Tin mới — bấm vào khung chat để đánh dấu đã đọc"></span> <span class="vp-open-hint">bấm để mở</span></summary>
 <div id="vp-status-strip" class="vp-strip"></div>
 <div id="vp-live-box"></div>
 </details>
 <details class="vp-collapse" id="vp-flow-details">
 <summary><span>🧭 Luồng xử lý vòng chat</span> <span style="opacity:.6;font-weight:400">(theo thứ tự bước — chỉ hiển thị, không đổi cách vòng chạy)</span> <span class="vp-open-hint">bấm để mở</span></summary>
 <div id="vp-flow"></div>
 </details>
</div></div>

<div id="simple-view">
 <div class="sv-card sv-head"><h2>📋 Văn phòng — dạng Đơn giản <button id="sv-back" style="float:right;background:#2563eb;color:#fff;border:none;border-radius:8px;padding:6px 12px;cursor:pointer">🌐 Về bản đồ 3D</button></h2>
 <p class="vp-empty">Bố cục 3 cột + thanh icon dọc bên trái (icon rail sẵn có của trang). Lựa chọn được lưu trên trình duyệt này; mặc định vẫn là bản đồ 3D. Bấm vào một dòng để chọn (nền sáng + bo tròn); nút hành động chỉ hiện khi hover vào dòng.</p></div>
 <div class="sv-card"><h2>🤖 Nhân sự</h2>
 <input id="sv-agent-search" class="sv-search" placeholder="🔍 Tìm agent…" autocomplete="off">
 <div class="sv-tabs" id="sv-agent-tabs"><button data-f="all" class="on">Tất cả</button><button data-f="running">Đang chạy</button><button data-f="waiting">Chờ việc</button><button data-f="resting">Nghỉ</button><button data-f="error">Lỗi</button></div>
 <div id="sv-agents"></div></div>
 <div class="sv-card"><h2>⏳ Việc chờ duyệt</h2>
 <input id="sv-task-search" class="sv-search" placeholder="🔍 Tìm việc…" autocomplete="off">
 <div class="sv-tabs" id="sv-task-tabs"><button data-f="all" class="on">Tất cả</button><button data-f="wait">Chờ duyệt</button></div>
 <div id="sv-tasks"></div></div>
 <div class="sv-card"><h2>🔴 VP Sống · Trực tiếp <span id="sv-unread" class="unread-badge" style="display:none"></span></h2><div id="sv-strip" class="vp-strip"></div><div id="sv-live"></div><div id="sv-flow"></div>
 <h2 style="margin-top:12px">💰 Tài chính · 💽 Ổ cứng</h2><div id="sv-fin"></div></div>
</div>

<button id="chat-bubble" title="Chat với AI">💬</button>
<div id="chat-panel">
 <select id="chat-agent">
  <option value="auto">🤖 Tự chọn AI</option><option value="gemini">🦊 Gemini</option>
  <option value="muse">🦉 Muse</option><option value="deepseek">🦈 DeepSeek</option>
  <option value="lmstudio">🐸 LM Studio</option>
 </select>
 <div id="chat-msgs"></div>
 <form id="chat-form"><input id="chat-input" placeholder="Nhắn cho AI…" autocomplete="off">
 <button type="submit" title="Gửi" aria-label="Gửi tin nhắn">➤</button></form>
</div>

<button id="settings-btn" title="Cài đặt">⚙️</button>
<div id="settings-panel"><div class="card" style="border:none;background:none;padding:0;box-shadow:none">
 <div class="sec-t">⚙️ Cài đặt hiển thị</div>
 <div class="set-row"><span>🚶 Tự động di chuyển</span><div class="switch" id="sw-move"></div></div>
 <div class="set-row"><span>🎥 Góc nhìn camera</span>
  <select id="sel-cam"><option value="iso">Isometric</option>
  <option value="top">Top-down</option><option value="free">Tự do xoay</option></select></div>
 <div class="set-row"><span>⏱️ Refresh dữ liệu</span>
  <select id="sel-refresh"><option value="15">15 giây</option>
  <option value="30" selected>30 giây</option><option value="60">60 giây</option></select></div>
 <div class="set-row"><span>👁️ Hiện tên nhân vật</span><div class="switch" id="sw-label"></div></div>
 <div class="set-row"><span>🔔 Báo khi đổi trạng thái</span><div class="switch" id="sw-notify"></div></div>
</div></div>

<div id="toast"></div>
<script type="importmap">
{"imports":{
 "three":"https://cdn.jsdelivr.net/npm/three@0.186.0/build/three.module.js",
 "three/addons/":"https://cdn.jsdelivr.net/npm/three@0.186.0/examples/jsm/"
}}
</script>
<script type="module">
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';

const CSRF = "__CSRF__";
let STATE = __STATE__;
const $ = id => document.getElementById(id);

// ---------- settings ----------
const DEF_SET = {autoMove:true, camera:'iso', refresh:30, labels:true, notify:false};
let SET = Object.assign({}, DEF_SET);
try{ Object.assign(SET, JSON.parse(localStorage.getItem('vp3d-set')||'{}')); }catch(e){}
function saveSet(){ localStorage.setItem('vp3d-set', JSON.stringify(SET)); }

// ---------- zones ----------
const ZONES = [
 {id:'ketoan',   x:-16,  z:-10, c:0x4aa8ff, icon:'\U0001F3E6', label:'Kế Toán'},
 {id:'kythuat',  x:-5.5, z:-10, c:0x3ddc84, icon:'\U0001F4BE', label:'Kỹ Thuật'},
 {id:'hauky',    x:5.5,  z:-10, c:0xff9f43, icon:'\U0001F3AC', label:'Hậu Kỳ'},
 {id:'app',      x:16,   z:-10, c:0xb07bff, icon:'\U0001F4F1', label:'App'},
 {id:'marketing',x:-16,  z:10,  c:0xf472b6, icon:'\U0001F4E3', label:'Marketing'},
 {id:'dulieu',   x:-5.5, z:10,  c:0x39c5ff, icon:'\U0001F5C4', label:'Dữ Liệu'},
 {id:'dieuhanh', x:5.5,  z:10,  c:0xe8b04a, icon:'\U0001F4C5', label:'Điều Hành'},
];
const zoneOf = id => ZONES.find(z=>z.id===id) || ZONES[0];
const ST_COLOR = {busy:0xf59e0b, ready:0x22c55e, stale:0x64748b, error:0xef4444};
const ST_TXT = {busy:['\U0001F504 ĐANG CHẠY','st-busy'], ready:['\U0001F7E2 SẴN SÀNG','st-ready'],
                stale:['\U0001F7E1 CHƯA KÍCH HOẠT','st-stale'], error:['\U0001F534 LỖI','st-error']};

// ---------- three setup ----------
const container = $('scene');
const renderer = new THREE.WebGLRenderer({antialias:true});
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;
container.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0a1424);
scene.fog = new THREE.Fog(0x0a1424, 80, 180);
const camera = new THREE.PerspectiveCamera(45, innerWidth/innerHeight, 0.1, 400);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true; controls.dampingFactor = 0.08;
controls.maxPolarAngle = 1.35; controls.minDistance = 8; controls.maxDistance = 120;
controls.enabled = false;

scene.add(new THREE.HemisphereLight(0x8fb8ff, 0x0a0f18, 1.0));
const sun = new THREE.DirectionalLight(0xfff2dd, 1.6);
sun.position.set(24, 34, 14); sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.camera.left=-30; sun.shadow.camera.right=30;
sun.shadow.camera.top=24; sun.shadow.camera.bottom=-24;
scene.add(sun);
ZONES.forEach(z=>{
  const pt = new THREE.PointLight(z.c, 12, 16, 1.8);
  pt.position.set(z.x, 4, z.z); scene.add(pt);
});

// ---------- floor ----------
const floor = new THREE.Mesh(
  new THREE.PlaneGeometry(48, 34),
  new THREE.MeshStandardMaterial({color:0x14233c, roughness:.9}));
floor.rotation.x = -Math.PI/2; floor.receiveShadow = true; scene.add(floor);
const grid = new THREE.GridHelper(48, 24, 0x2a3f66, 0x1d2f4a);
grid.position.y = 0.01; scene.add(grid);

// ---------- label sprite ----------
function makeLabel(text, scale=1){
  const cv = document.createElement('canvas'); cv.width=512; cv.height=112;
  const g = cv.getContext('2d');
  g.fillStyle='rgba(8,13,22,0.78)';
  g.beginPath(); g.roundRect(6,6,500,100,24); g.fill();
  g.strokeStyle='rgba(120,160,220,0.5)'; g.lineWidth=3; g.stroke();
  // tự thu nhỏ font nếu text dài
  let fs = 52;
  g.font='bold '+fs+'px "Segoe UI",sans-serif';
  while(g.measureText(text).width > 460 && fs > 24){
    fs -= 4; g.font='bold '+fs+'px "Segoe UI",sans-serif';
  }
  g.textAlign='center'; g.textBaseline='middle';
  g.fillStyle='#fff'; g.fillText(text, 256, 58);
  const tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace;
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({map:tex, transparent:true, depthTest:false}));
  sp.scale.set(4.6*scale, 1.0*scale, 1); sp.renderOrder = 5;
  return sp;
}

// ---------- zones visuals ----------
ZONES.forEach(z=>{
  const pad = new THREE.Mesh(
    new THREE.PlaneGeometry(9.6, 7.4),
    new THREE.MeshStandardMaterial({color:z.c, transparent:true, opacity:0.10, roughness:1}));
  pad.rotation.x = -Math.PI/2; pad.position.set(z.x, 0.02, z.z); scene.add(pad);
  const edge = new THREE.LineLoop(
    new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(-4.8,0,-3.7), new THREE.Vector3(4.8,0,-3.7),
      new THREE.Vector3(4.8,0,3.7), new THREE.Vector3(-4.8,0,3.7)]),
    new THREE.LineBasicMaterial({color:z.c, transparent:true, opacity:.7}));
  edge.position.set(z.x, 0.04, z.z); scene.add(edge);
  const desk = new THREE.Mesh(
    new THREE.BoxGeometry(3.4, 0.9, 1.6),
    new THREE.MeshStandardMaterial({color:0x1b2740, roughness:.6}));
  desk.position.set(z.x, 0.45, z.z-1.8); desk.castShadow = true; scene.add(desk);
  const screen = new THREE.Mesh(
    new THREE.BoxGeometry(2.2, 1.1, 0.08),
    new THREE.MeshStandardMaterial({color:0x0a0f18, emissive:z.c, emissiveIntensity:.55}));
  screen.position.set(z.x, 1.5, z.z-2.2); scene.add(screen);
  z._screen = screen;
  const lab = makeLabel(z.icon+' '+z.label, 1.6);
  lab.position.set(z.x, 4.6, z.z); scene.add(lab);
});

// ---------- agents (kiểu cute tròn như video Phòng kế toán AI) ----------
const agents = {};   // key -> {data, group, ring, label, faceGrp, target, speed, wanderT, bob}
function makeFace(mood){
  // mặt cute: 2 mắt + miệng, vẽ trên canvas → plane dán trước mặt
  const cv = document.createElement('canvas'); cv.width=128; cv.height=128;
  const g = cv.getContext('2d');
  g.clearRect(0,0,128,128);
  // mắt
  const eyeY = 52, eyeDX = 26;
  [[64-eyeDX,eyeY],[64+eyeDX,eyeY]].forEach(([x,y])=>{
    g.fillStyle='#fff'; g.beginPath(); g.arc(x,y,17,0,7); g.fill();
    g.fillStyle='#1a1a2e'; g.beginPath(); g.arc(x,y+2,8,0,7); g.fill();
    g.fillStyle='#fff'; g.beginPath(); g.arc(x-3,y-1,3,0,7); g.fill();
  });
  // miệng theo mood
  g.strokeStyle='#1a1a2e'; g.lineWidth=5; g.lineCap='round';
  g.beginPath();
  if(mood==='busy'){ g.arc(64,78,16,0.15*Math.PI,0.85*Math.PI); }        // cười tươi
  else if(mood==='error'){ g.arc(64,100,14,1.15*Math.PI,1.85*Math.PI); } // mếu
  else { g.arc(64,74,15,0.2*Math.PI,0.8*Math.PI); }                       // cười nhẹ
  g.stroke();
  // má hồng
  g.fillStyle='rgba(255,120,150,0.45)';
  g.beginPath(); g.arc(64-eyeDX-14,78,9,0,7); g.fill();
  g.beginPath(); g.arc(64+eyeDX+14,78,9,0,7); g.fill();
  const tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace;
  return new THREE.SpriteMaterial({map:tex, transparent:true, depthTest:false});
}
function buildAgent(a){
  const g = new THREE.Group();
  const col = new THREE.Color(a.color);
  const isMain = !!a.is_main;
  const R = isMain ? 0.85 : 0.62;   // Muse to hơn 1 chút
  // thân tròn cute (blob)
  const body = new THREE.Mesh(
    new THREE.SphereGeometry(R, 24, 18),
    new THREE.MeshStandardMaterial({color:col, roughness:.4, metalness:.1}));
  body.position.y = R + 0.12; body.castShadow = true; g.add(body);
  // mặt dán phía trước
  const faceMat = makeFace(a.status);
  const face = new THREE.Sprite(faceMat);
  face.scale.set(R*1.5, R*1.5, 1);
  face.position.set(0, R + 0.22, R*0.82); face.renderOrder = 6;
  g.add(face);
  // chân nhỏ 2 bên
  const footG = new THREE.SphereGeometry(R*0.22, 12, 10);
  const footM = new THREE.MeshStandardMaterial({color:col.clone().multiplyScalar(0.7), roughness:.5});
  [-1,1].forEach(s=>{
    const f = new THREE.Mesh(footG, footM);
    f.position.set(s*R*0.42, R*0.2, 0); f.castShadow = true; g.add(f);
  });
  // vòng trạng thái dưới chân
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(R+0.12, R+0.3, 40),
    new THREE.MeshBasicMaterial({color:ST_COLOR[a.status]||0x22c55e,
      transparent:true, opacity:.9, side:THREE.DoubleSide}));
  ring.rotation.x = -Math.PI/2; ring.position.y = 0.04; g.add(ring);
  // label tên + token/cost
  const t30 = (a.tokens && a.tokens[30]) || {};
  const tokTxt = t30.tokens ? ' 🎟️'+fmtT(t30.tokens) : '';
  const label = makeLabel(a.char+' '+a.name+tokTxt, isMain?1.2:1.05);
  label.position.y = R*2 + 1.35; g.add(label);
  // vị trí: Muse ở giữa, các con khác ở phòng ban
  let px, pz;
  if(isMain){ px = 0; pz = 0; }
  else { const z = zoneOf(a.dept); px = z.x+(Math.random()*4-2); pz = z.z+(Math.random()*3-1); }
  g.position.set(px, 0, pz);
  scene.add(g);
  agents[a.key] = {data:a, group:g, ring:ring, label:label, face:face, faceMat:faceMat,
    target:g.position.clone(), speed:3.4, wanderT:performance.now()/1000 + Math.random()*5,
    bob:Math.random()*6, prevStatus:a.status, bubbleT:0, bubble:null};
}
STATE.agents.forEach(buildAgent);

// ---------- robot nhỏ đi hành lang (như video) ----------
const robot = (()=>{
  const g = new THREE.Group();
  const mBody = new THREE.MeshStandardMaterial({color:0x94a3b8, roughness:.35, metalness:.6});
  const mDark = new THREE.MeshStandardMaterial({color:0x334155, roughness:.5, metalness:.4});
  const body = new THREE.Mesh(new THREE.BoxGeometry(0.55,0.7,0.45), mBody);
  body.position.y = 0.55; body.castShadow = true; g.add(body);
  const head = new THREE.Mesh(new THREE.BoxGeometry(0.5,0.4,0.4), mDark);
  head.position.y = 1.1; head.castShadow = true; g.add(head);
  // mắt robot (đèn xanh)
  const eyeM = new THREE.MeshBasicMaterial({color:0x22d3ee});
  [-1,1].forEach(s=>{
    const e = new THREE.Mesh(new THREE.SphereGeometry(0.07,10,8), eyeM);
    e.position.set(s*0.13, 1.12, 0.21); g.add(e);
  });
  // ăng-ten
  const ant = new THREE.Mesh(new THREE.CylinderGeometry(0.02,0.02,0.3,8), mDark);
  ant.position.y = 1.45; g.add(ant);
  const tip = new THREE.Mesh(new THREE.SphereGeometry(0.05,8,8),
    new THREE.MeshBasicMaterial({color:0xef4444}));
  tip.position.y = 1.62; g.add(tip);
  // bánh xe
  const wheelG = new THREE.CylinderGeometry(0.14,0.14,0.1,14);
  [-1,1].forEach(s=>{
    const w = new THREE.Mesh(wheelG, mDark);
    w.rotation.z = Math.PI/2; w.position.set(s*0.3, 0.14, 0); g.add(w);
  });
  const lab = makeLabel('🤖 Robot', 0.8); lab.position.y = 2.0; g.add(lab);
  g.position.set(0, 0, 0); scene.add(g);
  return {group:g, target:new THREE.Vector3(5,0,5), speed:2.6,
          wanderT:performance.now()/1000+2, tip:tip};
})();

// ---------- bong bóng thoại (như video) ----------
const SPEECHES = ['Số liệu khớp số rồi!','Giỏi lắm!','Xong việc! ✅','Đang chạy… 🔄',
  'Để em lo! 💪','Báo cáo xong! 📝','Tiết kiệm được $! 💵','Ổn hết anh ơi! 👍'];
function showBubble(ag){
  if(ag.bubble){ ag.group.remove(ag.bubble); ag.bubble = null; }
  const txt = SPEECHES[Math.floor(Math.random()*SPEECHES.length)];
  const cv = document.createElement('canvas'); cv.width=512; cv.height=160;
  const c = cv.getContext('2d');
  c.fillStyle='rgba(255,255,255,0.95)';
  c.beginPath(); c.roundRect(10,10,492,110,28); c.fill();
  // đuôi bong bóng
  c.beginPath(); c.moveTo(230,118); c.lineTo(256,150); c.lineTo(282,118); c.fill();
  c.font='bold 44px "Segoe UI",sans-serif'; c.textAlign='center'; c.textBaseline='middle';
  c.fillStyle='#1a1a2e'; c.fillText(txt, 256, 66);
  const tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace;
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({map:tex, transparent:true, depthTest:false}));
  sp.scale.set(4.2, 1.3, 1); sp.renderOrder = 7;
  const R = ag.data.is_main ? 0.85 : 0.62;
  sp.position.y = R*2 + 2.6;
  ag.group.add(sp); ag.bubble = sp;
  ag.bubbleT = performance.now()/1000 + 3.5;  // hiện 3.5s
}

function deskFront(zoneId){
  const z = zoneOf(zoneId);
  return new THREE.Vector3(z.x, 0, z.z + 0.6);
}
function ecRunning(){
  return STATE.ec.some(s=>s.status==='run');
}
function assignTargets(){
  STATE.agents.forEach(a=>{
    const ag = agents[a.key]; if(!ag) return;
    if(a.status==='busy'){
      ag.target.copy(deskFront(a.dept));
    }
  });
}
assignTargets();

// ---------- camera ----------
function applyCamera(){
  const m = SET.camera;
  controls.enabled = (m==='free');
  if(m==='top'){ camera.position.set(0, 54, 0.02); }
  else if(m==='iso'){ camera.position.set(30, 27, 30); }
  camera.lookAt(0, 0, 0);
  if(m!=='free'){ controls.target.set(0,0,0); }
}
applyCamera();

// ---------- panels ----------
function fmtT(t){ return t>=1e6 ? (t/1e6).toFixed(1)+'M' : t>=1e3 ? (t/1e3).toFixed(1)+'K' : ''+Math.round(t||0); }
function renderPanels(){
  const S = STATE;
  // company stats
  const revM = S.finance['Doanh thu tháng này'] || '?';
  $('company-stats').innerHTML =
    '💰 Tháng này: <b>'+revM+'</b> · ⭐ Uy tín: <b>'+S.company.reputation+'</b> · '+
    '✅ Tỷ lệ OK: <b>'+S.company.avg_ok+'%</b> · 🎟️ Token 30d: <b>'+fmtT(
      S.agents.reduce((s,a)=>s+((a.tokens[30]||{}).tokens||0),0))+'</b>';
  // ec strip
  $('ec-strip').innerHTML = S.ec.map(s=>{
    const cls = s.status==='done'?'ec-done':s.status==='run'?'ec-run':'ec-wait';
    const txt = s.status==='done'?'✅ '+s.id : s.status==='run'?'🔄 '+s.id+'…':'⏳ '+s.id;
    return '<span class="ec-mini '+cls+'">'+txt+'</span>';
  }).join('');
  // agents (vien mau trang thai + loi thoai ngan duoi ten; chi hien thi)
  $('agent-list').innerHTML = S.agents.length ? S.agents.map(a=>{
    const t = ST_TXT[a.status]||ST_TXT.stale;
    const t1=a.tokens[1]||{}, t7=a.tokens[7]||{}, t30=a.tokens[30]||{};
    const cost30 = t30.cost ? ' · 💵$'+t30.cost : '';
    const refill = a.key==='muse_main' ? ' <span class="st st-ready">🔄 refill tuần</span>' : '';
    const prepaid = a.prepaid ? '<br><span class="tok">💰 Prepaid (không refill)</span>' : '';
    const br = 'br-'+(a.display_status||'resting');
    const speech = a.speech ? '<br><span class="agent-speech">💬 “'+a.speech+'”</span>' : '';
    // (a) Chi hien khi task-bus co gan agent that (pending_count>0); khong co
    // thi khong hien so le tren the — tong van o dai trang thai + loi thoai.
    const pendTxt = (a.pending_count||0) > 0 ? '<br><span class="tok">⏳ '+(a.pending_count)+' việc chờ của agent này</span>' : '';
    // Nhan phan loai canh ten — chi phan anh trang thai DA CO (khong bia trang thai moi):
    // muse/muse_main + co viec pending = "Cho duyet" (giong muc "cho sep duyet" o trang 2D);
    // agent noi o vong moi nhat = "Dang ban"; con lai theo display_status.
    const _pend = (S.vp||{}).vp_pending_count||0, _spk = (S.vp||{}).vp_speakers||[];
    let _cls = ['Chờ việc','cls-wait'];
    if(a.display_status==='error') _cls = ['Lỗi','cls-err'];
    else if(a.display_status==='running') _cls = ['Đang chạy','cls-run'];
    else if((a.key==='muse'||a.key==='muse_main')&&_pend>0) _cls = ['Chờ duyệt','cls-wait'];
    else if(_spk.indexOf(a.name)>=0) _cls = ['Đang bàn','cls-chat'];
    else if(a.display_status==='resting') _cls = ['Nghỉ','cls-rest'];
    return '<div class="agent-row '+br+'" data-name="'+a.name.toLowerCase()+'" data-status="'+(a.display_status||'resting')+'"><span class="agent-dot" style="background:'+a.color+'"></span>'+
     '<span><span class="an">'+a.char+' '+a.name+'</span> <span class="cls-pill '+_cls[1]+'">'+_cls[0]+'</span>'+refill+speech+pendTxt+'<br><span class="tok">🎟️ 1d:'+fmtT(t1.tokens)+
     ' · 7d:'+fmtT(t7.tokens)+' · 30d:'+fmtT(t30.tokens)+cost30+'</span>'+prepaid+'</span>'+
     '<button class="row-action" title="Xem lời thoại của agent này">💬</button>'+
     '<span class="st '+t[1]+'">'+t[0]+'</span></div>';
  }).join('')
  : '<div class="vp-empty">Chưa có agent nào.<br><b>Làm gì tiếp:</b> kiểm tra cấu hình provider rồi tải lại trang — danh sách agent lấy từ router/provider, không nhập tay.</div>';
  // chon dong (nen sang + bo tron, khong vien cung) + hover-action (toast loi thoai)
  document.querySelectorAll('#agent-list .agent-row').forEach(function(row, idx){
    row.onclick = function(){
      document.querySelectorAll('#agent-list .agent-row').forEach(function(r){ r.classList.remove('selected'); });
      row.classList.add('selected');
    };
    var btn = row.querySelector('.row-action');
    if(btn) btn.onclick = function(e){
      e.stopPropagation();
      var ag = S.agents[idx];
      if(ag) toast('💬 '+ag.char+' '+ag.name+': '+(ag.speech||''));
    };
  });
  applyAgentFilter();
  // dai trang thai header khoi VP Song (so lieu that tu STATE)
  renderVpStrip(S);
  renderVpFlow(S);
  // finance
  const F = S.finance, keys = Object.keys(F);
  $('finance-box').innerHTML = keys.length ? keys.slice(0,6).map(k=>
    '<div class="fin-row"><span>'+k+'</span><span class="v">'+F[k]+'</span></div>').join('')
    : '<div class="vp-empty">Chưa có dữ liệu tài chính.<br><b>Làm gì tiếp:</b> bấm nút 🔄 Đồng bộ trên thanh icon trái, rồi tải lại trang.</div>';
  // drives
  const D = S.drives;
  $('drive-box').innerHTML = ['G','H','J'].map(d=>{
    const i = D[d];
    if(!i) return '<div class="fin-row"><span>💽 Ổ '+d+'</span><span class="v">⏳ chưa có số liệu</span></div>';
    const col = i.pct>=95?'#ef4444':i.pct>=85?'#f59e0b':'#22c55e';
    return '<div><div class="fin-row"><span>💽 Ổ '+d+'</span><span class="v">'+i.free.toFixed(0)+
     'GB trống</span></div><div class="dbar"><div style="width:'+i.pct+'%;background:'+col+'"></div></div></div>';
  }).join('') + (['G','H','J'].every(d=>!D[d])
    ? '<div class="vp-empty"><b>Làm gì tiếp:</b> số liệu ổ cứng do máy chủ cập nhật định kỳ — nếu trống lâu, kiểm tra lại máy chủ rồi tải lại trang.</div>' : '');
  // ec steps — tác vụ chuyển dữ liệu ổ cứng (thuộc Kỹ thuật/Dữ liệu); xong hết thì ẩn
  const ecAllDone = S.ec.length && S.ec.every(s=>s.status==='done' && s.pct>=100);
  document.getElementById('hud-bottom').style.display = ecAllDone ? 'none' : '';
  $('ec-steps').innerHTML = S.ec.length ? S.ec.map(s=>{
    const cls = s.status==='done'?'#22c55e':s.status==='run'?'#f59e0b':'#475569';
    const bar = s.pct>=0
      ? '<div class="pbar"><div class="pfill" style="width:'+s.pct+'%;background:'+cls+'"></div></div>'+
        '<div class="pmeta">'+s.pct+'% · '+s.detail+'</div>'
      : '<div class="pbar"><div class="pfill stripes" style="width:100%"></div></div>'+
        '<div class="pmeta">'+s.detail+'</div>';
    return '<div class="ec-step"><div class="eid">'+s.char+' '+s.id+'</div>'+
     '<div class="enm">'+s.name+'<br>'+s.route+'</div>'+bar+'</div>';
  }).join('')
  : '<div class="vp-empty">Chưa có tác vụ chuyển dữ liệu nào.<br><b>Làm gì tiếp:</b> khi có đợt chuyển ổ cứng mới, tác vụ sẽ tự hiện ở đây — không cần tạo tay.</div>';
  if(document.body.classList.contains('simple-view')) renderSimple();
}

// ---------- VP Song v2.5: dai trang thai + so do luong + dang Don gian (chi hien thi) ----------
function vpCounts(S){
  const c = {total:(S.agents||[]).length, running:0, waiting:0, resting:0, error:0};
  (S.agents||[]).forEach(a=>{
    const d = a.display_status || (a.status==='busy'?'running':a.status==='ready'?'waiting':a.status==='error'?'error':'resting');
    if(c[d]!==undefined) c[d]++;
  });
  return c;
}
function renderVpStrip(S){
  const el = $('vp-status-strip'); if(!el) return;
  const c = vpCounts(S), vp = S.vp || {};
  const pend = vp.vp_pending_count || 0;
  const round = vp.vp_last_round || 0;
  if(!c.total){
    el.innerHTML = '<span class="vp-empty">Chưa có agent nào — danh sách sẽ hiện khi router/provider có số liệu.</span>';
    return;
  }
  el.innerHTML = '👥 <b>'+c.total+' agent</b> · 🟢 <b>'+c.running+'</b> Đang chạy · '+
    '🟠 <b>'+c.waiting+'</b> Chờ việc · ⚪ <b>'+c.resting+'</b> Đang nghỉ · '+
    '🔴 <b>'+c.error+'</b> Gặp sự cố · ⏳ <b>'+pend+'</b> việc chờ anh duyệt'+
    (round ? ' · 💬 Vòng '+round : '');
  const sv = $('sv-strip'); if(sv) sv.innerHTML = el.innerHTML;
}
function renderVpFlow(S){
  const el = $('vp-flow'); if(!el) return;
  const vp = S.vp || {};
  const sp = vp.vp_speakers || [];
  const chat = sp.filter(n=>n!=='Hệ thống');
  const topic = vp.vp_topic || '';
  const pend = vp.vp_pending_count || 0;
  const steps = [
    ['1 · 📥 Lấy trạng thái thật', (pend? pend+' việc đang chờ' : 'chưa có việc đang chờ') + ' · council · goal'],
    ['2 · 💬 Nêu ý', chat[0] ? chat[0] + (topic ? ' — “'+topic+'”' : '') : 'chờ agent nêu ý ở vòng tới'],
    ['3 · 🔁 Phản biện', chat[1] ? chat[1] + ' phản biện / bổ sung' : 'chờ agent thứ hai phản biện'],
    ['4 · 💾 Ghi vào feed', vp.vp_last_round ? 'đã ghi vòng '+vp.vp_last_round : 'chưa ghi vòng nào'],
    ['5 · ✅ Chờ anh duyệt', pend ? pend+' việc đang chờ anh duyệt' : 'không còn việc chờ duyệt'],
  ];
  el.innerHTML = steps.map(s=>'<div class="flow-step"><b>'+s[0]+'</b><span>'+s[1]+'</span></div>').join('<span class="flow-arrow">→</span>') +
    (!vp.vp_last_round ? '<div class="vp-empty" style="flex-basis:100%">Chưa có vòng chat nào — sơ đồ sẽ hiện tên agent thật khi vòng đầu tiên chạy xong. Vòng chat chạy tự động mỗi giờ, không cần bấm gì.</div>' : '') +
    (!pend ? '<div class="vp-empty" style="flex-basis:100%">Chưa có việc đang chờ — giao việc mới ở <b>/new</b> để vòng chat có chủ đề bàn.</div>' : '');
}
function renderSimple(){
  const ag = $('sv-agents'), lv = $('sv-live'), fl = $('sv-flow'), fn = $('sv-fin');
  if(ag) ag.innerHTML = $('agent-list') ? $('agent-list').innerHTML : '';
  if(lv) lv.innerHTML = $('vp-live-box') ? $('vp-live-box').innerHTML : '';
  if(fl) fl.innerHTML = $('vp-flow') ? $('vp-flow').innerHTML : '';
  if(fn) fn.innerHTML = ($('finance-box') ? $('finance-box').innerHTML : '') + ($('drive-box') ? $('drive-box').innerHTML : '');
  // chon dong + hover-action tren ban sao agent (ban goc o HUD khong giu listener khi copy innerHTML)
  if(ag) ag.querySelectorAll('.agent-row').forEach(function(row, idx){
    row.onclick = function(){
      ag.querySelectorAll('.agent-row').forEach(function(r){ r.classList.remove('selected'); });
      row.classList.add('selected');
    };
    var btn = row.querySelector('.row-action');
    if(btn) btn.onclick = function(e){
      e.stopPropagation();
      var a2 = STATE.agents[idx];
      if(a2) toast('💬 '+a2.char+' '+a2.name+': '+(a2.speech||''));
    };
  });
  // cot viec cho duyet: tieu de that tu task-bus pending (chi doc), nhan "Cho duyet" nhu trang 2D
  const tk = $('sv-tasks');
  if(tk){
    const titles = ((STATE.vp||{}).vp_pending_titles||[]);
    tk.innerHTML = titles.length ? titles.map(function(t){
      return '<div class="task-row" data-name="'+escHtml(t).toLowerCase()+'" data-status="wait">'+
       '<span class="cls-pill cls-wait">Chờ duyệt</span><span>'+escHtml(t)+'</span>'+
       '<a class="row-action" href="/new" title="Mở trang giao việc">↗ Mở /new</a></div>';
    }).join('')
    : '<div class="vp-empty">Chưa có việc đang chờ.<br><b>Làm gì tiếp:</b> giao việc mới ở <b>/new</b> để vòng chat có chủ đề bàn.</div>';
    tk.querySelectorAll('.task-row').forEach(function(row){
      row.onclick = function(){
        tk.querySelectorAll('.task-row').forEach(function(r){ r.classList.remove('selected'); });
        row.classList.add('selected');
      };
    });
  }
  const su = $('sv-unread'), vu = $('vp-unread');
  if(su && vu){ su.textContent = vu.textContent; su.style.display = vu.style.display; }
  applySimpleAgentFilter();
  applySimpleTaskFilter();
}

// ---------- v2.6 Liquid Glass: loc/tim client-side (chi hien thi, khong doi du lieu) ----------
function escHtml(s){
  return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
let agentQ = '', agentF = 'all', svAgentQ = '', svAgentF = 'all', svTaskQ = '', svTaskF = 'all';
function filterRows(container, q, f){
  const el = document.getElementById(container); if(!el) return;
  const rows = el.querySelectorAll('.agent-row, .task-row');
  rows.forEach(function(r){
    const nm = (r.getAttribute('data-name')||'').toLowerCase();
    const st = r.getAttribute('data-status')||'';
    const okQ = !q || nm.indexOf(q)>=0;
    const okF = (f==='all') || (st===f);
    r.style.display = (okQ && okF) ? '' : 'none';
  });
}
function applyAgentFilter(){ filterRows('agent-list', agentQ, agentF); }
function applySimpleAgentFilter(){ filterRows('sv-agents', svAgentQ, svAgentF); }
function applySimpleTaskFilter(){ filterRows('sv-tasks', svTaskQ, svTaskF); }
function bindTabs(tabId, onPick){
  const wrap = document.getElementById(tabId); if(!wrap) return;
  wrap.querySelectorAll('button').forEach(function(b){
    b.onclick = function(){
      wrap.querySelectorAll('button').forEach(function(x){ x.classList.remove('on'); });
      b.classList.add('on');
      onPick(b.getAttribute('data-f'));
    };
  });
}
(function(){
  const s1 = document.getElementById('agent-search');
  if(s1) s1.oninput = function(){ agentQ = s1.value.trim().toLowerCase(); applyAgentFilter(); };
  bindTabs('agent-tabs', function(f){ agentF = f; applyAgentFilter(); });
  const s2 = document.getElementById('sv-agent-search');
  if(s2) s2.oninput = function(){ svAgentQ = s2.value.trim().toLowerCase(); applySimpleAgentFilter(); };
  bindTabs('sv-agent-tabs', function(f){ svAgentF = f; applySimpleAgentFilter(); });
  const s3 = document.getElementById('sv-task-search');
  if(s3) s3.oninput = function(){ svTaskQ = s3.value.trim().toLowerCase(); applySimpleTaskFilter(); };
  bindTabs('sv-task-tabs', function(f){ svTaskF = f; applySimpleTaskFilter(); });
})();
function setSimpleView(on){
  document.body.classList.toggle('simple-view', !!on);
  try{ localStorage.setItem('vp3d-view', on ? 'simple' : '3d'); }catch(e){}
  const btn = $('vp-view-toggle');
  if(btn) btn.textContent = on ? '🌐 Bản đồ 3D' : '📋 Đơn giản';
  if(on) renderSimple();
}
(function(){
  let saved = null;
  try{ saved = localStorage.getItem('vp3d-view'); }catch(e){}
  const btn = $('vp-view-toggle');
  if(btn) btn.onclick = ()=>setSimpleView(!document.body.classList.contains('simple-view'));
  const back = $('sv-back');
  if(back) back.onclick = ()=>setSimpleView(false);
  if(saved === 'simple') setSimpleView(true);
})();

// ---------- v2.9: panel phu thu gon mac dinh, bam pill de mo (chi hien thi) ----------
(function(){
  var panels = {left:false, right:false};
  try{ var pp = JSON.parse(localStorage.getItem('vp3d-panels')||'{}');
    panels.left = !!pp.left; panels.right = !!pp.right; }catch(e){}
  function paint(){
    document.body.classList.toggle('show-left', panels.left);
    document.body.classList.toggle('show-right', panels.right);
    var bl = document.getElementById('btn-left-toggle');
    var br = document.getElementById('btn-right-toggle');
    if(bl) bl.classList.toggle('on', panels.left);
    if(br) br.classList.toggle('on', panels.right);
    try{ localStorage.setItem('vp3d-panels', JSON.stringify(panels)); }catch(e){}
  }
  var bl = document.getElementById('btn-left-toggle');
  var br = document.getElementById('btn-right-toggle');
  if(bl) bl.onclick = function(){ panels.left = !panels.left; paint(); };
  if(br) br.onclick = function(){ panels.right = !panels.right; paint(); };
  paint();
})();

renderPanels();

// ---------- data refresh ----------
let lastStatus = {};
STATE.agents.forEach(a=>{ lastStatus[a.key]=a.status; });
async function refreshData(){
  try{
    const r = await fetch('/api/office3d/state');
    const d = await r.json();
    STATE = d;
    d.agents.forEach(a=>{
      const ag = agents[a.key]; if(!ag) return;
      ag.data = a;
      ag.ring.material.color.setHex(ST_COLOR[a.status]||0x64748b);
      if(SET.notify && lastStatus[a.key] && lastStatus[a.key]!==a.status){
        toast(a.char+' '+a.name+': '+(ST_TXT[a.status]?ST_TXT[a.status][0]:a.status));
      }
      lastStatus[a.key] = a.status;
    });
    assignTargets();
    renderPanels();
  }catch(e){}
}
let refreshTimer = setInterval(refreshData, SET.refresh*1000);

// ---------- toast ----------
let toastT=null;
function toast(msg){
  const t=$('toast'); t.textContent=msg; t.style.display='block';
  clearTimeout(toastT); toastT=setTimeout(()=>{t.style.display='none';}, 3500);
}

// ---------- animation ----------
const clock = new THREE.Clock();
function animate(){
  requestAnimationFrame(animate);
  const dt = Math.min(clock.getDelta(), 0.05);
  const t = clock.elapsedTime;
  const now = performance.now()/1000;
  Object.values(agents).forEach(ag=>{
    const p = ag.group.position, tg = ag.target;
    const dx = tg.x-p.x, dz = tg.z-p.z, dist = Math.hypot(dx,dz);
    if(dist > 0.12){
      const step = Math.min(dist, ag.speed*dt);
      p.x += dx/dist*step; p.z += dz/dist*step;
      ag.group.rotation.y = Math.atan2(dx, dz);
      p.y = Math.abs(Math.sin(t*11+ag.bob))*0.09;   // bước đi nảy
    } else {
      p.y *= 0.9;
      // đi lang thang khi rảnh
      const st = ag.data.status;
      if(SET.autoMove && st!=='busy' && now > ag.wanderT){
        const z = zoneOf(ag.data.dept);
        ag.target.set(z.x+(Math.random()*7-3.5), 0, z.z+(Math.random()*5-2.5));
        ag.wanderT = now + 4 + Math.random()*6;
      }
    }
    // màn hình desk sáng khi chủ đang bận
    const z = zoneOf(ag.data.dept);
    if(z._screen) z._screen.material.emissiveIntensity = ag.data.status==='busy' ? 1.2 : 0.45;
  });
  // robot đi hành lang (khu trung tâm giữa các phòng)
  {
    const rp = robot.group.position, rt = robot.target;
    const dx = rt.x-rp.x, dz = rt.z-rp.z, dist = Math.hypot(dx,dz);
    if(dist > 0.15){
      const step = Math.min(dist, robot.speed*dt);
      rp.x += dx/dist*step; rp.z += dz/dist*step;
      robot.group.rotation.y = Math.atan2(dx, dz);
      rp.y = Math.abs(Math.sin(t*9))*0.05;
    } else if(SET.autoMove && now > robot.wanderT){
      // đi ngẫu nhiên quanh trung tâm (hành lang)
      robot.target.set(Math.random()*20-10, 0, Math.random()*12-6);
      robot.wanderT = now + 5 + Math.random()*7;
    }
    robot.tip.material.color.setHex(Math.sin(t*6)>0 ? 0xef4444 : 0x22c55e);
  }
  // bong bóng thoại ngẫu nhiên
  Object.values(agents).forEach(ag=>{
    if(ag.bubble && now > ag.bubbleT){
      ag.group.remove(ag.bubble); ag.bubble = null;
    } else if(!ag.bubble && SET.autoMove && Math.random() < dt*0.03){
      showBubble(ag);
    }
  });
  controls.update();
  renderer.render(scene, camera);
}
animate();

addEventListener('resize', ()=>{
  camera.aspect = innerWidth/innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});
$('loading').style.display='none';

// ---------- settings ----------
function bindSwitch(id, key, cb){
  const el = $(id);
  const paint = ()=>el.classList.toggle('on', !!SET[key]);
  paint();
  el.onclick = ()=>{ SET[key]=!SET[key]; saveSet(); paint(); if(cb)cb(); };
}
bindSwitch('sw-move','autoMove');
bindSwitch('sw-label','labels', ()=>{ Object.values(agents).forEach(a=>a.label.visible=SET.labels); });
bindSwitch('sw-notify','notify');
$('sel-cam').value = SET.camera;
$('sel-cam').onchange = e=>{ SET.camera=e.target.value; saveSet(); applyCamera(); };
$('sel-refresh').value = String(SET.refresh);
$('sel-refresh').onchange = e=>{ SET.refresh=+e.target.value; saveSet();
  clearInterval(refreshTimer); refreshTimer=setInterval(refreshData, SET.refresh*1000); };
$('settings-btn').onclick = ()=>$('settings-panel').classList.toggle('open');
if(!SET.labels) Object.values(agents).forEach(a=>a.label.visible=false);

// ---------- chat ----------
const AGENT_META = {gemini:['🦊','Gemini'], muse:['🦉','Muse'], deepseek:['🦈','DeepSeek'],
                    lmstudio:['🐸','LM Studio'], muse_main:['🤖','Muse']};
$('chat-bubble').onclick = ()=>{
  const p=$('chat-panel'); p.classList.toggle('open');
  if(p.classList.contains('open') && !$('chat-msgs').children.length) loadHistory();
};
function renderMsg(m){
  const d=document.createElement('div');
  const isU = m.role==='user';
  d.className='msg '+(isU?'user':'ai');
  const meta = !isU && m.agent && AGENT_META[m.agent] ? AGENT_META[m.agent][0]+' '+AGENT_META[m.agent][1] : '';
  d.innerHTML = (meta?'<div class="who">'+meta+'</div>':'')+'<div></div>';
  d.lastChild.textContent = m.content||'';
  return d;
}
async function loadHistory(){
  try{
    const r = await fetch('/api/chat/history'); const d = await r.json();
    const box=$('chat-msgs'); box.innerHTML='';
    (d.messages||[]).slice(-30).forEach(m=>box.appendChild(renderMsg(m)));
    box.scrollTop=box.scrollHeight;
  }catch(e){}
}
$('chat-form').onsubmit = async e=>{
  e.preventDefault();
  const inp=$('chat-input'), text=inp.value.trim(); if(!text) return;
  const agent=$('chat-agent').value; inp.value='';
  const box=$('chat-msgs');
  box.appendChild(renderMsg({role:'user', content:text})); box.scrollTop=box.scrollHeight;
  const typing=document.createElement('div'); typing.className='msg ai'; typing.textContent='…';
  box.appendChild(typing); box.scrollTop=box.scrollHeight;
  try{
    const r = await fetch('/api/chat/send',{method:'POST',
      headers:{'Content-Type':'application/x-www-form-urlencoded'},
      body:'msg='+encodeURIComponent(text)+'&agent='+encodeURIComponent(agent)+'&csrf='+encodeURIComponent(CSRF)});
    const d = await r.json(); typing.remove();
    if(d.message){ box.appendChild(renderMsg(d.message)); }
    else if(d.error){ box.appendChild(renderMsg({role:'ai', agent:'muse', content:'⚠️ '+d.error})); }
    box.scrollTop=box.scrollHeight;
  }catch(err){ typing.remove(); }
};

// ---------- VP Sống · trực tiếp (gộp từ trang VP Sống cũ) ----------
(function(){
  var box=document.getElementById('vp-live-box'); if(!box) return;
  var seen={}, lastId=0, lastDate='', lastAgent='';
  // Badge "chua doc": chi luu client-side (localStorage), khong doi feed/server/luat doc.
  var vpUnread=0, vpLastRead=null, firstPoll=true;
  try{ var _lr = localStorage.getItem('vp-last-read-id'); vpLastRead = (_lr===null?null:parseInt(_lr,10)); }catch(e){}
  function fmtDate(ts){ var d=new Date(ts); if(isNaN(d.getTime())) return '';
    return ('0'+d.getDate()).slice(-2)+'/'+('0'+(d.getMonth()+1)).slice(-2)+'/'+d.getFullYear(); }
  function fmtTime(ts){ var d=new Date(ts); if(isNaN(d.getTime())) return '';
    return ('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2); }
  function linkify(text){
    var esc = escHtml(text);
    var urlRe = new RegExp('https?://[^\\s<]+', 'g');
    return esc.replace(urlRe, function(u){
      var dom = (u.split('/')[2]||u);
      return '<a class="vp-linkcard" target="_blank" rel="noopener" href="'+u.replace(/"/g,'&quot;')+'">🌐 '+escHtml(dom)+'</a>';
    });
  }
  function updateUnreadBadge(){
    var b=document.getElementById('vp-unread'); if(!b) return;
    if(vpUnread>0){ b.textContent='🔴 '+vpUnread+' chưa đọc'; b.style.display='inline-block'; }
    else b.style.display='none';
    var sb=document.getElementById('sv-unread');
    if(sb){ sb.textContent=b.textContent; sb.style.display=b.style.display; }
  }
  function markVpRead(){
    if(lastId>0){ vpLastRead=lastId; try{ localStorage.setItem('vp-last-read-id', String(lastId)); }catch(e){} }
    vpUnread=0; updateUnreadBadge();
  }
  box.onclick = markVpRead;
  function addMsg(m){
    var ds=fmtDate(m.ts);
    if(ds && ds!==lastDate){
      lastDate=ds;
      var dv=document.createElement('div'); dv.className='vp-date-pill'; dv.textContent='📅 '+ds;
      box.appendChild(dv);
    }
    var grouped=(m.agent===lastAgent); lastAgent=m.agent;
    var d=document.createElement('div');
    d.className='vp-live-msg'+(m.agent==='Hệ thống'?' sys':'')+(grouped?' vp-grouped':'');
    var b=document.createElement('b'); b.className='vp-sender'; b.textContent=m.agent||'AI';
    var s=document.createElement('span'); s.innerHTML=linkify(m.text||'');
    var tm=document.createElement('span'); tm.className='vp-time'; tm.textContent=fmtTime(m.ts);
    d.appendChild(b); d.appendChild(s); d.appendChild(tm); box.appendChild(d);
  }
  function emptyReason(d){
    if(!d.loop_started) return 'Vòng chat chưa chạy trên máy chủ này';
    if(d.last_error) return 'Vòng mới nhất lỗi: '+d.last_error+' — sẽ thử lại vòng sau';
    return 'Chờ vòng chat tiếp theo…';
  }
  function setStatus(d){
    var st=document.getElementById('vp-live-status'); if(!st) return;
    if(d.messages && d.messages.length){ st.textContent='· Vòng '+d.last_round+' · '+d.msg_count+' tin'; }
    else { st.textContent='· '+emptyReason(d); }
  }
  async function poll(first){
    try{
      var r=await fetch('/api/office-chat?since='+(first?0:lastId));
      var d=await r.json();
      setStatus(d);
      if(first && !d.messages.length){ box.innerHTML='<div class="vp-empty">⏳ '+emptyReason(d)+'<br><b>Làm gì tiếp:</b> vòng chat chạy tự động mỗi giờ — tin đầu tiên sẽ hiện ở đây, không cần bấm gì. Nếu sau 1 giờ vẫn trống, kiểm tra vòng chat trên máy chủ rồi tải lại trang.</div>'; if(document.body.classList.contains('simple-view')) renderSimple(); return; }
      if(first){ box.innerHTML=''; lastDate=''; lastAgent=''; }
      d.messages.forEach(function(m){
        if(seen[m.id]) return; seen[m.id]=1; addMsg(m);
        if(m.id>lastId) lastId=m.id;
        if(vpLastRead!==null && m.id>vpLastRead) vpUnread++;
      });
      if(firstPoll){
        firstPoll=false;
        if(vpLastRead===null) markVpRead(); else updateUnreadBadge();
      } else updateUnreadBadge();
      if(document.body.classList.contains('simple-view')) renderSimple();
    }catch(e){}
  }
  poll(true); setInterval(function(){poll(false);},30000);
})();
</script>
</body></html>
"""
        body = body.replace("__STATE__", state_json)
        body = body.replace("__CSRF__", csrf)
        body = body.replace("__USER__", _h.escape(user))
        body = body.replace("__VERSION__", ORCHESTRA_VERSION)
        body = body.replace("__VERSION_DATE__", ORCHESTRA_VERSION_DATE)
        self._send(body)


    def _ai_office(self, user):
        """🏢 Văn Phòng AI — nhân vật là trung tâm, thống nhất với Orchestra."""
        import re, subprocess, json as _json, time as _time, os as _os
        csrf = self._csrf_token() or ""
        # ---------- dữ liệu tài chính ----------
        fin, overdue = {}, []
        try:
            from data_providers.finance_data import fetch_dashboard, is_configured
            if is_configured():
                d = fetch_dashboard()
                if d:
                    fin = d.get("stats", {})
                    overdue = d.get("overdue", [])
        except Exception:
            pass
        # ---------- token stats chi tiết: 1 / 7 / 30 ngày ----------
        tok = {}
        try:
            from data_providers.token_stats import get_stats
            for days in (1, 7, 30):
                s = get_stats(days)
                for a, v in s.get("by_ai", {}).items():
                    tok.setdefault(a, {})[days] = {
                        "tokens": v["in"] + v["out"],
                        "calls": v["calls"],
                        "cost": v.get("cost_usd", 0),
                    }
                if days == 1:
                    total_tok = s.get("total_tokens", 0)
                    total_cost = s.get("total_cost_usd", 0)
        except Exception:
            total_tok, total_cost = 0, 0

        # ---------- dung lượng ổ G/H/J từ sheet Kho Ổ Cứng (cache 2h) ----------
        drives = {}
        _cache = "/tmp/ofc-drive-cache.json"
        try:
            use_cache = (_os.path.exists(_cache)
                         and _time.time() - _os.path.getmtime(_cache) < 7200)
            if use_cache:
                drives = _json.load(open(_cache))
            else:
                r = subprocess.run(
                    ["/opt/hatch/bin/hatch_gws_cli", "sheets", "spreadsheets",
                     "values", "get", "--params", _json.dumps({
                         "spreadsheetId": "1iACXzX7gqhEwZhbn5FMKbILplKOLuD7okEJ5db10TIc",
                         "range": "Tổng quan!A1:F12"})],
                    capture_output=True, text=True, timeout=15)
                vals = _json.loads(r.stdout).get("values", [])
                for row in vals[1:]:
                    if len(row) >= 5 and row[0] in ("G", "H", "J"):
                        try:
                            drives[row[0]] = {
                                "free": float(row[3].replace(",", ".")),
                                "total": float(row[1].replace(",", ".")),
                                "pct": int(row[4].replace("%", "")),
                            }
                        except Exception:
                            pass
                if drives:
                    _json.dump(drives, open(_cache, "w"))
        except Exception:
            pass
        # ---------- kho kiến thức chung (Drive) ----------
        kb_status = {"skills": 0, "rules": 0, "loaded_at": "?"}
        kb_link = "https://drive.google.com/drive/folders/1GYwsn2uiQ817qYRlZuFsCLUC9eJBk-nO"
        try:
            from data_providers.knowledge import status as kb_status_fn
            kb_status = kb_status_fn()
            kb_link = kb_status.get("drive_link", kb_link)
        except Exception:
            pass
        n_events = None
        try:
            from data_providers.calendar_data import upcoming
            n_events = len(upcoming(7))
        except Exception:
            pass
        drive_n, drive_built = None, None
        try:
            from data_providers.drive_index import index_status
            st = index_status()
            drive_n, drive_built = st.get("count", 0), st.get("last_build")
        except Exception:
            pass

        def _num(t):
            dd = re.sub(r"[^\d]", "", t or "")
            return int(dd) if dd else 0

        def _vnd(n):
            return f"{n:,}".replace(",", ".") + "đ"

        # Nhân vật chính = AI agent thật của Orchestra + 2 role học từ mô hình bot theo vai trò
        AGENTS = [
            ("gemini", "🦊", "Gemini", "#4aa8ff", "Cáo lanh lợi",
             "📊 Phân tích chi tiêu"),
            ("muse", "🦉", "Muse", "#b07bff", "Cú điềm tĩnh",
             "✅ Điều phối + Duyệt chi"),
            ("deepseek", "🦈", "DeepSeek", "#39c5ff", "Cá mập tinh mắt",
             "🔍 Soi công nợ"),
            ("safety", "🛡️", "Bot An Toàn", "#ff6b6b", "Khiên bảo vệ",
             "🔒 Kiểm duyệt an toàn"),
            ("pm", "📋", "Trưởng Dự Án", "#ffa94d", "Điều phối viên",
             "📊 Điều phối task"),
            ("lmstudio", "🐸", "LM Studio", "#3ddc84", "Ếch tốc độ",
             "⚡ Trả lời nhanh"),
        ]
        C = {k: c for k, _, _, c, _, _ in AGENTS}
        # ---------- Cấp bậc + hiệu suất agent (đúng bản chất AI) ----------
        # AI KHÔNG có lương/nghỉ việc/tinh thần kiểu người.
        # Thăng cấp = mở rộng quyền hạn. Trạng thái = vận hành.
        RANKS = ["Thực tập sinh", "Nhân viên", "Chuyên viên",
                 "Trưởng nhóm", "Trưởng phòng", "Giám đốc"]
        RANK_PERM = {
            0: "Chỉ làm task đơn giản",
            1: "Task thông thường",
            2: "Task phức tạp",
            3: "Điều phối nhóm nhỏ",
            4: "Quản lý phòng ban (tối đa 15 NV)",
            5: "Điều phối toàn bộ",
        }
        RANK_PROMO = {
            0: "Cần: 10 task xong, tỷ lệ thành công ≥ 90%",
            1: "Cần: 30 task xong, tỷ lệ thành công ≥ 92%",
            2: "Cần: 60 task xong, tỷ lệ thành công ≥ 95%",
            3: "Cần: 100 task xong, tỷ lệ thành công ≥ 95%",
            4: "Cần: 150 task xong, tỷ lệ thành công ≥ 97%",
            5: "Tối cao",
        }
        rpg = {"agents": {}, "company": {"reputation": 82}}
        _rpg_path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "data", "agent_rpg.json")
        try:
            rpg = _json.load(open(_rpg_path))
        except Exception:
            pass
        # hiệu suất từ router stats thật: calls, fails, avg_ms
        agent_perf, agent_rank, agent_status = {}, {}, {}
        _router_stats = {}
        try:
            from orchestrator.router import get_router
            for s in get_router().all_stats():
                _router_stats[s["agent"]] = s
        except Exception:
            pass
        # inflight cho các phần khác dùng
        inflight = {a: s.get("in_flight", 0)
                    for a, s in _router_stats.items()}
        for key, _, _, _, _, _ in AGENTS:
            rd = rpg.get("agents", {}).get(key, {})
            rank = rd.get("rank", 1)
            st = _router_stats.get(key, {})
            calls = st.get("calls", 0)
            fails = st.get("fails", 0)
            avg_ms = st.get("avg_ms", 0)
            ok_rate = round((calls - fails) * 100 / calls, 1) if calls else 100.0
            # trạng thái vận hành: kiểm tra sức khỏe provider thật
            if inflight.get(key, 0) > 0:
                status, status_cls = "🔄 ĐANG CHẠY", "busy"
                health_msg = ""
            else:
                try:
                    from data_providers.provider_health import provider_health
                    healthy, health_msg = provider_health(key)
                except Exception:
                    healthy, health_msg = True, ""
                if not healthy:
                    status, status_cls = "🔴 LỖI", "error"
                elif calls and fails * 2 >= calls:
                    status, status_cls = "🔴 LỖI", "error"
                    health_msg = f"Tỷ lệ lỗi cao ({fails}/{calls})"
                else:
                    status, status_cls = "🟢 SẴN SÀNG", "ready"
            agent_perf[key] = {"calls": calls, "fails": fails,
                               "ok_rate": ok_rate, "avg_ms": avg_ms,
                               "health_msg": health_msg if 'health_msg' in dir() else ""}
            agent_rank[key] = rank
            agent_status[key] = (status, status_cls)
        avg_ok = (round(sum(p["ok_rate"] for p in agent_perf.values())
                        / len(agent_perf), 1) if agent_perf else 100.0)
        # ---------- buff phòng ban ----------
        DEPT_BUFF = {
            "Kế Toán & Tài Chính": "💰 Giảm 10% chi phí",
            "Kỹ Thuật & Lưu Trữ": "🛡️ An toàn dữ liệu +",
            "Hậu Kỳ & Sản Xuất": "⚡ Tốc độ xử lý +15%",
            "App & Mobile": "🔧 Ít bug hơn",
            "Social & Marketing": "⭐ Uy tín + mỗi ngày",
            "Lịch & Điều Hành": "📅 Không trễ deadline",
            "Phòng Dữ Liệu": "🔍 Tìm kiếm siêu nhanh",
        }
        # ---------- chờ sếp duyệt (task-bus pending) ----------
        pending_approvals = []
        try:
            _tb = os.path.expanduser("~/workspace/task-bus/pending")
            for fn in sorted(os.listdir(_tb)):
                if fn.endswith(".json"):
                    d = _json.load(open(os.path.join(_tb, fn)))
                    pending_approvals.append({
                        "file": fn,
                        "task": d.get("task", "")[:120],
                        "from": d.get("from", "?"),
                    })
        except Exception:
            pass

        # ---------- (b) Pattern v2.6 tu trang 3D: dai trang thai + loi thoai
        # (chi hien thi, dung lai get_vp_summary() cua office_chat, khong doi
        # vong chat / task-bus / luat duyet). Resting: trang 2D khong co
        # trang thai nghi rieng (ready = cho viec) nen dem = 0, hien that.
        vp2 = {"vp_topic": "", "vp_speakers": [], "vp_pending_count": 0,
               "vp_pending_by_agent": {}, "vp_last_round": 0}
        try:
            import office_chat as _oc2
            vp2 = _oc2.get_vp_summary()
        except Exception:
            pass
        _pend2 = vp2.get("vp_pending_count") or 0
        _spk2 = vp2.get("vp_speakers") or []
        _topic2 = vp2.get("vp_topic") or ""
        _by2 = {str(k).strip().lower(): v
                for k, v in (vp2.get("vp_pending_by_agent") or {}).items()}
        _vp_counts2 = {
            "total": len(AGENTS),
            "running": sum(1 for v in agent_status.values() if v[1] == "busy"),
            "waiting": sum(1 for v in agent_status.values() if v[1] == "ready"),
            "resting": 0,
            "error": sum(1 for v in agent_status.values() if v[1] == "error"),
        }
        vp_strip2 = (
            f"<div class='ofc-vp-strip'>👥 <b>{_vp_counts2['total']} agent</b> · "
            f"🟢 <b>{_vp_counts2['running']}</b> Đang chạy · "
            f"🟠 <b>{_vp_counts2['waiting']}</b> Chờ việc · "
            f"⚪ <b>{_vp_counts2['resting']}</b> Đang nghỉ · "
            f"🔴 <b>{_vp_counts2['error']}</b> Gặp sự cố · "
            f"⏳ <b>{_pend2}</b> việc chờ anh duyệt"
            + (f" · 💬 Vòng {vp2.get('vp_last_round')}" if vp2.get("vp_last_round") else "")
            + "</div>")
        if not vp2.get("vp_last_round"):
            vp_strip2 += (
                "<div class='ofc-empty'>Chưa có vòng chat nào — vòng chat chạy "
                "tự động mỗi giờ, không cần bấm gì. Tin đầu tiên sẽ hiện ở "
                "khung chat VP Sống trên trang 3D.</div>")

        def _speech2(key, name):
            _st, _cls = agent_status.get(key, ("🟢 SẴN SÀNG", "ready"))
            if _cls == "error":
                return "Gặp sự cố — kiểm tra provider"
            if _cls == "busy":
                return "Đang chạy task"
            if key == "muse" and _pend2 > 0:
                return "Chờ anh duyệt: %d việc đang chờ" % _pend2
            if name in _spk2 and _topic2:
                return "Đang bàn: " + _topic2
            if name in _spk2:
                return "Đang bàn trong vòng chat mới nhất"
            if key in ("gemini", "deepseek"):
                return "Nghỉ — chờ vòng chat sau"
            return "Sẵn sàng — chờ việc mới"

        def _pend_txt2(key, name):
            n = _by2.get(key.lower(), _by2.get(name.lower(), 0))
            return (f"<div class='ofc-pend'>⏳ {n} việc chờ của agent này</div>"
                    if n > 0 else "")

        def hero(key, char, name, color, persona, role):
            t = tok.get(key, {})
            d1 = t.get(1, {"tokens": 0, "calls": 0, "cost": 0})
            rank = agent_rank.get(key, 1)
            perf = agent_perf.get(key, {"calls": 0, "fails": 0,
                                        "ok_rate": 100.0, "avg_ms": 0})
            status, status_cls = agent_status.get(key, ("🟢 SẴN SÀNG", "ready"))
            health_tip = perf.get("health_msg", "")
            tip_attr = f" title='{html.escape(health_tip)}'" if health_tip else ""
            badge = f"<span class='ofc-badge ofc-{status_cls}'{tip_attr}>{status}</span>"
            cls = status_cls
            spd = (f"{perf['avg_ms']/1000:.1f}s" if perf["avg_ms"] else "—")
            return (
                f"<a class='ofc-hero {cls}' href='/chat' title='Nhắn với {html.escape(name)}'>"
                f"<div class='ofc-char'>{char}</div>"
                f"<div class='ofc-table'></div>"
                f"<div class='ofc-rank'>🎖️ {RANKS[rank]}</div>"
                f"<div class='ofc-perm'>{RANK_PERM[rank]}</div>"
                f"<div class='ofc-name' style='border-color:{color}'>"
                f"<span class='ofc-dot' style='background:{color}'></span>{html.escape(name)}</div>"
                f"<div class='ofc-persona'>“{persona}”</div>"
                f"<div class='ofc-role'>{role}</div>"
                f"<div class='ofc-speech'>💬 “{html.escape(_speech2(key, name))}”</div>"
                f"{_pend_txt2(key, name)}{badge}"
                f"<div class='ofc-perf'>✅ {perf['ok_rate']}% · "
                f"⚡ {spd} · 🔢 {perf['calls']} task</div>"
                f"<div class='ofc-promo' title='{RANK_PROMO[rank]}'>⬆️ {RANK_PROMO[rank]}</div>"
                f"<div class='ofc-kb'>📚 Đã học: {kb_status['skills']} skills · "
                f"{kb_status['rules']} quy tắc</div>"
                f"<div class='ofc-tok'>🎟️ {fmt_num(d1['tokens'])} token · "
                f"~${d1['cost']:.4f}</div></a>")

        def metric(k, v):
            return (f"<div class='ofc-m'><div class='ofc-mk'>{html.escape(k)}</div>"
                    f"<div class='ofc-mv'>{html.escape(v)}</div></div>")

        def soon():
            return ("<div class='ofc-m'><div class='ofc-mk'>Dữ liệu</div>"
                    "<div class='ofc-mv'>⏳ Sắp có</div></div>")

        # ----- metrics Kế Toán & Tài Chính -----
        m_ketoan = "".join(metric(k, fin[k]) for k in
                           ("Doanh thu 2026", "Công nợ", "Doanh thu tháng này",
                            "Doanh thu 7 ngày") if k in fin)
        if overdue:
            m_ketoan += metric("⚠️ Công nợ quá hạn", ", ".join(overdue[:3]))
        if not m_ketoan:
            m_ketoan = soon()

        # ----- metrics Lịch & Điều Hành -----
        done_m = _num(fin.get("Hoàn thành tháng này", ""))
        rev_m = _num(fin.get("Doanh thu tháng này", ""))
        m_lich = ""
        if "Show" in fin:
            m_lich += metric("Tổng show", fin["Show"])
        if "Hoàn thành tháng này" in fin:
            m_lich += metric("Xong tháng này", fin["Hoàn thành tháng này"])
        if n_events is not None:
            m_lich += metric("📅 Sự kiện 7 ngày tới", str(n_events))
        if done_m and rev_m:
            m_lich += metric("💰 TB/show tháng này", _vnd(rev_m // done_m))
        if not m_lich:
            m_lich = soon()

        # ----- metrics Phòng Dữ Liệu -----
        m_data = ""
        if drive_n is not None:
            m_data += metric("📁 File đã index",
                             f"{drive_n:,}".replace(",", "."))
            m_data += metric("🕐 Index lúc",
                             str(drive_built)[:16] if drive_built else "chưa chạy")
        sync = [("Finance", bool(fin)), ("Drive", bool(drive_n)), ("Orchestra", True)]
        sync_parts = []
        for n, ok in sync:
            cls = "ok" if ok else "no"
            mark = "✅" if ok else "❌"
            sync_parts.append(f"<span class='ofc-sync {cls}'>{mark}{n}</span>")
        m_data += (f"<div class='ofc-m ofc-m-wide'><div class='ofc-mk'>🔗 Đồng bộ nguồn</div>"
                   f"<div class='ofc-mv'>{' '.join(sync_parts)}</div></div>")
        m_data += metric("🛡️ Backup gần nhất", "⏳ Sắp có")

        # ----- EC Shuffle: 4 bước + nhân vật riêng -----
        EC_STEPS = [
            ("B1", "🐢", "Rùa vận chuyển", "INSTA 360 (81GB)", "G:", "J:",
             "done", 100, "81GB → J: xong"),
            ("B2", "🐜", "Kiến thợ", "Work (70GB)", "G:", "H:\\Work-G-temp",
             "done", 100, "70GB → H: xong"),
            ("B3", "🦥", "Lười biếng", "Vertex/Pic/Fonts/2023 (26GB)", "G:", "H:",
             "done", 100, "26GB → H: xong"),
            ("B4", "🦣", "Voi khổng lồ", "EC Team (295GB)", "H:", "G:",
             "done", 100, "295GB → G: xong"),
        ]

        def ec_card(sid, char, cname, name, src, dst, st, pct, detail):
            bmap = {"done": ("✅ XONG", "ofc-ec-done"),
                    "run": ("🔄 ĐANG CHẠY", "ofc-ec-run"),
                    "wait": ("⏳ CHỜ", "ofc-ec-wait")}
            blabel, bcls = bmap[st]
            if pct >= 0:
                bar = (f"<div class='ofc-pbar'><div class='ofc-pfill {bcls}' "
                       f"style='width:{pct}%'></div></div>"
                       f"<div class='ofc-pmeta'>{pct}% · {html.escape(detail)}</div>")
            else:
                bar = (f"<div class='ofc-pbar'><div class='ofc-pfill ofc-ec-run "
                       f"ofc-stripes'></div></div>"
                       f"<div class='ofc-pmeta'>{html.escape(detail)}</div>")
            busy = "busy" if st == "run" else ("idle" if st == "wait" else "done")
            return (
                f"<div class='ofc-ec-card {bcls}'>"
                f"<div class='ofc-ec-char {busy}'>{char}</div>"
                f"<div class='ofc-ec-id'>{sid} · {cname}</div>"
                f"<div class='ofc-ec-name'>{html.escape(name)}</div>"
                f"<div class='ofc-ec-route'>{html.escape(src)} → {html.escape(dst)}</div>"
                f"{bar}"
                f"<span class='ofc-badge {bcls}'>{blabel}</span></div>")

        ec_html = "".join(ec_card(*s) for s in EC_STEPS)
        # xong hết thì ẩn luôn (thuộc Kỹ thuật · Dữ liệu, không phải phòng ban riêng)
        ec_all_done = EC_STEPS and all(s[6] == "done" and s[7] >= 100 for s in EC_STEPS)
        ec_section = "" if ec_all_done else (
            f"<div class='ofc-sec-title ofc-hide-simple'>💾 Tác vụ chuyển dữ liệu ổ cứng "
            f"<span style='opacity:.6;font-weight:400'>(Kỹ thuật · Dữ liệu)</span></div>"
            f"<div class='ofc-ec-row'>{ec_html}</div>")
        disk_html = ""
        for d in ("G", "H", "J"):
            info = drives.get(d)
            if info:
                col = ("#ef4444" if info["pct"] >= 95
                       else ("#f59e0b" if info["pct"] >= 85 else "#22c55e"))
                disk_html += (
                    f"<div class='ofc-m'><div class='ofc-mk'>💽 Ổ {d}: "
                    f"trống {info['free']:.1f}/{info['total']:.0f} GB</div>"
                    f"<div class='ofc-pbar'><div class='ofc-pfill' "
                    f"style='width:{info['pct']}%;background:{col}'></div></div>"
                    f"<div class='ofc-pmeta'>{info['pct']}% đã dùng</div></div>")
            else:
                disk_html += metric(f"💽 Ổ {d}", "⏳ đang cập nhật")

        # ----- bảng token chi tiết -----
        tok_rows = ""
        for key, char, name, color, _, _ in AGENTS:
            t = tok.get(key, {})
            d1 = t.get(1, {"tokens": 0, "calls": 0, "cost": 0})
            d7 = t.get(7, {"tokens": 0, "calls": 0, "cost": 0})
            d30 = t.get(30, {"tokens": 0, "calls": 0, "cost": 0})
            tok_rows += (
                f"<tr><td><span class='ofc-dot' style='background:{color}'></span>"
                f"{char} {html.escape(name)}</td>"
                f"<td>{fmt_num(d1['tokens'])}</td>"
                f"<td>{fmt_num(d7['tokens'])}</td>"
                f"<td>{fmt_num(d30['tokens'])}</td>"
                f"<td>~${d30['cost']:.4f}</td></tr>")
        tok_table = (
            "<table class='ofc-table'><tr><th>AI</th><th>Hôm nay</th>"
            "<th>7 ngày</th><th>30 ngày</th><th>Chi phí 30d</th></tr>"
            f"{tok_rows}</table>")

        # ----- phòng ban (compact) -----
        def desk(key, char, name, color, role, lead=False):
            status, status_cls = agent_status.get(key, ("🟢 SẴN SÀNG", "ready"))
            perf_d = agent_perf.get(key, {})
            health_tip = perf_d.get("health_msg", "")
            tip_attr = f" title='{html.escape(health_tip)}'" if health_tip else ""
            badge = f"<span class='ofc-badge ofc-{status_cls}'{tip_attr}>{status}</span>"
            cls = status_cls
            lead_tag = ("<div class='ofc-lead-tag'>👔 TRƯỞNG PHÒNG</div>"
                        if lead else "")
            lead_cls = " ofc-lead" if lead else ""
            # token + chi phí
            _tk = tok.get(key, {})
            _t30 = _tk.get(30, {})
            _tok_html = ""
            if _t30.get("tokens"):
                _c = _t30.get("cost", 0)
                _cost_txt = f" · 💵${_c:.4f}" if _c else ""
                _tok_html = (f"<div class='ofc-tok'>🎟️ {fmt_num(_t30['tokens'])}"
                             f"{_cost_txt}</div>")
            return (
                f"<a class='ofc-desk {cls}{lead_cls}' href='/chat'>"
                f"{lead_tag}"
                f"<div class='ofc-char sm'>{char}</div>"
                f"<div class='ofc-name' style='border-color:{color}'>"
                f"<span class='ofc-dot' style='background:{color}'></span>"
                f"{html.escape(name)}</div>"
                f"<div class='ofc-role'>{role}</div>"
                f"<div class='ofc-speech'>💬 “{html.escape(_speech2(key, name))}”</div>"
                f"{_pend_txt2(key, name)}{badge}{_tok_html}</a>")

        sections = []

        def section(icon, name, live, metrics_html, desks):
            tag = ("<span class='ofc-live-tag'>✅ Data thật</span>" if live
                   else "<span class='ofc-soon-tag'>⏳ Sắp có data</span>")
            buff = DEPT_BUFF.get(name, "")
            n_staff = len(desks)
            sections.append(
                f"<div class='ofc-dept'>"
                f"<div class='ofc-dept-head'><span class='ofc-dept-title'>{icon} {name}</span>{tag}</div>"
                f"<div class='ofc-dept-sub'><span class='ofc-buff'>{buff}</span>"
                f"<span class='ofc-mgr'>👥 {n_staff}/15 nhân viên</span></div>"
                f"<div class='ofc-metrics'>{metrics_html}</div>"
                f"<div class='ofc-desks'>{''.join(desks)}</div></div>")

        section("🏦", "Kế Toán & Tài Chính", bool(fin), m_ketoan, [
            desk("gemini", "🦊", "Gemini", C["gemini"], "📊 Phân tích chi tiêu", lead=True),
            desk("deepseek", "🦈", "DeepSeek", C["deepseek"], "🔍 Soi công nợ"),
            desk("lmstudio", "🐸", "LM Studio", C["lmstudio"], "⚡ Trả lời nhanh")])
        section("💾", "Kỹ Thuật & Lưu Trữ", False, soon(), [
            desk("lmstudio", "🖥️", "LM Studio", C["lmstudio"], "💽 Giám sát ổ cứng", lead=True),
            desk("lmstudio", "🗜️", "LM Studio", C["lmstudio"], "🗂️ Phân loại & dọn file")])
        section("🎬", "Hậu Kỳ & Sản Xuất", False, soon(), [
            desk("gemini", "🎞️", "Gemini", C["gemini"], "✂️ Dựng & cắt ghép", lead=True)])
        section("📱", "App & Mobile", False, soon(), [
            desk("muse", "📲", "Muse", C["muse"], "🔨 Build & test app", lead=True),
            desk("deepseek", "🐞", "DeepSeek", C["deepseek"], "🔍 Soi bug & review code")])
        section("📣", "Social & Marketing", False, soon(), [
            desk("gemini", "📢", "Gemini", C["gemini"], "✍️ Viết bài social", lead=True)])
        section("📅", "Lịch & Điều Hành", bool(fin) or n_events is not None, m_lich, [
            desk("muse", "🗓️", "Muse", C["muse"], "🎬 Điều phối show", lead=True),
            desk("gemini", "⏰", "Gemini", C["gemini"], "📅 Theo dõi lịch"),
            desk("deepseek", "👷", "DeepSeek", C["deepseek"], "💵 Crew & lương")])
        section("🗄️", "Phòng Dữ Liệu", drive_n is not None, m_data, [
            desk("muse", "🗂️", "Muse", C["muse"], "📁 Kho Drive: index & tìm kiếm", lead=True),
            desk("muse", "🔄", "Muse", C["muse"], "🔗 Đồng bộ Finance↔Drive↔Orchestra"),
            desk("deepseek", "📈", "DeepSeek", C["deepseek"], "📊 Phân tích & báo cáo"),
            desk("lmstudio", "🛡️", "LM Studio", C["lmstudio"], "💾 Backup & an toàn")])

        # ----- dải tổng quan -----
        strip = "".join(metric(k, fin[k]) for k in list(fin)[:4] if k in fin)
        if n_events is not None:
            strip += metric("📅 Sự kiện 7 ngày", str(n_events))
        strip += metric("🎟️ Token hôm nay",
                        f"{fmt_num(total_tok)} token · ~${total_cost:.4f}")
        # ----- chỉ số công ty -----
        rep = rpg.get("company", {}).get("reputation", 82)
        done_s = fin.get("Hoàn thành tháng này", "?")
        company_html = (
            f"<div class='ofc-sec-title'>🏢 Chỉ số công ty</div>"
            f"<div class='ofc-strip' style='grid-template-columns:repeat(4,1fr)'>"
            f"{metric('💰 Doanh thu tháng', fin.get('Doanh thu tháng này', '?'))}"
            f"{metric('⭐ Uy tín', str(rep) + '/100')}"
            f"{metric('✅ Tỷ lệ thành công TB', str(avg_ok) + '%')}"
            f"{metric('📊 Dự án xong tháng này', str(done_s))}"
            f"</div>")
        if not strip:
            strip = soon()

        css = """
        .ofc-live{color:#3ddc84;font-weight:600;}
        .ofc-strip{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px;}
        .ofc-hero-row{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;
          margin-bottom:14px;padding:16px;border-radius:18px;
          background:linear-gradient(180deg,rgba(74,168,255,.07),rgba(74,168,255,.02)),
          repeating-linear-gradient(0deg,transparent,transparent 39px,rgba(255,255,255,.025) 40px),
          repeating-linear-gradient(90deg,transparent,transparent 39px,rgba(255,255,255,.025) 40px);
          border:1px solid var(--border);}
        .ofc-hero{background:rgba(255,255,255,.03);border:1px solid var(--border);
          border-radius:14px;padding:12px 6px;text-align:center;text-decoration:none;color:inherit;
          display:block;transition:transform .2s;}
        .ofc-hero:hover{transform:translateY(-4px);}
        .ofc-char{font-size:58px;line-height:1.25;animation:ofc-float 3.2s ease-in-out infinite;
          filter:drop-shadow(0 4px 8px rgba(0,0,0,.45));}
        .ofc-char.sm{font-size:40px;}
        .ofc-hero.busy .ofc-char,.ofc-desk.busy .ofc-char{animation:ofc-pulse 1.4s infinite;}
        .ofc-hero:hover .ofc-char,.ofc-desk:hover .ofc-char{animation:ofc-bounce .6s;}
        .ofc-table{height:10px;margin:2px 14px 0;border-radius:5px;
          background:linear-gradient(180deg,#8b5e34,#6b4423);
          box-shadow:0 2px 4px rgba(0,0,0,.4);}
        .ofc-name{font-weight:700;margin-top:8px;padding:4px;border-bottom:3px solid;
          border-radius:6px;font-size:14px;}
        .ofc-persona{font-size:11px;color:var(--muted);font-style:italic;margin-top:4px;}
        .ofc-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;}
        .ofc-role{font-size:12px;color:var(--muted);margin:6px 0;}
        .ofc-badge{display:inline-block;font-size:11px;font-weight:700;padding:3px 10px;border-radius:20px;}
        .ofc-busy{background:#123f2b;color:#3ddc84;}
        .ofc-ready{background:#123f2b;color:#3ddc84;}
        .ofc-error{background:#4a1515;color:#ff6b6b;}
        .ofc-stale{background:#3a3f47;color:#9aa0a6;}
        .ofc-hero.ready .ofc-char{animation:ofc-float 3.2s ease-in-out infinite;}
        .ofc-hero.busy .ofc-char{animation:ofc-pulse 1.4s infinite;}
        .ofc-hero.error .ofc-char{animation:ofc-shake .5s infinite;}
        .ofc-rank{font-size:11px;font-weight:800;color:#ffd43b;margin-top:6px;}
        .ofc-perm{font-size:10px;color:var(--muted);font-style:italic;}
        .ofc-perf{font-size:11px;color:var(--muted);margin-top:6px;}
        .ofc-promo{font-size:10px;color:#8ab4f8;margin-top:4px;cursor:help;}
        .ofc-idle{background:#3a3f47;color:#9aa0a6;}
        .ofc-tok{font-size:11px;color:var(--muted);margin-top:6px;}
        .ofc-dept{background:var(--card);border:1px solid var(--border);border-radius:16px;
          padding:12px;margin-bottom:12px;}
        .ofc-dept-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;}
        .ofc-dept-title{font-size:16px;font-weight:700;}
        .ofc-live-tag{font-size:11px;font-weight:700;color:#3ddc84;background:#123f2b;
          padding:3px 10px;border-radius:20px;white-space:nowrap;}
        .ofc-soon-tag{font-size:11px;font-weight:700;color:#9aa0a6;background:#3a3f47;
          padding:3px 10px;border-radius:20px;white-space:nowrap;}
        .ofc-metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:10px;}
        .ofc-m{background:rgba(255,255,255,.03);border:1px solid var(--border);
          border-radius:10px;padding:6px 10px;}
        .ofc-m-wide{grid-column:span 2;}
        .ofc-mk{font-size:11px;color:var(--muted);}
        .ofc-mv{font-size:14px;font-weight:700;}
        .ofc-desks{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;}
        .ofc-desk{background:rgba(255,255,255,.03);border:1px solid var(--border);
          border-radius:12px;padding:10px 6px;text-align:center;text-decoration:none;color:inherit;
          display:block;transition:transform .2s;}
        .ofc-desk:hover{transform:translateY(-3px);}
        .ofc-sync{font-size:12px;font-weight:700;margin-right:8px;white-space:nowrap;}
        .ofc-sync.ok{color:#3ddc84;}
        .ofc-lead-tag{font-size:10px;font-weight:800;color:#1a1a2e;background:#ffd43b;
          padding:2px 8px;border-radius:12px;margin-bottom:4px;display:inline-block;}
        .ofc-desk.ofc-lead{border:2px solid #ffd43b;}
        .ofc-org{background:var(--card);border:1px solid var(--border);border-radius:16px;
          padding:16px;margin-bottom:12px;text-align:center;}
        .ofc-org-top{display:inline-block;padding:8px 20px;}
        .ofc-org-char{font-size:40px;}
        .ofc-org-char.big{font-size:52px;animation:ofc-float 3s ease-in-out infinite;}
        .ofc-org-name{font-weight:800;font-size:14px;border-bottom:3px solid #4aa8ff;
          display:inline-block;padding:2px 10px;border-radius:6px;}
        .ofc-org-dept{font-size:11px;color:var(--muted);margin-top:2px;}
        .ofc-org-arrow{font-size:20px;margin:2px 0;color:var(--muted);}
        .ofc-org-row{display:flex;justify-content:center;gap:8px;flex-wrap:wrap;margin-top:6px;}
        .ofc-org-node{background:rgba(255,255,255,.03);border:1px solid var(--border);
          border-radius:10px;padding:8px 10px;min-width:86px;}
        .ofc-org-node .ofc-org-char{font-size:30px;}
        .ofc-flow{background:#123f2b;border:1px solid #3ddc84;color:#d3f9d8;
          border-radius:12px;padding:10px 14px;margin-bottom:12px;font-size:13px;}
        .ofc-flow-idle{background:rgba(255,255,255,.03);border-color:var(--border);color:var(--muted);}
        .ofc-flow a{color:#4aa8ff;font-weight:700;}
        .ofc-sticky{position:sticky;top:0;z-index:900;background:rgba(13,17,23,.95);
          backdrop-filter:blur(8px);border:1px solid var(--border);border-radius:12px;
          padding:8px 14px;margin-bottom:12px;display:flex;align-items:center;
          gap:8px;flex-wrap:wrap;font-size:12px;}
        .ofc-stk-label{font-weight:800;color:var(--muted);}
        .ofc-stk-sep{color:var(--border);}
        .ofc-stk-item{font-weight:700;padding:3px 10px;border-radius:16px;
          background:#3a3f47;color:#9aa0a6;white-space:nowrap;}
        .ofc-stk-item.done{background:#123f2b;color:#3ddc84;}
        .ofc-stk-item.run{background:#4a3208;color:#fbbf24;animation:ofc-pulse 1.6s infinite;}
        .ofc-stk-item.idle{background:#3a3f47;color:#9aa0a6;}
        .ofc-kb{font-size:10px;color:#8ab4f8;margin-top:4px;}
        .ofc-kb-link{font-size:13px;margin-top:4px;}
        .ofc-kb-link a{color:#4aa8ff;}
        .ofc-reload{background:#1a3a5c;border:1px solid #4aa8ff;color:#8ab4f8;
          border-radius:16px;padding:2px 10px;font-size:11px;font-weight:700;cursor:pointer;}
        .ofc-reload:hover{background:#4aa8ff;color:#fff;}
        /* bong bóng chat nổi */
        .ofc-bubble{position:fixed;right:18px;bottom:18px;width:60px;height:60px;
          border-radius:50%;background:linear-gradient(135deg,#4aa8ff,#b07bff);
          font-size:30px;display:flex;align-items:center;justify-content:center;
          cursor:pointer;box-shadow:0 4px 16px rgba(0,0,0,.5);z-index:999;
          border:none;transition:transform .2s;}
        .ofc-bubble:hover{transform:scale(1.1);}
        .ofc-bubble .ofc-unread{position:absolute;top:-4px;right:-4px;background:#ff6b6b;
          color:#fff;font-size:11px;font-weight:800;min-width:20px;height:20px;
          border-radius:10px;display:none;align-items:center;justify-content:center;}
        .ofc-panel{position:fixed;right:18px;bottom:90px;width:360px;max-width:calc(100vw - 36px);
          height:480px;max-height:calc(100vh - 120px);background:var(--card);
          border:1px solid var(--border);border-radius:16px;z-index:999;
          display:none;flex-direction:column;overflow:hidden;
          box-shadow:0 8px 32px rgba(0,0,0,.6);}
        .ofc-panel.open{display:flex;}
        .ofc-panel-head{padding:10px 12px;border-bottom:1px solid var(--border);
          display:flex;align-items:center;gap:8px;}
        .ofc-panel-head select{flex:1;background:var(--bg3);color:inherit;
          border:1px solid var(--border);border-radius:8px;padding:6px;font-size:13px;}
        .ofc-panel-head button{background:none;border:none;color:var(--muted);
          font-size:18px;cursor:pointer;}
        .ofc-panel-msgs{flex:1;overflow-y:auto;padding:10px;display:flex;
          flex-direction:column;gap:8px;}
        .ofc-panel-input{display:flex;gap:6px;padding:10px;border-top:1px solid var(--border);}
        .ofc-panel-input input{flex:1;background:var(--bg3);border:1px solid var(--border);
          border-radius:20px;padding:8px 14px;color:inherit;font-size:13px;}
        .ofc-panel-input button{background:#4aa8ff;border:none;color:#fff;
          border-radius:20px;padding:8px 16px;cursor:pointer;font-weight:700;}
        .ofc-msg{max-width:85%;padding:7px 11px;border-radius:14px;font-size:13px;line-height:1.45;}
        .ofc-msg.user{align-self:flex-end;background:#4aa8ff;color:#fff;
          border-bottom-right-radius:4px;}
        .ofc-msg.ai{align-self:flex-start;background:#161b22;
          border:1px solid var(--border);border-bottom-left-radius:4px;}
        .ofc-msg .ofc-who{display:flex;align-items:center;gap:4px;font-size:11px;
          font-weight:700;margin-bottom:3px;}
        .ofc-msg .ofc-time{font-size:10px;color:var(--muted);margin-top:3px;text-align:right;}
        .ofc-ec-row{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:10px;}
        .ofc-ec-card{background:rgba(255,255,255,.03);border:1px solid var(--border);
          border-radius:12px;padding:12px 8px;text-align:center;border-top:4px solid #475569;}
        .ofc-ec-card.ofc-ec-done{border-top-color:#22c55e;}
        .ofc-ec-card.ofc-ec-run{border-top-color:#f59e0b;box-shadow:0 0 14px rgba(245,158,11,.25);}
        .ofc-ec-char{font-size:46px;animation:ofc-float 3s ease-in-out infinite;}
        .ofc-ec-char.busy{animation:ofc-pulse 1.4s infinite;}
        .ofc-ec-id{font-weight:700;font-size:13px;margin-top:4px;}
        .ofc-ec-name{font-size:12px;color:var(--muted);}
        .ofc-ec-route{font-size:11px;color:var(--muted);margin:4px 0;}
        .ofc-pbar{height:8px;background:rgba(255,255,255,.08);border-radius:4px;
          overflow:hidden;margin:6px 4px;}
        .ofc-pfill{height:100%;border-radius:4px;transition:width .5s;}
        .ofc-ec-done{background:#22c55e;}
        .ofc-ec-run{background:#f59e0b;}
        .ofc-ec-wait{background:#475569;}
        .ofc-stripes{background:repeating-linear-gradient(45deg,#f59e0b,#f59e0b 8px,#d97706 8px,#d97706 16px);
          width:100%!important;animation:ofc-slide 1.2s linear infinite;}
        .ofc-pmeta{font-size:11px;color:var(--muted);}
        .ofc-table{width:100%;border-collapse:collapse;margin:4px 0 12px;font-size:13px;}
        .ofc-table th,.ofc-table td{padding:8px 10px;text-align:left;
          border-bottom:1px solid var(--border);}
        .ofc-table th{color:var(--muted);font-size:11px;text-transform:uppercase;}
        .ofc-sec-title{font-size:16px;font-weight:700;margin:14px 0 8px;}
        .ofc-chat-link{display:inline-block;margin-left:10px;font-size:13px;font-weight:600;
          color:#4aa8ff;text-decoration:none;border:1px solid #4aa8ff;border-radius:20px;
          padding:4px 14px;}
        /* (b) Pattern v2.6: vien mau trang thai + loi thoai + dai trang thai + empty + Don gian */
        .ofc-hero.busy,.ofc-desk.busy{border-left:4px solid #22c55e;}
        .ofc-hero.ready,.ofc-desk.ready{border-left:4px solid #f59e0b;}
        .ofc-hero.error,.ofc-desk.error{border-left:4px solid #ef4444;}
        .ofc-speech{font-size:11px;color:#c4d4e8;font-style:italic;margin-top:4px;}
        .ofc-pend{font-size:11px;color:#fbbf24;font-weight:700;margin-top:2px;}
        .ofc-vp-strip{display:flex;gap:10px;flex-wrap:wrap;font-size:12px;color:var(--muted);
          background:rgba(255,255,255,.03);border:1px solid var(--border);border-radius:10px;
          padding:8px 12px;margin-bottom:12px;}
        .ofc-vp-strip b{color:var(--text);}
        .ofc-empty{color:var(--muted);font-size:12px;line-height:1.5;margin-bottom:12px;}
        .ofc-empty b{color:var(--text);}
        body.ofc-simple .ofc-org,body.ofc-simple .ofc-ec-row,
        body.ofc-simple .ofc-table,body.ofc-simple .ofc-dept,
        body.ofc-simple .ofc-hide-simple{display:none !important;}
        body.ofc-simple .ofc-hero-row{grid-template-columns:repeat(3,1fr);}
        @keyframes ofc-float{0%,100%{transform:translateY(0)}50%{transform:translateY(-7px)}}
        @keyframes ofc-pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.6;transform:scale(1.08)}}
        @keyframes ofc-bounce{0%,100%{transform:translateY(0)}30%{transform:translateY(-14px)}60%{transform:translateY(0)}80%{transform:translateY(-6px)}}
        @keyframes ofc-shake{0%,100%{transform:translateX(0)}25%{transform:translateX(-4px)}75%{transform:translateX(4px)}}
        @keyframes ofc-slide{to{background-position:22px 0}}
        @media(max-width:700px){.ofc-hero-row{grid-template-columns:repeat(2,1fr);}
          .ofc-desks{grid-template-columns:repeat(2,1fr);}
          .ofc-metrics,.ofc-strip{grid-template-columns:repeat(2,1fr);}.ofc-m-wide{grid-column:span 2;}
          .ofc-ec-row{grid-template-columns:repeat(2,1fr);}}
        """
        heroes = "".join(hero(*a) for a in AGENTS)
        # ----- bong bóng chat nổi (Messenger-style) -----
        bubble_html = """
        <button class="ofc-bubble" id="ofc-bubble" title="Chat với AI">💬
          <span class="ofc-unread" id="ofc-unread">0</span></button>
        <div class="ofc-panel" id="ofc-panel">
          <div class="ofc-panel-head">
            <select id="ofc-agent">
              <option value="auto">🤖 Tự động</option>
              <option value="gemini">🔵 Gemini</option>
              <option value="muse">🟣 Muse</option>
              <option value="lmstudio">🟢 LM Studio</option>
              <option value="deepseek">🔷 DeepSeek</option>
            </select>
            <button id="ofc-min" title="Thu gọn">—</button>
          </div>
          <div class="ofc-panel-msgs" id="ofc-msgs"></div>
          <div class="ofc-panel-input">
            <input id="ofc-text" placeholder="Nhắn tin..." autocomplete="off">
            <button id="ofc-send">Gửi</button>
          </div>
        </div>"""
        bubble_js = """
<script>
(function(){
var AMETA={gemini:{label:'Gemini',icon:'🔵'},muse:{label:'Muse',icon:'🟣'},
lmstudio:{label:'LM Studio',icon:'🟢'},deepseek:{label:'DeepSeek',icon:'🔷'}};
var panel=document.getElementById('ofc-panel'),bubble=document.getElementById('ofc-bubble'),
box=document.getElementById('ofc-msgs'),inp=document.getElementById('ofc-text'),
unread=document.getElementById('ofc-unread'),lastId=null,open=false,unreadN=0;
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function renderMsg(m){
  var div=document.createElement('div');
  if(m.role==='user'){div.className='ofc-msg user';}
  else{div.className='ofc-msg ai';
    var meta=AMETA[m.agent]||{label:m.agent||'AI',icon:'🤖'};
    div.innerHTML='<div class="ofc-who"><span>'+meta.icon+'</span>'+esc(meta.label)+'</div>';}
  var c=document.createElement('div');c.innerHTML=esc(m.content).replace(/\\n/g,'<br>');
  div.appendChild(c);
  var t=document.createElement('div');t.className='ofc-time';
  t.textContent=(m.created_at||'').slice(11,16);div.appendChild(t);
  div.dataset.id=m.id;return div;
}
async function loadH(){
  try{var r=await fetch('/api/chat/history');var d=await r.json();
    box.innerHTML='';(d.messages||[]).forEach(function(m){box.appendChild(renderMsg(m));lastId=m.id;});
    box.scrollTop=box.scrollHeight;}catch(e){}
}
async function send(){
  var text=inp.value.trim();if(!text)return;
  var agent=document.getElementById('ofc-agent').value;inp.value='';
  box.appendChild(renderMsg({id:'tmp'+Date.now(),role:'user',content:text,created_at:new Date().toISOString()}));
  box.scrollTop=box.scrollHeight;
  try{var r=await fetch('/api/chat/send',{method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:'msg='+encodeURIComponent(text)+'&agent='+encodeURIComponent(agent)+'&csrf='+encodeURIComponent(OFC_CSRF)});
    var d=await r.json();
    if(d.message){box.appendChild(renderMsg(d.message));lastId=d.message.id;box.scrollTop=box.scrollHeight;}
    else if(d.error){box.appendChild(renderMsg({id:'e',role:'ai',agent:'muse',content:'⚠️ '+d.error,created_at:''}));}
  }catch(e){}
}
function toggle(force){
  open=(typeof force==='boolean')?force:!open;
  panel.classList.toggle('open',open);
  if(open){unreadN=0;unread.style.display='none';loadH();box.scrollTop=box.scrollHeight;}
}
bubble.onclick=function(){toggle();};
document.getElementById('ofc-min').onclick=function(){toggle(false);};
document.getElementById('ofc-send').onclick=send;
inp.addEventListener('keydown',function(e){if(e.key==='Enter')send();});
setInterval(async function(){
  if(open||!lastId&&false)return;
  try{var r=await fetch('/api/chat/history');var d=await r.json();
    var msgs=d.messages||[];if(!msgs.length)return;
    var last=msgs[msgs.length-1];
    if(last.id!==lastId){
      if(open){var seen={};Array.prototype.forEach.call(box.children,function(c){seen[c.dataset.id]=1;});
        msgs.forEach(function(m){if(!seen[m.id])box.appendChild(renderMsg(m));});
        lastId=last.id;box.scrollTop=box.scrollHeight;}
      else if(last.role!=='user'){unreadN++;unread.textContent=unreadN>9?'9+':unreadN;unread.style.display='flex';lastId=last.id;}
      else{lastId=last.id;}
    }}catch(e){}
},5000);
loadH();
})();
</script>"""
        # ----- sơ đồ tổ chức -----
        org_leads = [
            ("🦊", "Gemini", C["gemini"], "Kế Toán"),
            ("🐸", "LM Studio", C["lmstudio"], "Kỹ Thuật"),
            ("🦊", "Gemini", C["gemini"], "Hậu Kỳ"),
            ("🦉", "Muse", C["muse"], "App"),
            ("🦊", "Gemini", C["gemini"], "Social"),
            ("🦉", "Muse", C["muse"], "Lịch"),
            ("🦉", "Muse", C["muse"], "Dữ Liệu"),
        ]
        org_html = "".join(
            f"<div class='ofc-org-node'><div class='ofc-org-char'>{ch}</div>"
            f"<div class='ofc-org-name' style='border-color:{col}'>{nm}</div>"
            f"<div class='ofc-org-dept'>{dp}</div></div>"
            for ch, nm, col, dp in org_leads)
        # ----- luồng công việc đang chạy -----
        busy_agents = [a for a in AGENTS if inflight.get(a[0], 0) > 0]
        if busy_agents:
            flow_items = " → ".join(
                f"{a[1]} {a[2]}" for a in busy_agents)
            flow_html = (
                f"<div class='ofc-flow'>🔄 <b>Luồng đang chạy:</b> "
                f"👑 Anh → 🦉 Muse (Giám đốc) → {flow_items} đang xử lý</div>")
        else:
            flow_html = ("<div class='ofc-flow ofc-flow-idle'>💤 Chưa có việc đang chạy — "
                         "giao việc ở <a href='/new'>➕ Giao việc</a> hoặc nhắn "
                         "<a href='/chat'>💬 Chat AI</a></div>")
        # ----- dải tiến trình sticky (luôn hiển thị) -----
        # EC shuffle mini: B1–B4 xong (23/10/2026)
        ec_mini = (
            "<span class='ofc-stk-item done'>✅ B1</span>"
            "<span class='ofc-stk-item done'>✅ B2</span>"
            "<span class='ofc-stk-item done'>✅ B3</span>"
            "<span class='ofc-stk-item done'>✅ B4</span>")
        busy_mini = "".join(
            f"<span class='ofc-stk-item run'>{a[1]} {a[2]}</span>"
            for a in AGENTS if inflight.get(a[0], 0) > 0)
        if not busy_mini:
            busy_mini = "<span class='ofc-stk-item idle'>💤 Tất cả rảnh</span>"
        sticky_html = (
            f"<div class='ofc-sticky'>"
            f"<span class='ofc-stk-label'>🔀 EC:</span>{ec_mini}"
            f"<span class='ofc-stk-sep'>|</span>"
            f"<span class='ofc-stk-label'>👥 Agent:</span>{busy_mini}"
            f"</div>")
        # ----- khu kho kiến thức chung -----
        kb_html = (
            f"<div class='ofc-dept'><div class='ofc-dept-head'>"
            f"<span class='ofc-dept-title'>📚 Kho Kiến Thức Chung</span>"
            f"<span class='ofc-live-tag'>✅ Agent đã học chung</span></div>"
            f"<div class='ofc-metrics'>"
            f"{metric('🧠 Skills', str(kb_status['skills']))}"
            f"{metric('📜 Quy tắc studio', str(kb_status['rules']))}"
            f"{metric('💰 Finance', '✅ đã nối' if fin else '❌ chưa nối')}"
            f"<div class='ofc-m'><div class='ofc-mk'>🕐 Học lúc</div>"
            f"<div class='ofc-mv'>{html.escape(kb_status['loaded_at'])} "
            f"<button class='ofc-reload' id='ofc-kb-reload' "
            f"title='Cho học lại ngay'>🔄 Học lại</button></div></div>"
            f"</div>"
            f"<div class='ofc-kb-link'>📂 <a href='{kb_link}' target='_blank'>"
            f"Mở folder Kho Kiến Thức AI trên Drive</a></div>"
            f"</div>")
        # ----- chờ sếp duyệt -----
        if pending_approvals:
            appr_items = "".join(
                f"<div class='ofc-appr-item'><div class='ofc-appr-task'>"
                f"📋 {html.escape(a['task'])}</div>"
                f"<div class='ofc-appr-meta'>từ {html.escape(a['from'])} · "
                f"<button class='ofc-approve' data-f='{html.escape(a['file'])}'>"
                f"✅ Duyệt</button></div></div>"
                for a in pending_approvals[:5])
            appr_html = (
                f"<div class='ofc-sec-title'>✍️ Chờ sếp duyệt "
                f"({len(pending_approvals)})</div>"
                f"<div class='ofc-appr'>{appr_items}</div>")
        else:
            appr_html = (
                "<div class='ofc-empty'>Chưa có việc đang chờ.<br>"
                "<b>Làm gì tiếp:</b> giao việc mới ở <b>/new</b> để vòng "
                "chat có chủ đề bàn.</div>")
        try:
            board_html = _build_board_html(TASK_BUS_DIR) if _build_board_html else ""
        except Exception:
            board_html = ("<div class='ofc-sec-title'>📋 Bảng tiến trình tổng</div>"
                          "<div style='color:#f59e0b;font-size:13px'>"
                          "Không tải được bảng (lỗi kỹ thuật, trang vẫn chạy bình thường).</div>")
        body = (f"<style>{css}</style>"
                f"{sticky_html}"
                f"<h1>🏢 Văn Phòng AI <span class='ofc-live'>● LIVE</span>"
                f" <span style='font-size:12px;color:#7cc4ff;border:1px solid #2a3d5c;border-radius:12px;padding:2px 10px'>"
                f"v{ORCHESTRA_VERSION} · {ORCHESTRA_VERSION_DATE}</span>"
                f"<a class='ofc-chat-link' href='/chat'>💬 Vào Chat AI</a>"
                f"<button id='ofc-simple-toggle' class='ofc-chat-link' "
                f"style='background:none;cursor:pointer'>📋 Đơn giản</button></h1>"
                f"<p class='muted'>5 nhân vật AI thật của Orchestra · mỗi người 1 tính cách + nhiệm vụ · "
                f"bấm vào nhân vật để nhắn tin · tự refresh 30s</p>"
                f"{vp_strip2}"
                f"<div class='ofc-strip'>{strip}</div>"
                f"{company_html}"
                f"{appr_html}"
                f"<div class='ofc-hero-row'>{heroes}</div>"
                f"<div class='ofc-sec-title ofc-hide-simple'>🏛️ Sơ đồ tổ chức</div>"
                f"<div class='ofc-org'>"
                f"<div class='ofc-org-top'><div class='ofc-org-char big'>👑</div>"
                f"<div class='ofc-org-name'>Anh Thống</div>"
                f"<div class='ofc-org-dept'>Chủ tịch — giao việc</div></div>"
                f"<div class='ofc-org-arrow'>⬇</div>"
                f"<div class='ofc-org-top'><div class='ofc-org-char big'>🦉</div>"
                f"<div class='ofc-org-name' style='border-color:{C['muse']}'>Muse</div>"
                f"<div class='ofc-org-dept'>GIÁM ĐỐC — điều phối chung</div></div>"
                f"<div class='ofc-org-arrow'>⬇ chia về 7 phòng ban</div>"
                f"<div class='ofc-org-row'>{org_html}</div>"
                f"</div>"
                f"{flow_html}"
                f"{ec_section}"
                f"{board_html}"
                f"<div class='ofc-metrics' style='grid-template-columns:repeat(3,1fr)'>{disk_html}</div>"
                f"<div class='ofc-sec-title ofc-hide-simple'>🎟️ Token chi tiết theo AI</div>"
                f"{tok_table}"
                f"{kb_html}"
                f"{''.join(sections)}"
                f"{bubble_html}"
                f"<script>setTimeout(()=>location.reload(),30000)</script>"
                f"<script>const OFC_CSRF={csrf!r};</script>"
                f"<script>"
                f"document.getElementById('ofc-kb-reload').onclick=async function(){{"
                f"this.textContent='⏳ Đang học...';"
                f"try{{var r=await fetch('/api/kb/reload');var d=await r.json();"
                f"if(d.ok){{this.textContent='✅ Đã học '+d.skills+' skills';location.reload();}}"
                f"else{{this.textContent='❌ Lỗi';}}}}catch(e){{this.textContent='❌ Lỗi';}}"
                f"}};"
                f"document.querySelectorAll('.ofc-approve').forEach(function(btn){{"
                f"btn.onclick=async function(){{"
                f"btn.textContent='⏳...';"
                f"try{{var r=await fetch('/api/kb/approve?f='+encodeURIComponent(btn.dataset.f));"
                f"var d=await r.json();"
                f"if(d.ok){{btn.textContent='✅ Đã duyệt';btn.disabled=true;location.reload();}}"
                f"else{{btn.textContent='❌ Lỗi';}}}}catch(e){{btn.textContent='❌ Lỗi';}}"
                f"}};}});"
                f"(function(){{var b=document.getElementById('ofc-simple-toggle');"
                f"if(!b)return;function setS(on){{document.body.classList.toggle('ofc-simple',!!on);"
                f"b.textContent=on?'🌐 Đầy đủ':'📋 Đơn giản';"
                f"try{{localStorage.setItem('ofc-view',on?'simple':'full');}}catch(e){{}}}}"
                f"var s=null;try{{s=localStorage.getItem('ofc-view');}}catch(e){{}}"
                f"if(s==='simple')setS(true);"
                f"b.onclick=function(){{setS(!document.body.classList.contains('ofc-simple'));}};}})();</script>"
                f"{bubble_js}")
        self._send(page("Văn Phòng AI", body, active="/ai-office", user=user))

    def _skills_page(self, user, q, name):
        if name:
            s = get_skill(name)
            if not s:
                self._send(page("Skill", "<h1>Không tìm thấy skill</h1>", user=user), 404)
                return
            body = (f"<p><a href='/skills' style='color:var(--accent)'>← Skills</a></p>"
                    f"<h1>🧠 {html.escape(s['title'])}</h1>"
                    f"<p class='muted'>{html.escape(s['name'])} · {s['size_kb']} KB</p>"
                    f"<p>{html.escape(s['summary'])}</p>"
                    f"<h2>Nội dung</h2><pre>{html.escape(s['content'][:8000])}</pre>")
            self._send(page(s["title"], body, active="/skills", user=user))
            return

        skills = load_all_skills()
        if q:
            results = search_skills(q, 20)
        else:
            results = list(skills.values())[:30]
        items = "".join(
            f"<div class='card'><h3><a href='/skills?name={s['name']}' style='color:var(--text)'>"
            f"{html.escape(s['title'])}</a></h3>"
            f"<p class='muted' style='font-size:.85rem'>{html.escape(s['summary'][:150])}...</p>"
            f"<span class='agent-tag'>{html.escape(s['name'])}</span></div>"
            for s in results)
        self._send(page("Skills",
            f"<h1>🧠 Skills ({len(skills)})</h1>"
            f"<div class='card'><form method='get' action='/skills'>"
            f"<input type='text' name='q' placeholder='Tìm skill... (vd: pdf, trading, video)' "
            f"value='{html.escape(q)}' style='margin-bottom:0'>"
            f"<button type='submit' style='margin-top:8px'>🔍 Tìm</button></form></div>"
            f"<div class='grid cols2' style='margin-top:12px'>{items}</div>",
            active="/skills", user=user))

    def _settings_html(self, user=""):
        agents_html = ""
        try:
            from agents import list_agents, get_agent
            for aname in list_agents():
                try:
                    ok = get_agent(aname).check()
                except Exception:
                    ok = False
                agents_html += (f"<div class='tbar'><span class='lbl'>🤖 {html.escape(aname)}</span>"
                                f"<span class='badge {'done' if ok else 'failed'}'>"
                                f"{'🟢 Sẵn sàng' if ok else '🔴 Chưa sẵn sàng'}</span></div>")
        except Exception as e:
            agents_html = f"<p class='muted'>Lỗi: {html.escape(str(e))}</p>"

        n_skills = len(load_all_skills())
        warn = os.environ.get("TOKEN_WARN_THRESHOLD", "100000")
        return (
            f"<h2>Trạng thái AI</h2><div class='card'>{agents_html}</div>"
            f"<h2>Skills</h2><div class='card'><p>🧠 <b>{n_skills}</b> skills đã index. "
            f"<a href='/skills' style='color:var(--accent)'>Xem danh sách →</a></p></div>"
            f"<h2>Token</h2><div class='card'>"
            f"<p class='muted'>Ngưỡng cảnh báo token/task: <b>{warn}</b><br>"
            f"Đổi bằng env <code>TOKEN_WARN_THRESHOLD</code> khi chạy server.</p></div>"
            f"<h2>Tài khoản</h2><div class='card'><p>👤 <b>{html.escape(user)}</b></p>"
            f"<a class='btn btn-ghost' href='/logout' style='width:auto'>Đăng xuất</a></div>")

    def _settings(self, user):
        self._send(page("Cài đặt",
            f"<h1>⚙️ Cài đặt</h1>" + self._settings_html(user),
            active="/settings", user=user))

    # ============ POST ============
    # ---------- Voice: noi bang giong noi tieng Viet -> task-bus ----------
    def _voice_page(self, user):
        csrf = self._csrf_token() or ""
        dests = _voice_dests()
        friendly = {
            "main": "Main chat (tự điều phối sang side chat đúng)",
            "ky-thuat-luu-tru": "Kỹ thuật – Lưu trữ",
            "app-desktop-mobile": "App Desktop & Mobile",
        }
        opts = ""
        for d in dests:
            label = friendly.get(d, d)
            sel = " selected" if d == "main" else ""
            opts += f"<option value=\"{html.escape(d)}\"{sel}>{html.escape(label)}</option>"
        body = """
<h1>🎤 Voice — nói chuyện bằng giọng nói <span class="badge running">Voice __VOICE_VERSION__ · __VOICE_DATE__</span></h1>
<p class="muted">Bấm mic, nói tiếng Việt, gửi qua task-bus tới Main chat rồi sang side chat. Khi có trả lời, trang sẽ tự đọc lại bằng giọng nói. Đây là gửi nhận bất đồng bộ (thường vài phút), không phải hội thoại live.</p>
<div class="card">
<p id="voice-support" class="muted">⏳ Đang kiểm tra trình duyệt...</p>
<button id="voice-mic" class="btn">🎤 Bấm để nói</button>
<label>Nội dung (nói hoặc gõ tay)</label>
<textarea id="voice-text" placeholder="Bấm mic rồi nói tiếng Việt... hoặc gõ tay vào đây"></textarea>
<label>Gửi tới</label>
<select id="voice-dest">__DEST_OPTIONS__</select>
<button id="voice-send" class="btn">📤 Gửi qua task-bus</button>
<div id="voice-status" style="margin-top:12px"></div>
</div>
<div id="voice-result-box" class="card" style="display:none;margin-top:12px">
<h3>✅ Trả lời</h3>
<div id="voice-result"></div>
<button id="voice-replay" class="btn btn-ghost">🔊 Nghe lại</button>
</div>
<div class="card" style="margin-top:12px">
<h3>📱 Dùng với Siri</h3>
<p class="muted">Đã có endpoint cho iOS Shortcut: POST /api/voice-task (kèm token X-Voice-Token). Hướng dẫn tạo Shortcut + giới hạn v1 xem trong file VOICE.md của app và báo cáo. Lưu ý: điện thoại cần tới được app qua tunnel — bản Railway không nối task-bus của VM nên không dùng cho cầu này.</p>
<h3>⚙️ Cách hoạt động</h3>
<p class="muted">Voice → task-bus pending (from=orchestra-voice, kèm voice_id) → watcher 5 phút báo Main chat → Main chat điều phối sang side chat → side chat xử lý, ghi kết quả vào trường result của file done (giữ voice_id) → trang này poll thấy done thì đọc lại bằng giọng nói tiếng Việt.</p>
</div>
<script>
const VOICE_CSRF = "__CSRF__";
const voiceText = document.getElementById('voice-text');
const voiceMic = document.getElementById('voice-mic');
const voiceSend = document.getElementById('voice-send');
const voiceStatus = document.getElementById('voice-status');
const voiceSupport = document.getElementById('voice-support');
const voiceResultBox = document.getElementById('voice-result-box');
const voiceResult = document.getElementById('voice-result');
const voiceReplay = document.getElementById('voice-replay');
let voiceRec = null;
let voiceListening = false;
let voicePollTimer = null;
let voiceCurrentId = '';
let voiceLastResult = '';
function voiceSetStatus(html){ voiceStatus.innerHTML = html; }
function voiceSpeak(text){
  voiceLastResult = text;
  if(!('speechSynthesis' in window)){ return false; }
  try{
    window.speechSynthesis.cancel();
    var u = new SpeechSynthesisUtterance(text);
    u.lang = 'vi-VN';
    u.rate = 1;
    window.speechSynthesis.speak(u);
    return true;
  }catch(e){ return false; }
}
voiceReplay.onclick = function(){ if(voiceLastResult){ voiceSpeak(voiceLastResult); } };
var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
if(SR){
  voiceSupport.textContent = '✅ Trình duyệt hỗ trợ nhận giọng nói tiếng Việt (Web Speech API).';
  voiceMic.onclick = function(){
    if(voiceListening){ try{ voiceRec.stop(); }catch(e){} return; }
    voiceRec = new SR();
    voiceRec.lang = 'vi-VN';
    voiceRec.interimResults = true;
    voiceRec.continuous = false;
    voiceRec.onresult = function(ev){
      var finalText = '';
      var interimText = '';
      for(var i = ev.resultIndex; i < ev.results.length; i++){
        if(ev.results[i].isFinal){ finalText += ev.results[i][0].transcript; }
        else { interimText += ev.results[i][0].transcript; }
      }
      if(finalText){ voiceText.value = (voiceText.value + ' ' + finalText).trim(); }
      if(interimText){ voiceSupport.textContent = '🎧 Đang nghe: ' + interimText; }
    };
    voiceRec.onend = function(){
      voiceListening = false;
      voiceMic.textContent = '🎤 Bấm để nói';
      voiceSupport.textContent = '✅ Trình duyệt hỗ trợ nhận giọng nói tiếng Việt (Web Speech API).';
    };
    voiceRec.onerror = function(ev){
      voiceListening = false;
      voiceMic.textContent = '🎤 Bấm để nói';
      voiceSupport.textContent = '⚠️ Lỗi nhận giọng nói: ' + (ev.error || 'không rõ') + '. Hãy gõ tay rồi gửi.';
    };
    try{
      voiceRec.start();
      voiceListening = true;
      voiceMic.textContent = '⏹️ Bấm để dừng';
      voiceSupport.textContent = '🎧 Đang nghe... nói tiếng Việt đi anh.';
    }catch(e){
      voiceSupport.textContent = '⚠️ Không mở được mic: ' + e + '. Hãy gõ tay rồi gửi.';
    }
  };
} else {
  voiceSupport.textContent = '⚠️ Trình duyệt này không hỗ trợ Web Speech API — nút mic bị tắt. Hãy dùng Chrome (Android/máy tính) hoặc Safari mới, hoặc gõ tay vào ô rồi gửi.';
  voiceMic.disabled = true;
  voiceMic.textContent = '🎤 Không hỗ trợ mic trên trình duyệt này';
}
async function voicePoll(){
  if(!voiceCurrentId) return;
  try{
    var r = await fetch('/api/voice/poll?voice_id=' + encodeURIComponent(voiceCurrentId));
    var d = await r.json();
    if(d.status === 'done'){
      clearInterval(voicePollTimer);
      voicePollTimer = null;
      var res = d.result || '(Đã xong nhưng không có nội dung trả lời.)';
      voiceResult.textContent = res;
      voiceResultBox.style.display = 'block';
      voiceSetStatus('✅ Đã có trả lời.');
      var ok = voiceSpeak(res);
      if(!ok){ voiceSetStatus('✅ Đã có trả lời. (Máy không tự đọc được — bấm 🔊 Nghe lại.)'); }
    } else if(d.status === 'working'){
      voiceSetStatus('🔄 Đang xử lý... ' + (d.detail || '') + (d.progress ? ' (' + d.progress + '%)' : ''));
    } else if(d.status === 'pending'){
      voiceSetStatus('⏳ Đang chờ Main chat điều phối...');
    } else {
      voiceSetStatus('⚠️ Không tìm thấy task ' + voiceCurrentId + '.');
      clearInterval(voicePollTimer);
      voicePollTimer = null;
    }
  }catch(e){
    voiceSetStatus('⚠️ Lỗi kiểm tra trả lời: ' + e);
  }
}
voiceSend.onclick = async function(){
  var text = voiceText.value.trim();
  var dest = document.getElementById('voice-dest').value;
  if(!text){ voiceSetStatus('⚠️ Chưa có nội dung — bấm mic nói hoặc gõ tay rồi gửi.'); return; }
  voiceSend.disabled = true;
  voiceSend.textContent = '⏳ Đang gửi...';
  try{
    var r = await fetch('/api/voice/send', {
      method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: 'text=' + encodeURIComponent(text) + '&dest=' + encodeURIComponent(dest) + '&csrf=' + encodeURIComponent(VOICE_CSRF)
    });
    var d = await r.json();
    if(d.ok){
      voiceCurrentId = d.voice_id;
      voiceResultBox.style.display = 'none';
      voiceSetStatus('✅ Đã gửi qua task-bus (voice_id: ' + d.voice_id + ', tới: ' + d.to + '). Đang chờ trả lời...');
      if(voicePollTimer){ clearInterval(voicePollTimer); }
      voicePollTimer = setInterval(voicePoll, 5000);
    } else {
      voiceSetStatus('❌ Lỗi gửi: ' + (d.error || 'không rõ'));
    }
  }catch(e){
    voiceSetStatus('❌ Lỗi gửi: ' + e);
  }
  voiceSend.disabled = false;
  voiceSend.textContent = '📤 Gửi qua task-bus';
};
</script>
"""
        body = body.replace("__CSRF__", csrf).replace("__DEST_OPTIONS__", opts).replace("__VOICE_VERSION__", VOICE_VERSION).replace("__VOICE_DATE__", VOICE_VERSION_DATE)
        self._send(page("Voice", body, active="/voice", user=user))

    def _voice_send(self, q):
        """POST /api/voice/send — trang Voice gui text qua task-bus."""
        import json as _json
        text = q.get("text", [""])[0].strip()
        dest = q.get("dest", ["main"])[0].strip() or "main"
        if not text:
            body = _json.dumps({"ok": False, "error": "Chưa có nội dung — bấm mic nói hoặc gõ tay rồi gửi."}, ensure_ascii=False).encode("utf-8")
        elif len(text) > 4000:
            body = _json.dumps({"ok": False, "error": "Nội dung quá dài (tối đa 4000 ký tự)."}, ensure_ascii=False).encode("utf-8")
        else:
            try:
                voice_id, fn, dest = _voice_write_task(text, dest, "voice-page")
                body = _json.dumps({"ok": True, "voice_id": voice_id, "file": fn, "to": dest}, ensure_ascii=False).encode("utf-8")
            except Exception as e:
                body = _json.dumps({"ok": False, "error": f"Không ghi được task-bus: {e}"}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _voice_poll(self, q):
        """GET /api/voice/poll?voice_id=... — trang Voice hoi tra loi."""
        import json as _json
        vid = q.get("voice_id", [""])[0].strip()
        if not vid or not vid.startswith("voice-"):
            body = _json.dumps({"ok": False, "error": "Thiếu voice_id."}, ensure_ascii=False).encode("utf-8")
        else:
            status, data, fn = _voice_find(vid)
            if status is None:
                body = _json.dumps({"ok": True, "status": "not_found"}, ensure_ascii=False).encode("utf-8")
            elif status == "done":
                body = _json.dumps({"ok": True, "status": "done", "result": data.get("result", ""), "file": fn}, ensure_ascii=False).encode("utf-8")
            else:
                body = _json.dumps({"ok": True, "status": status, "progress": data.get("progress"), "detail": data.get("detail", ""), "file": fn}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _voice_task_api(self, q):
        """POST /api/voice-task — iOS Shortcut (Siri) gui text qua task-bus.

        Khong can session login (Shortcut khong co cookie).
        Xac thuc bang token: header X-Voice-Token hoac ?token=,
        doi chieu env ORCHESTRA_VOICE_TOKEN (nap tu .env luc khoi dong).
        Neu chua cau hinh token -> 503 (fail closed).
        Chap nhan ca form-urlencoded va JSON {text, target|dest}.
        """
        import json as _json
        want = os.environ.get("ORCHESTRA_VOICE_TOKEN", "").strip()
        if not want:
            body = _json.dumps({"ok": False, "error": "Chưa cấu hình ORCHESTRA_VOICE_TOKEN trên server (thêm vào .env rồi restart app)."}, ensure_ascii=False).encode("utf-8")
            code = 503
        else:
            got = (self.headers.get("X-Voice-Token", "") or "").strip()
            if not got:
                got = q.get("token", [""])[0].strip()
            if not got or not secrets.compare_digest(got, want):
                body = _json.dumps({"ok": False, "error": "Token không hợp lệ."}, ensure_ascii=False).encode("utf-8")
                code = 401
            else:
                text = q.get("text", [""])[0].strip()
                dest = (q.get("target", [""])[0] or q.get("dest", [""])[0] or "main").strip() or "main"
                if not text:
                    body = _json.dumps({"ok": False, "error": "Thiếu text."}, ensure_ascii=False).encode("utf-8")
                    code = 400
                elif len(text) > 4000:
                    body = _json.dumps({"ok": False, "error": "Nội dung quá dài (tối đa 4000 ký tự)."}, ensure_ascii=False).encode("utf-8")
                    code = 400
                else:
                    try:
                        voice_id, fn, dest = _voice_write_task(text, dest, "siri")
                        print(f"  [voice-task] siri -> {dest} voice_id={voice_id} len={len(text)}")
                        msg = "Đã gửi: " + (text[:80] + ("..." if len(text) > 80 else ""))
                        body = _json.dumps({"ok": True, "voice_id": voice_id, "file": fn, "to": dest, "message": msg}, ensure_ascii=False).encode("utf-8")
                        code = 200
                    except Exception as e:
                        body = _json.dumps({"ok": False, "error": f"Không ghi được task-bus: {e}"}, ensure_ascii=False).encode("utf-8")
                        code = 500
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        u = urlparse(self.path)
        p = u.path
        n = int(self.headers.get("Content-Length", 0))
        _raw = self.rfile.read(n).decode() if n else ""
        _ctype = self.headers.get("Content-Type", "")
        if "application/json" in _ctype and _raw.strip():
            # Siri Shortcut gui JSON {text, target} — chuan hoa ve dang q giong form
            try:
                _jb = json.loads(_raw)
                if isinstance(_jb, dict):
                    q = {k: [v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)] for k, v in _jb.items()}
                else:
                    q = {}
            except Exception:
                q = {}
        else:
            q = parse_qs(_raw)

        if p == "/login":
            ip = self.client_address[0]
            if not _login_allowed(ip):
                self._send(page("Đăng nhập",
                    "<div class='login-wrap'><div class='card'>"
                    "<p style='color:var(--danger)'>Sai quá nhiều lần — IP bị khóa tạm thời 15 phút.</p>"
                    "<a class='btn' href='/login'>Thử lại sau</a></div></div>"))
                return
            user, pw = q.get("u", [""])[0], q.get("p", [""])[0]
            if verify(user, pw):
                _login_ok(ip)
                tok = make_session(user)
                self.send_response(302)
                self.send_header("Set-Cookie", f"tok={tok}; Path=/; HttpOnly; SameSite=Lax; Secure")
                self.send_header("Location", "/")
                self.end_headers()
            else:
                _login_failed(ip)
                self._send(page("Đăng nhập",
                    "<div class='login-wrap'><div class='card'>"
                    "<p style='color:var(--danger)'>Sai tài khoản/mật khẩu.</p>"
                    "<a class='btn' href='/login'>Thử lại</a></div></div>"))
            return

        # Siri endpoint: KHONG can session login (Shortcut khong co cookie).
        # Xac thuc bang token rieng (X-Voice-Token hoac ?token=) — xem _voice_task_api.
        if p == "/api/voice-task":
            self._voice_task_api(q); return

        user = self._user()
        if not user:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
            return

        # CSRF: mọi POST thay đổi trạng thái phải kèm token của session
        if p in ("/api/chat/send", "/run", "/api/voice/send"):
            if not self._csrf_ok(q):
                self._send(page("Lỗi",
                    "<div class='card'><p style='color:var(--danger)'>"
                    "CSRF token không hợp lệ — tải lại trang rồi thử lại.</p></div>",
                    user=user), 403)
                return

        if p == "/api/chat/send":
            self._api_chat_send(q); return
        if p == "/api/voice/send":
            self._voice_send(q); return
        if p == "/run":
            title = q.get("title", ["Task"])[0].strip() or "Task"
            raw = q.get("subs", [""])[0]
            subtasks = []
            for line in raw.splitlines():
                line = line.strip()
                if not line or "|" not in line:
                    continue
                kind, prompt = line.split("|", 1)
                kind, prompt = kind.strip(), prompt.strip()
                if prompt:
                    subtasks.append(SubTask(
                        id=f"st-{len(subtasks)+1}", kind=kind, prompt=prompt))
            if not subtasks:
                subtasks.append(SubTask(id="st-1", kind="quick_qa", prompt=title))

            # Ước lượng token trước khi chạy
            est = orch.estimate_task(subtasks)
            warn_html = ""
            if est["warn"]:
                warn_html = (f"<div class='warn-box'>⚠️ Task ước lượng "
                             f"<b>{fmt_num(est['est_total'])}</b> tokens, vượt ngưỡng "
                             f"{fmt_num(est['threshold'])}. Vẫn chạy?</div>")

            # Gợi ý skill cho từng subtask
            skill_html = ""
            for st in subtasks:
                sug = orch.suggest_skills_for(st, 2)
                if sug:
                    tags = "".join(f"<span class='skill-tag'>🧠 {s['name']}</span>" for s in sug)
                    skill_html += f"<p><b>{html.escape(st.kind)}</b>: {tags}</p>"
            if skill_html:
                skill_html = f"<div class='card'><h3>🧠 Skill gợi ý</h3>{skill_html}</div>"

            # Chạy task (blocking — đơn giản cho v1)
            task = orch.run(title, subtasks)
            self.send_response(302)
            self.send_header("Location", f"/task?id={task.id}")
            self.end_headers()
            return

        self._send(page("404", "<h1>Không tìm thấy</h1>", user=user), 404)


if __name__ == "__main__":
    # Bootstrap admin user từ env (dùng cho deploy Railway/lần đầu)
    _au = os.environ.get("ORCHESTRA_ADMIN_USER")
    _ap = os.environ.get("ORCHESTRA_ADMIN_PASS")
    if _au and _ap:
        try:
            if create_user(_au, _ap):
                print(f"  [bootstrap] đã tạo user '{_au}'")
        except Exception as e:
            print(f"  [bootstrap] bỏ qua: {e}")
    print(f"Tom AI Orchestra — port {PORT}")
    try:
        import office_chat
        office_chat.start_loop()
    except Exception as e:
        print(f"  [office-chat] khong khoi dong duoc vong tu chat: {e}")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
