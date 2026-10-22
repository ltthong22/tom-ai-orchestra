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
        if p == "/logout":
            destroy_session(self._cookie_token())
            self.send_response(302)
            self.send_header("Set-Cookie", "tok=; Path=/; Max-Age=0")
            self.send_header("Location", "/login")
            self.end_headers()
            return
        self._send(page("404", "<h1>Không tìm thấy</h1>", user=user), 404)

    def _api_router(self):
        """JSON live status: slots + latency từng agent (kiểu Live activity dashboard)."""
        import json as _json
        try:
            from orchestrator.router import get_router
            data = {"agents": get_router().all_stats()}
        except Exception as e:
            data = {"error": str(e), "agents": []}
        body = _json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dashboard(self, user):
        tasks = orch.list_tasks(10)
        cards = ""
        for t in tasks:
            cards += (f"<div class='card'><h3><a href='/task?id={t['id']}' "
                      f"style='color:var(--text)'>{html.escape(t['title'][:60])}</a></h3>"
                      f"<span class='badge {t['status']}'>{t['status']}</span> "
                      f"<span class='muted' style='font-size:.8rem'>{t['created_at']}</span></div>")
        if not cards:
            cards = "<div class='card'><p class='muted'>Chưa có task nào. <a href='/new' style='color:var(--accent)'>Giao việc mới →</a></p></div>"

        # token summary mini
        tok_html = ""
        if _tracker:
            tot = _tracker.total()
            by = _tracker.summary_by_agent(7)
            top = by[0] if by else None
            tok_html = (f"<div class='grid cols3'>"
                        f"<div class='card'><div class='stat'>{fmt_num(tot['input']+tot['output'])}<small> tokens</small></div><p class='muted'>Tổng đã dùng</p></div>"
                        f"<div class='card'><div class='stat'>{tot['calls']}<small> calls</small></div><p class='muted'>API calls</p></div>"
                        f"<div class='card'><div class='stat' style='font-size:1.2rem'>{(top['agent'] if top else '—')}</div><p class='muted'>AI dùng nhiều nhất (7 ngày)</p></div>"
                        f"</div>")

        self._send(page("Tổng quan",
            f"<h1>📊 Tổng quan</h1>{tok_html}"
            f"<h2>⚡ Live activity — AI Providers</h2>"
            f"<div class='card'><div id='router-live' class='muted'>Đang tải...</div></div>"
            f"""<script>
            async function loadRouter(){{
              try{{
                const r = await fetch('/api/router'); const d = await r.json();
                const el = document.getElementById('router-live');
                if(!d.agents || !d.agents.length){{ el.textContent='Chưa có dữ liệu'; return; }}
                el.innerHTML = d.agents.map(a=>{{
                  const pct = a.max_slots ? Math.round(a.in_flight/a.max_slots*100) : 0;
                  const lat = a.avg_ms ? (a.avg_ms>=1000 ? (a.avg_ms/1000).toFixed(1)+'s' : a.avg_ms+'ms') : '—';
                  const fail = a.calls ? Math.round(a.fails/a.calls*1000)/10+'%' : '—';
                  return `<div style='margin:.5rem 0;padding:.5rem;border:1px solid var(--border);border-radius:8px'>`
                    + `<b>${{a.agent}}</b> <span class='muted'>${{a.in_flight}}/${{a.max_slots}} slots · ${{lat}} avg · ${{a.calls}} calls · ${{fail}} failed</span>`
                    + `<div style='background:var(--border);border-radius:4px;height:6px;margin-top:4px'>`
                    + `<div style='width:${{pct}}%;height:6px;border-radius:4px;background:var(--accent)'></div></div></div>`;
                }}).join('');
              }}catch(e){{ document.getElementById('router-live').textContent='Lỗi tải'; }}
            }}
            loadRouter(); setInterval(loadRouter, 5000);
            </script>"""
            f"<h2>Task gần đây</h2><div class='grid'>{cards}</div>",
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
