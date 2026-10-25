"""Vong chat tu dong cua Van phong AI song (VP Song).

- Moi gio 1 vong ngan, xen ke 2 che do theo so vong:
  + Vong LE: chu de tu trang thai that (task-bus pending XOAY VONG tung dot 3 task,
    council_log, goal moi nhat) — chi DOC, khong sua.
  + Vong CHAN: 1 agent (luan phien theo so vong) TU DE XUAT chu de kieu JARVIS, ca hai ban ve no.
    Chu de tu tao la Y TUONG/CAU HOI/DE XUAT, khong duoc bia su kien ve studio (xem PROPOSE_PROMPT).
- Vong that bai (khong co chu de that, provider loi) van tang so vong de vong sau doi che do nhu thuong.
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
    "KHONG bia su kien ve studio (ten show, ten khach, so tien, ngay gio, ket qua test); "
    "su kien that chi lay tu thong tin kem theo; khong chac thi noi dang gia thuyet "
    "('gia su...', 'neu...') chu khong khang dinh. "
    "Tra loi ngan gon 2-3 cau, tieng Viet, giong tu nhien nhu chat nhom."
)

_lock = threading.Lock()
_started = False
_last_error = ""


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
    global _last_error
    with _lock:
        _last_error = ""
        feed = _read_feed()
        msgs = feed["messages"]
        nid = (msgs[-1]["id"] + 1) if msgs else 1
        msgs.append({"id": nid, "agent": agent, "text": text,
                     "ts": _now_iso(), "round": round_no})
        feed["messages"] = msgs[-MAX_MESSAGES:]
        feed["last_round"] = round_no
        _write_feed(feed)
        return nid


def get_status():
    """Trang thai vong chat cho giao dien (de hien ly do khi feed rong)."""
    feed = _read_feed()
    return {"loop_started": _started,
            "last_round": feed.get("last_round", 0),
            "msg_count": len(feed.get("messages", [])),
            "last_error": _short(_last_error, 120) if _last_error else ""}


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
    """Tat ca task pending (moi nhat truoc). Vong chat xoay vong lay tung dot 3 task
    (con tro luu trong feed) de task nao cung duoc thao luan, khong chi 3 task moi nhat."""
    try:
        files = sorted(glob.glob(os.path.join(TASK_BUS_PENDING, "*.json")),
                       key=lambda p: os.path.getmtime(p), reverse=True)
    except Exception:
        return 0, []
    titles = []
    for p in files:
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
        titles.append(_short(title, 90))
    return len(files), titles


def _pending_window(titles):
    """Lay dot 3 task tiep theo theo con tro xoay vong, luu con tro vao feed."""
    if not titles:
        return []
    with _lock:
        feed = _read_feed()
        cur = feed.get("pending_cursor", 0) % len(titles)
        n = min(3, len(titles))
        window = [titles[(cur + i) % len(titles)] for i in range(n)]
        feed["pending_cursor"] = (cur + n) % len(titles)
        _write_feed(feed)
    return window


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
    window = _pending_window(titles)
    if window:
        parts.append(f"Viec dang cho ({n}, dot nay: " + "; ".join(window) + ")")
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


def _bump_round(round_no):
    """Tang so vong khi vong that bai truoc khi co tin nao (de vong sau doi che do)."""
    with _lock:
        feed = _read_feed()
        if round_no > feed.get("last_round", 0):
            feed["last_round"] = round_no
            _write_feed(feed)


PROPOSER_ROLE = {
    "Gemini": "nghien cuu, thi truong, tai chinh studio, xu huong content/gear",
    "DeepSeek": "ky thuat app, quy trinh lam viec, phuong phap trading, toi uu he thong",
}

PROPOSE_PROMPT = (
    "Ban dang o Van phong AI cua studio TomStudio. Hay tu nghi ra 1 chu de DANG BAN cho studio "
    "(y tuong moi, quan sat, cau hoi, de xuat cai tien) — uu tien goc nhin: {role}, nhung duoc lan san "
    "sang linh vuc khac neu co y hay.\n"
    "Trang thai that moi nhat (chi tham khao, khong duoc bia them su kien):\n{snapshot}\n\n"
    "RANH GIOI TRUNG THUC: duoc tu nghi Y TUONG/CAU HOI/DE XUAT thoai mai; TUYET DOI KHONG bia SU KIEN "
    "ve studio (khong bia ten show, ten khach, so tien, ngay gio, ket qua test). Moi su kien that chi duoc "
    "lay tu trang thai that ke tren; neu khong chac 1 su kien co that khong thi noi dang gia thuyet "
    "('gia su...', 'neu...') chu khong khang dinh.\n\n"
    "Tra loi dung 2 dong:\nChu de: <1 cau ngan gon>\nLy do: <1 cau vi sao dang ban>"
)


def _parse_proposal(text):
    """Tach 'Chu de:' / 'Ly do:' tu cau tra loi de xuat (chiu ca co dau/khong dau)."""
    topic, reason = "", ""
    for ln in (text or "").splitlines():
        ln = ln.strip()
        low = ln.lower()
        if low.startswith(("chu de:", "chủ đề:")):
            topic = ln.split(":", 1)[1].strip()
        elif low.startswith(("ly do:", "lý do:")):
            reason = ln.split(":", 1)[1].strip()
    if not topic:
        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        topic = _short(lines[0], 120) if lines else ""
    return _short(topic, 200), _short(reason, 200)


def _run_real_round(round_no):
    """Vong LE: chu de tu trang thai that, 2 tin (nguoi mo + nguoi tra loi)."""
    global _last_error
    topic = _pick_topic()
    if not topic:
        _last_error = "khong co chu de that"
        _log(f"round {round_no} skip: khong co chu de that")
        _bump_round(round_no)
        return False
    first_agent = "Gemini" if ((round_no // 2) % 2 == 0) else "DeepSeek"
    second_agent = "DeepSeek" if first_agent == "Gemini" else "Gemini"
    first_prompt = (
        f"Trang thai moi nhat cua studio:\n{topic}\n\n"
        "Nhan xet ngan gon 2-3 cau: dieu gi dang chu y nhat, va nen chu y gi tiep theo? "
        "(Chi noi, khong lam gi ca.)")
    first_text, first_meta = _ASK[first_agent](first_prompt, SYSTEM_PROMPT)
    if not first_text:
        _last_error = str(first_meta)
        _log(f"round {round_no} skip: {first_agent} FAIL: {first_meta}")
        _bump_round(round_no)
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


def _run_self_round(round_no):
    """Vong CHAN kieu JARVIS: 1 agent tu de xuat chu de, ca hai ban 2 luot ngan."""
    global _last_error
    proposer = "Gemini" if ((round_no // 2) % 2 == 0) else "DeepSeek"
    other = "DeepSeek" if proposer == "Gemini" else "Gemini"
    snapshot = _pick_topic() or "Khong co du lieu that moi."
    prompt = PROPOSE_PROMPT.format(role=PROPOSER_ROLE[proposer],
                                   snapshot=_short(snapshot, 600))
    prop_text, prop_meta = _ASK[proposer](prompt, SYSTEM_PROMPT)
    if not prop_text:
        _last_error = str(prop_meta)
        _log(f"round {round_no} skip: {proposer} de xuat FAIL: {prop_meta}")
        _bump_round(round_no)
        return False
    topic, reason = _parse_proposal(prop_text)
    if not topic:
        _last_error = "de xuat rong"
        _log(f"round {round_no} skip: de xuat rong")
        _bump_round(round_no)
        return False
    sys_text = f"💡 Chủ đề do {proposer} tự đề xuất: {topic}"
    if reason:
        sys_text += f" — {reason}"
    _append("Hệ thống", sys_text, round_no)
    _log(f"round {round_no}: {proposer} de xuat: {topic} | meta: {prop_meta}")
    comment_prompt = (
        f"Chu de do {proposer} tu de xuat: {topic}"
        + (f" ({reason})" if reason else "")
        + "\n\nBinh luan ngan gon 2-3 cau: thay hay/do o dau, bo sung gi? "
        "(Chi noi, khong lam gi ca.)")
    other_text, other_meta = _ASK[other](comment_prompt, SYSTEM_PROMPT)
    if other_text:
        _append(other, other_text, round_no)
        _log(f"round {round_no}: {other} ok {other_meta}")
    else:
        _log(f"round {round_no}: {other} FAIL: {other_meta}")
        take_prompt = (
            f"Chu de ban vua de xuat: {topic}"
            + (f" ({reason})" if reason else "")
            + "\n\nTrinh bay ngan gon 2-3 cau ve chu de nay: vi sao no quan trong, nen bat dau tu dau? "
            "(Chi noi, khong lam gi ca.)")
        take_text, take_meta = _ASK[proposer](take_prompt, SYSTEM_PROMPT)
        if take_text:
            _append(proposer, take_text, round_no)
            _log(f"round {round_no}: {proposer} take ok {take_meta}")
        else:
            _log(f"round {round_no}: {proposer} take FAIL: {take_meta}")
        return True
    reply_prompt = (
        f"{other} vua binh luan:\n\"{other_text}\"\n\n"
        "Tra loi ngan gon 2-3 cau: dong y hay phan doi diem nao, chot lai huong lam? "
        "(Chi noi, khong lam gi ca.)")
    reply_text, reply_meta = _ASK[proposer](reply_prompt, SYSTEM_PROMPT)
    if reply_text:
        _append(proposer, reply_text, round_no)
        _log(f"round {round_no}: {proposer} ok {reply_meta}")
    else:
        _log(f"round {round_no}: {proposer} reply FAIL: {reply_meta}")
    return True


def _run_round():
    feed = _read_feed()
    round_no = feed.get("last_round", 0) + 1
    if round_no % 2 == 1:
        return _run_real_round(round_no)
    return _run_self_round(round_no)


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
