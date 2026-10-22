"""Tom AI Orchestra — Web UI responsive (mobile-first, dark mode).

Màn hình: dashboard, giao việc mới, chi tiết task, lịch sử,
token usage, skills, cài đặt.
Chạy: python3 web/app.py  ->  http://localhost:8099
"""
import html
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from orchestrator import Orchestrator, SubTask
from auth.auth import verify, make_session, check_session, destroy_session, create_user

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
"""

NAV_ITEMS = [
    ("/", "📊", "Tổng quan"),
    ("/chat", "💬", "Chat AI"),
    ("/new", "➕", "Giao việc"),
    ("/history", "📜", "Lịch sử"),
    ("/tokens", "🎟️", "Token"),
    ("/skills", "🧠", "Skills"),
    ("/settings", "⚙️", "Cài đặt"),
]


def page(title, body, active="/", user=""):
    nav = "".join(
        f"<a href='{p}' class='{'active' if p == active else ''}'>{icon} {lbl}</a>"
        for p, icon, lbl in NAV_ITEMS)
    return f"""<!DOCTYPE html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0d1117">
<title>{html.escape(title)} — Tom AI Orchestra</title>
<style>{CSS}</style></head>
<body>
<div class="topbar"><span class="logo">🎼 Orchestra</span><span class="spacer"></span>
{f"<span class='muted'>👤 {html.escape(user)}</span>" if user else ""}</div>
<nav class="nav">{nav}</nav>
<div class="wrap tabbar">{body}</div>
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
            self._dashboard(user); return
        if p == "/new":
            self._new_form(user); return
        if p == "/task":
            self._task_detail(user, q.get("id", [""])[0]); return
        if p == "/history":
            self._history(user); return
        if p == "/tokens":
            self._tokens_page(user); return
        if p == "/skills":
            self._skills_page(user, q.get("q", [""])[0], q.get("name", [""])[0]); return
        if p == "/settings":
            self._settings(user); return
        if p == "/api/router":
            self._api_router(); return
        if p == "/chat":
            self._chat_page(user); return
        if p == "/api/chat/history":
            self._api_chat_history(); return
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
            data = {"error": str(e), "messages": []}
        body = _json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
                # chọn agent: auto -> dùng pick_agent, hoặc ép agent cụ thể
                if agent_want != "auto":
                    agent_name = agent_want
                else:
                    agent_name = orch.pick_agent("quick_qa")
                st = SubTask(id="st-chat", kind="quick_qa", prompt=msg,
                             agent_name=agent_name if agent_want != "auto" else None)
                r = orch._run_subtask(st)
                agent_used = r.get("agent", agent_name)
                reply = r["output"] if r["success"] and r["output"] else (
                    f"⚠️ {agent_used} lỗi: {r.get('error', 'không rõ')}")
                saved = save_message("ai", reply, agent=agent_used)
                body = _json.dumps({"ok": True, "message": saved},
                                   ensure_ascii=False).encode("utf-8")
            except Exception as e:
                body = _json.dumps({"error": str(e)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _chat_page(self, user):
        self._send(page("Chat AI", """
<h1>💬 Chat AI</h1>
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
  claude:{label:'Claude', icon:'🟠', cls:'claude'},
};
const box = document.getElementById('chat-msgs');
const typing = document.getElementById('typing');
let lastId = '';

function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

function renderMsg(m){
  const div = document.createElement('div');
  if(m.role === 'user'){
    div.className = 'msg user';
    div.innerHTML = esc(m.content) + `<div class="time">${(m.created_at||'').slice(11,16)}</div>`;
  } else {
    const meta = AGENT_META[m.agent] || {label:m.agent||'AI', icon:'🤖', cls:'muse'};
    div.className = 'msg ai';
    div.innerHTML = `<div class="who ${meta.cls}"><span class="ava ${meta.cls}">${meta.icon}</span>${meta.label}</div>`
      + esc(m.content) + `<div class="time">${(m.created_at||'').slice(11,16)}</div>`;
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
      body:'msg='+encodeURIComponent(text)+'&agent='+encodeURIComponent(agent),
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
""", active="/chat", user=user))
        tasks = orch.list_tasks(10)
        cards = ""
        for t in tasks:
            cards += (f"<div class='card'><h3><a href='/task?id={t['id']}' "
                      f"style='color:var(--text)'>{html.escape(t['title'][:60])}</a></h3>"
                      f"<span class='badge {t['status']}'>{t['status']}</span> "
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
const AGENT_COLOR = {{"gemini":"b","lmstudio":"g","muse":"y","deepseek":"r","claude":"b"}};
const AGENT_LABEL = {{"gemini":"Gemini","lmstudio":"LM Studio","muse":"Muse","deepseek":"DeepSeek","claude":"Claude"}};

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
    const order = ['gemini','lmstudio','muse','deepseek','claude'];
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
        self._send(page("Giao việc mới",
            """<h1>➕ Giao việc mới</h1>
            <div class="card"><form method="post" action="/run">
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
            f"<p><span class='badge {t['status']}'>{t['status']}</span> "
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
            f"<td><span class='badge {t['status']}'>{t['status']}</span></td>"
            f"<td class='muted'>{t['created_at'][:16]}</td></tr>"
            for t in tasks)
        self._send(page("Lịch sử",
            f"<h1>📜 Lịch sử task</h1><div class='card' style='overflow-x:auto'>"
            f"<table class='tbl'><tr><th>ID</th><th>Tiêu đề</th><th>TT</th><th>Tạo lúc</th></tr>{rows}</table></div>",
            active="/history", user=user))

    def _tokens_page(self, user):
        if not _tracker:
            self._send(page("Token", "<h1>🎟️ Token</h1><p class='muted'>Chưa có dữ liệu.</p>",
                            active="/tokens", user=user))
            return
        by_agent = _tracker.summary_by_agent(30)
        daily = _tracker.daily(14)
        by_task = _tracker.summary_by_task(10)
        tot = _tracker.total()

        max_tok = max([r["tin"] or 0 for r in by_agent] + [1])
        agent_bars = "".join(
            f"<div class='tbar'><span class='lbl'>{html.escape(r['agent'])}</span>"
            f"<span class='track'><span class='fill' style='width:{int((r['tin'] or 0)/max_tok*100)}%'></span></span>"
            f"<span class='val'>{fmt_num(r['tin'])} in · {fmt_num(r['tout'])} out<br>${r['est_cost_usd']}</span></div>"
            for r in by_agent) or "<p class='muted'>Chưa có dữ liệu.</p>"

        daily_rows = "".join(
            f"<tr><td>{r['day']}</td><td>{fmt_num(r['tin'])}</td>"
            f"<td>{fmt_num(r['tout'])}</td><td>{r['calls']}</td></tr>"
            for r in daily)

        task_rows = "".join(
            f"<tr><td><a href='/task?id={r['task_id']}' style='color:var(--accent)'>{r['task_id']}</a></td>"
            f"<td>{fmt_num(r['tin'])}</td><td>{fmt_num(r['tout'])}</td><td>{r['calls']}</td></tr>"
            for r in by_task)

        self._send(page("Token usage",
            f"<h1>🎟️ Token usage</h1>"
            f"<div class='grid cols3'>"
            f"<div class='card'><div class='stat'>{fmt_num(tot['input']+tot['output'])}</div><p class='muted'>Tổng tokens</p></div>"
            f"<div class='card'><div class='stat'>{tot['calls']}</div><p class='muted'>API calls</p></div>"
            f"<div class='card'><div class='stat'>{len(by_agent)}</div><p class='muted'>AI đang dùng</p></div></div>"
            f"<h2>Theo AI (30 ngày)</h2><div class='card'>{agent_bars}</div>"
            f"<h2>Theo ngày</h2><div class='card' style='overflow-x:auto'>"
            f"<table class='tbl'><tr><th>Ngày</th><th>Input</th><th>Output</th><th>Calls</th></tr>{daily_rows}</table></div>"
            f"<h2>Task tốn token nhất</h2><div class='card' style='overflow-x:auto'>"
            f"<table class='tbl'><tr><th>Task</th><th>Input</th><th>Output</th><th>Calls</th></tr>{task_rows}</table></div>",
            active="/tokens", user=user))

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

    def _settings(self, user):
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
        self._send(page("Cài đặt",
            f"<h1>⚙️ Cài đặt</h1>"
            f"<h2>Trạng thái AI</h2><div class='card'>{agents_html}</div>"
            f"<h2>Skills</h2><div class='card'><p>🧠 <b>{n_skills}</b> skills đã index. "
            f"<a href='/skills' style='color:var(--accent)'>Xem danh sách →</a></p></div>"
            f"<h2>Token</h2><div class='card'>"
            f"<p class='muted'>Ngưỡng cảnh báo token/task: <b>{warn}</b><br>"
            f"Đổi bằng env <code>TOKEN_WARN_THRESHOLD</code> khi chạy server.</p></div>"
            f"<h2>Tài khoản</h2><div class='card'><p>👤 <b>{html.escape(user)}</b></p>"
            f"<a class='btn btn-ghost' href='/logout' style='width:auto'>Đăng xuất</a></div>",
            active="/settings", user=user))

    # ============ POST ============
    def do_POST(self):
        u = urlparse(self.path)
        p = u.path
        n = int(self.headers.get("Content-Length", 0))
        q = parse_qs(self.rfile.read(n).decode() if n else "")

        if p == "/login":
            user, pw = q.get("u", [""])[0], q.get("p", [""])[0]
            if verify(user, pw):
                tok = make_session(user)
                self.send_response(302)
                self.send_header("Set-Cookie", f"tok={tok}; Path=/; HttpOnly; SameSite=Lax")
                self.send_header("Location", "/")
                self.end_headers()
            else:
                self._send(page("Đăng nhập",
                    "<div class='login-wrap'><div class='card'>"
                    "<p style='color:var(--danger)'>Sai tài khoản/mật khẩu.</p>"
                    "<a class='btn' href='/login'>Thử lại</a></div></div>"))
            return

        user = self._user()
        if not user:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
            return

        if p == "/api/chat/send":
            self._api_chat_send(q); return
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
    HTTPServer(("0.0.0.0", PORT), H).serve_forever()
