"""Telegram bot: nhận task từ anh, gửi kết quả về.
Env cần: TELEGRAM_BOT_TOKEN, TELEGRAM_OWNER_ID (mặc định 8502284486).

Lệnh:
  /task <mô tả>     - chạy task đơn (double-check 3 lớp)
  /parallel         - reply nhiều dòng, mỗi dòng 1 subtask song song
  /status <task_id> - xem trạng thái task
  /tasks            - 10 task gần nhất
  /agents           - kiểm tra agent nào đang sống
"""
import json
import os
import urllib.request
import urllib.parse
import time

from orchestrator import Orchestrator, SubTask
from workflows import double_check, parallel
from agents import get_agent, list_agents

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER_ID = os.environ.get("TELEGRAM_OWNER_ID", "8502284486")
API = f"https://api.telegram.org/bot{TOKEN}"
orch = Orchestrator()


def tg(method: str, **params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"{API}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def send(chat_id: str, text: str):
    # Cắt message dài quá giới hạn Telegram
    for i in range(0, len(text), 4000):
        tg("sendMessage", chat_id=chat_id, text=text[i:i + 4000])
        time.sleep(0.3)


def handle(msg: dict):
    chat_id = str(msg["chat"]["id"])
    if chat_id != OWNER_ID:
        send(chat_id, "Bot này chỉ phục vụ chủ nhân.")
        return
    text = msg.get("text", "").strip()
    if not text.startswith("/"):
        # Tin nhắn thường = task nhanh qua double-check
        send(chat_id, "Đang xử lý (double-check 3 lớp)...")
        res = double_check.run(orch, text[:60], text)
        out = f"{'ĐẠT' if res['ok'] else 'CHƯA ĐẠT'} — {text[:60]}\n\n{res.get('work_output', res.get('error'))}"
        if not res["ok"]:
            out += f"\n\nVerdict: {res.get('verdict', '')}"
        send(chat_id, out)
        return

    parts = text.split(None, 1)
    cmd, arg = parts[0], (parts[1] if len(parts) > 1 else "")

    if cmd == "/task" and arg:
        send(chat_id, "Đang xử lý (double-check 3 lớp)...")
        res = double_check.run(orch, arg[:60], arg)
        send(chat_id, f"{'ĐẠT' if res['ok'] else 'CHƯA ĐẠT'}\n\n{res.get('work_output', res.get('error'))}")

    elif cmd == "/parallel" and arg:
        items = [{"prompt": line.strip()} for line in arg.splitlines() if line.strip()]
        send(chat_id, f"Chạy song song {len(items)} subtask...")
        res = parallel.run(orch, "parallel task", items)
        send(chat_id, ("XONG\n\n" if res["ok"] else "LỖI\n\n") + res["final"][:4000])

    elif cmd == "/status" and arg:
        t = orch.get_task(arg.strip())
        send(chat_id, json.dumps(t, ensure_ascii=False, indent=1)[:4000] if t else "Không tìm thấy task.")

    elif cmd == "/tasks":
        rows = orch.list_tasks(10)
        send(chat_id, "\n".join(f"{r['id']} | {r['status']} | {r['title'][:40]}" for r in rows) or "Chưa có task.")

    elif cmd == "/agents":
        lines = []
        for name in list_agents():
            try:
                ok = get_agent(name).check()
            except Exception:
                ok = False
            lines.append(f"{'🟢' if ok else '🔴'} {name}")
        send(chat_id, "\n".join(lines))

    else:
        send(chat_id, "Lệnh: /task <mô tả> | /parallel (nhiều dòng) | /status <id> | /tasks | /agents")


def main():
    if not TOKEN:
        print("Thiếu TELEGRAM_BOT_TOKEN")
        return
    print("Bot đang chạy... Ctrl+C để dừng.")
    offset = 0
    while True:
        try:
            r = tg("getUpdates", offset=offset, timeout=30)
            for up in r.get("result", []):
                offset = up["update_id"] + 1
                if "message" in up:
                    handle(up["message"])
        except Exception as e:
            print("poll lỗi:", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
