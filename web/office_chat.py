"""Vong chat tu dong cua Van phong AI song (VP Song).

- Moi gio 1 vong ngan: Gemini noi -> DeepSeek tra loi (luan phien chieu nguoc lai theo so vong).
- Chu de lay tu trang thai that (task-bus pending, council_log, goal moi nhat) — chi DOC, khong sua.
- Tin nhan do provider tao that. Tin "He thong" chi mang trang thai that, khong gan loi soan san cho agent.
- Feed luu o data/office_chat_feed.json (ghi nguyen tu tmp+rename, khoa thread, giu toi da 200 tin).
  Feed song qua restart process cung may; tren Railway se mat khi redeploy (chua co volume).
- Cong tac tat: tao file data/office_chat_off (hoac env OFFICE_CHAT_OFF=1) -> vong lap ngu, khong goi provider.
- Chay 1 vong ngay: tao file data/office_chat_run_once -> thread chay vong trong 60s toi (xoa flag sau 1 lan thu).
- Guardrail: chi chat. Prompt cam tien that/dat lenh/sua bot/file/task. Loi provider -> bo qua vong im lang,
  khong retry don dap. Tin ngan, moi gio toi da 1 vong.
- Khong chua secret: key lay tu env (app da load .env) giong agents/gemini.py; khong in key ra log.
"""

import glob
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
FEED_PATH = os.path.join(DATA_DIR, "office_chat_feed.json")
OFF_FLAG = os.path.join(DATA_DIR, "office_chat_off")
RUN_ONCE_FLAG = os.path.join(DATA_DIR, "office_chat_run_once")
TASK_BUS_PENDING = os.path.expanduser("~/workspace/task-bus/pending")
COUNCIL_LOG = os.path.expanduser("~/workspace/tradebot/vm/council_log.md")
GOALS_DIR = os.path.expanduser("~/workspace/goals")

MAX_MESSAGES = 200
MAX_TEXT = 600
ROUND_INTERVAL = 3600
TICK = 60

SYSTEM_PROMPT = (
    "Ban la thanh vien hoi dong AI noi bo cua studio TomStudio, dang chat trong Van phong AI. "
    "Chi tro chuyen, nhan xet, goi y. TUYET DOI KHONG dung tien that, khong dat lenh giao dich, "
    "khong sua bot dang chay, khong sua/xoa file hay task cua user, khong goi cong cu. "
    "Tra loi ngan gon 2-3 cau, tieng Viet, giong tu nhien nhu chat nhom."
)

_lock = threading.Lock()
_started = False


def _log(msg):
    print(f"[office-chat] {msg}", flush=True)


def _load_dotenv():
    """Load repo-root .env vao env (giong web/app.py), khong in gia tri."""
    try:
        env_path = os.path.join(REPO_ROOT, ".env")
        if os.path.exists(env_path):
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip('"').strip("'")
                        if k and k not in os.environ:
                            os.environ[k] = v
    except Exception:
        pass


_load_dotenv()


def _now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read_feed():
    try:
        with open(FEED_PATH, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("messages"), list):
            return d
    except Exception:
        pass
    return {"messages": [], "last_round": 0}


def _write_feed(feed):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = FEED_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(feed, f, ensure_ascii=False)
    os.replace(tmp, FEED_PATH)


def _append(agent, text, round_no):
    with _lock:
        feed = _read_feed()
        msgs = feed["messages"]
        nid = (msgs[-1]["id"] + 1) if msgs else 1
        msgs.append({"id": nid, "agent": agent, "text": text,
                     "ts": _now_iso(), "round": round_no})
        feed["messages"] = msgs[-MAX_MESSAGES:]
        feed["last_round"] = round_no
        _write_feed(feed)
        return nid


def get_messages(since=0, init=False):
    """Feed cho endpoint /api/office-chat. init=1 chi tra latest_id (trang lay moc, khong dump lich su)."""
    feed = _read_feed()
    msgs = feed["messages"]
    latest = msgs[-1]["id"] if msgs else 0
    if init:
        return {"messages": [], "latest_id": latest}
    return {"messages": [m for m in msgs if m["id"] > since][-100:],
            "latest_id": latest}


def _short(text, limit=MAX_TEXT):
    text = (text or "").strip()
    if len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0] or text[:limit]
        return cut + "…"
    return text


def _pending_titles():
    try:
        files = sorted(glob.glob(os.path.join(TASK_BUS_PENDING, "*.json")),
                       key=lambda p: os.path.getmtime(p), reverse=True)
    except Exception:
        return 0, []
    titles = []
    for p in files[:3]:
        title = None
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                for k in ("title", "task", "name", "description"):
                    v = d.get(k)
                    if isinstance(v, str) and v.strip():
                        title = v.strip()
                        break
        except Exception:
            title = None
        if not title:
            base = os.path.basename(p)[:-5]
            import re
            base = re.sub(r"^\d{8}-\d{6}-", "", base)
            base = re.sub(r"^\d+-", "", base)
            title = base.replace("-", " ").replace("_", " ")
        titles.append(_short(title, 120))
    return len(files), titles


def _council_lines():
    try:
        with open(COUNCIL_LOG, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        return [_short(ln, 200) for ln in lines[-3:]]
    except Exception:
        return []


def _goal_titles():
    try:
        files = sorted(glob.glob(os.path.join(GOALS_DIR, "*/GOAL.md")),
                       key=lambda p: os.path.getmtime(p), reverse=True)
    except Exception:
        return []
    titles = []
    for p in files[:2]:
        try:
            with open(p, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip().lstrip("#").strip()
                    if ln:
                        titles.append(_short(ln, 120))
                        break
        except Exception:
            pass
    return titles


def _pick_topic():
    parts = []
    n, titles = _pending_titles()
    if titles:
        parts.append(f"Viec dang cho ({n}): " + "; ".join(titles))
    cl = _council_lines()
    if cl:
        parts.append("Hoi dong moi nhat: " + " | ".join(cl))
    gl = _goal_titles()
    if gl:
        parts.append("Goal moi: " + "; ".join(gl))
    if not parts:
        return None
    return _short(" ".join(parts), 900)


def _ask_gemini(prompt, system):
    try:
        from agents.gemini import GeminiAgent
        res = GeminiAgent().run(prompt, system=system, timeout=90)
        if res.success and res.output:
            meta = res.meta or {}
            return _short(res.output), meta
        return None, (res.error or "Gemini khong tra loi")
    except Exception as e:
        return None, f"Gemini exception: {e}"


def _ask_deepseek(prompt, system):
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        return None, "Thieu DEEPSEEK_API_KEY"
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 300,
    }
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", "90", "https://api.deepseek.com/chat/completions",
             "-H", "Content-Type: application/json",
             "-H", "Authorization: Bearer " + key,
             "-d", json.dumps(payload)],
            capture_output=True, text=True, timeout=120)
    except Exception as e:
        return None, f"curl exception: {e}"
    if r.returncode != 0:
        return None, f"curl exit {r.returncode}"
    try:
        j = json.loads(r.stdout)
    except Exception:
        return None, "DeepSeek tra ve khong phai JSON"
    if "error" in j:
        err = j["error"]
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        return None, f"DeepSeek API: {msg}"
    try:
        text = j["choices"][0]["message"]["content"]
        usage = j.get("usage", {})
        return _short(text), usage
    except Exception as e:
        return None, f"DeepSeek parse: {e}"


_ASK = {"Gemini": _ask_gemini, "DeepSeek": _ask_deepseek}


def _run_round():
    topic = _pick_topic()
    if not topic:
        _log("skip: khong co chu de that")
        return False
    feed = _read_feed()
    round_no = feed.get("last_round", 0) + 1
    if round_no % 2 == 1:
        first_agent, second_agent = "Gemini", "DeepSeek"
    else:
        first_agent, second_agent = "DeepSeek", "Gemini"
    first_prompt = (
        f"Trang thai moi nhat cua studio:\n{topic}\n\n"
        "Nhan xet ngan gon 2-3 cau: dieu gi dang chu y nhat, va nen chu y gi tiep theo? "
        "(Chi noi, khong lam gi ca.)")
    first_text, first_meta = _ASK[first_agent](first_prompt, SYSTEM_PROMPT)
    if not first_text:
        _log(f"round {round_no} skip: {first_agent} FAIL: {first_meta}")
        return False
    _append("Hệ thống",
            f"🔄 Vòng chat tự động #{round_no} — chủ đề từ trạng thái thật: {_short(topic, 300)}",
            round_no)
    _append(first_agent, first_text, round_no)
    _log(f"round {round_no}: {first_agent} ok {first_meta}")
    reply_prompt = (
        f"{first_agent} vua noi:\n\"{first_text}\"\n\n"
        "Tra loi ngan gon 2-3 cau: dong y hay phan doi diem nao, bo sung gi? "
        "(Chi noi, khong lam gi ca.)")
    second_text, second_meta = _ASK[second_agent](reply_prompt, SYSTEM_PROMPT)
    if second_text:
        _append(second_agent, second_text, round_no)
        _log(f"round {round_no}: {second_agent} ok {second_meta}")
    else:
        _log(f"round {round_no}: {second_agent} FAIL (giu vong 1 tin): {second_meta}")
    return True


def _killed():
    return os.path.exists(OFF_FLAG) or os.environ.get("OFFICE_CHAT_OFF") == "1"


def _loop():
    _log("loop started (moi gio 1 vong; tat bang file data/office_chat_off)")
    last_hourly = time.time()
    first_done = False
    while True:
        try:
            if _killed():
                time.sleep(300)
                continue
            if os.path.exists(RUN_ONCE_FLAG):
                try:
                    os.remove(RUN_ONCE_FLAG)
                except Exception:
                    pass
                _run_round()
                last_hourly = time.time()
                first_done = True
                time.sleep(TICK)
                continue
            if not first_done:
                # Lan dau tien (feed rong): chay 1 vong ngay de trang co tin song.
                # Restart sau do khong chay ngay nua (feed da co tin).
                first_done = True
                if not _read_feed()["messages"]:
                    _run_round()
                    last_hourly = time.time()
                time.sleep(TICK)
                continue
            if time.time() - last_hourly >= ROUND_INTERVAL:
                _run_round()
                last_hourly = time.time()
        except Exception as e:
            _log(f"loop exception (bo qua): {e}")
        time.sleep(TICK)


def start_loop():
    global _started
    if _started:
        return
    _started = True
    t = threading.Thread(target=_loop, daemon=True, name="office-chat-loop")
    t.start()


def run_once():
    return _run_round()


if __name__ == "__main__":
    ok = run_once()
    print("office-chat run_once:", ok)
