"""Bang tien trinh tong (task-bus) cho trang /ai-office — v1.0 · 28/10/2026.

Doc truc tiep task-bus (pending/claimed/done) moi lan goi nen bang luon tuoi,
khong co file du lieu rieng de lech. Quy tac hien thi (anh chot 26/10):
- moi agent/side chat 1 dong: dang lam gi, tien do %, trang thai
  DANG LAM / DA XONG / CHUA LAM;
- chi hien task dang hoat dong (pending/claimed) hoac moi xong gan day (7 ngay);
- task claimed qua 24h khong xong -> danh dau TREO ro rang, khong giau;
- task khong lam gi thi khong hien.
Chi dung stdlib, khong import app — de test doc lap.
"""
import html
import json
import os
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=7))
RECENT_DAYS = 7
STUCK_HOURS = 24

AGENT_LABEL = {
    "gemini": "🦊 Gemini",
    "deepseek": "🦈 DeepSeek",
    "lmstudio": "💻 LM Studio",
    "muse": "🦉 Muse",
    "main": "👑 Main",
    "?": "❓ Chưa gán bên",
}

EC_DONE = [
    ("B1", "🐢", "INSTA 360 (81GB)", "G: → J:"),
    ("B2", "🐜", "Work (70GB)", "G: → H:"),
    ("B3", "🦥", "Vertex/Pic/Fonts/2023 (26GB)", "G: → H:"),
    ("B4", "🦣", "EC Team (295GB)", "H: → G:"),
]


def _parse_time(s):
    """Parse ISO '2026-10-28T03:02:00+07:00' hoac '2026-10-28 03:02:00'."""
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        t = s.strip().replace("Z", "+00:00")
        d = datetime.fromisoformat(t)
        if d.tzinfo is None:
            d = d.replace(tzinfo=TZ)
        return d
    except Exception:
        return None


def _task_time(data, mtime):
    for key in ("claimed_at", "updated_at", "completed_at", "created_at"):
        t = _parse_time(data.get(key))
        if t:
            return t
    return datetime.fromtimestamp(mtime, tz=TZ)


def _load_tasks(bus_dir):
    """Tra ve list dict: {subdir, file, mtime, data} — bo file hong."""
    out = []
    for sub in ("pending", "claimed", "done"):
        d = os.path.join(bus_dir, sub)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(d, fn)
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    continue
                out.append({"subdir": sub, "file": fn, "mtime": os.path.getmtime(p),
                            "data": data})
            except Exception:
                continue
    return out


def _owner_of(data):
    to = data.get("to")
    if isinstance(to, list):
        to = to[0] if to else None
    if isinstance(to, str) and to.strip():
        return to.strip()
    frm = data.get("from")
    if isinstance(frm, str) and frm.strip():
        return frm.strip()
    return "?"


def _status_of(item):
    data = item["data"]
    st = data.get("status") or item["subdir"]
    if st in ("claimed", "working", "in_progress", "running"):
        t = _task_time(data, item["mtime"])
        if (datetime.now(TZ) - t) > timedelta(hours=STUCK_HOURS):
            return "TREO"
        return "DANG LAM"
    if st == "pending":
        return "CHUA LAM"
    if st == "error":
        return "LOI"
    return "DA XONG"


def _pct_of(item, status):
    data = item["data"]
    p = data.get("progress")
    if isinstance(p, (int, float)) and not isinstance(p, bool):
        return f"{int(p)}%"
    if status == "DA XONG":
        return "100%"
    if status == "CHUA LAM":
        return "0%"
    return "—"


def build_board_rows(bus_dir):
    """Moi owner 1 dong: claimed > pending > done/error moi (7 ngay)."""
    now = datetime.now(TZ)
    by_owner = {}
    for item in _load_tasks(bus_dir):
        by_owner.setdefault(_owner_of(item["data"]), []).append(item)
    rows = []
    for owner, items in by_owner.items():
        claimed = [i for i in items
                   if (i["data"].get("status") or i["subdir"]) in
                   ("claimed", "working", "in_progress", "running")]
        pending = [i for i in items if (i["data"].get("status") or i["subdir"]) == "pending"]
        recent = [i for i in items
                  if (i["data"].get("status") or i["subdir"]) in ("done", "error")
                  and (now - _task_time(i["data"], i["mtime"])) <= timedelta(days=RECENT_DAYS)]
        pick = None
        if claimed:
            pick = max(claimed, key=lambda i: _task_time(i["data"], i["mtime"]))
        elif pending:
            pick = max(pending, key=lambda i: _task_time(i["data"], i["mtime"]))
        elif recent:
            pick = max(recent, key=lambda i: _task_time(i["data"], i["mtime"]))
        if not pick:
            continue  # owner khong co task dang hoat dong / moi xong -> khong hien
        status = _status_of(pick)
        text = (pick["data"].get("task") or pick["data"].get("title") or pick["file"])
        text = " ".join(str(text).split())
        if len(text) > 90:
            text = text[:87] + "…"
        rows.append({
            "owner": owner,
            "label": AGENT_LABEL.get(owner, owner),
            "task": text,
            "pct": _pct_of(pick, status),
            "status": status,
            "file": pick["file"],
        })
    rank = {"TREO": 0, "DANG LAM": 1, "CHUA LAM": 2, "LOI": 3, "DA XONG": 4}
    rows.sort(key=lambda r: (rank.get(r["status"], 5), r["owner"]))
    return rows


def build_board_html(bus_dir):
    """HTML 1 section: khoi EC Shuffle DA XONG + bang tong agent/side chat."""
    rows = build_board_rows(bus_dir)
    n_work = sum(1 for r in rows if r["status"] in ("DANG LAM", "TREO"))
    n_wait = sum(1 for r in rows if r["status"] == "CHUA LAM")
    n_done = sum(1 for r in rows if r["status"] == "DA XONG")
    n_stuck = sum(1 for r in rows if r["status"] == "TREO")
    ec_rows = "".join(
        f"<div style='display:flex;gap:8px;align-items:center;padding:4px 0'>"
        f"<span>{char}</span><b style='min-width:28px'>{bid}</b>"
        f"<span style='flex:1'>{html.escape(name)}</span>"
        f"<span style='color:#94a3b8;font-size:12px'>{html.escape(route)}</span>"
        f"<span>✅ 100%</span></div>"
        for bid, char, name, route in EC_DONE)
    if rows:
        trs = ""
        for r in rows:
            badge = {"DANG LAM": "🔄 ĐANG LÀM", "CHUA LAM": "⏳ CHƯA LÀM",
                     "DA XONG": "✅ ĐÃ XONG", "LOI": "❌ LỖI",
                     "TREO": "⚠️ TREO"}.get(r["status"], r["status"])
            trs += (f"<tr><td style='padding:6px 8px;white-space:nowrap'>"
                    f"{html.escape(r['label'])}</td>"
                    f"<td style='padding:6px 8px'>{html.escape(r['task'])}</td>"
                    f"<td style='padding:6px 8px;text-align:center'>{r['pct']}</td>"
                    f"<td style='padding:6px 8px;white-space:nowrap'>{badge}</td></tr>")
        table = (f"<table style='width:100%;border-collapse:collapse;font-size:13px'>"
                 f"<tr style='color:#94a3b8;text-align:left'>"
                 f"<th style='padding:6px 8px'>Bên</th>"
                 f"<th style='padding:6px 8px'>Đang làm gì</th>"
                 f"<th style='padding:6px 8px;text-align:center'>Tiến độ</th>"
                 f"<th style='padding:6px 8px'>Trạng thái</th></tr>{trs}</table>")
    else:
        table = ("<div style='color:#94a3b8;font-size:13px'>"
                 "Chưa có task đang hoạt động hoặc mới xong gần đây.</div>")
    return (
        f"<div class='ofc-sec-title'>📋 Bảng tiến trình tổng "
        f"<span style='opacity:.6;font-weight:400'>(task-bus · cập nhật mỗi lần tải trang)</span></div>"
        f"<div style='border:1px solid #2a3d5c;border-radius:12px;padding:12px 14px;margin-bottom:14px'>"
        f"<div style='font-weight:700;margin-bottom:6px'>🔀 EC Shuffle — chuyển dữ liệu ổ cứng: "
        f"✅ ĐÃ XONG 100%</div>"
        f"<div style='font-size:13px'>{ec_rows}</div>"
        f"<div style='color:#94a3b8;font-size:12px;margin-top:4px'>"
        f"Xong 23/10/2026 · verify 26/10/2026 · nguồn: task-bus (Kỹ thuật - Lưu Trữ)</div>"
        f"<hr style='border:none;border-top:1px solid #2a3d5c;margin:12px 0'>"
        f"<div style='color:#94a3b8;font-size:12px;margin-bottom:8px'>"
        f"{n_work} đang làm · {n_wait} chờ · {n_done} mới xong (7 ngày)"
        + (f" · ⚠️ {n_stuck} treo" if n_stuck else "") + "</div>"
        f"{table}</div>")
