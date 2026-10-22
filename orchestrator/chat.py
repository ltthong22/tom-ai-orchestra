"""Chat Messenger-style cho Orchestra: lưu tin nhắn user + AI (kèm tên AI)."""
import sqlite3
import uuid
from datetime import datetime

DB_PATH = "data/orchestra.db"


def _db():
    import os
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS chat_messages(
        id TEXT PRIMARY KEY,
        role TEXT,          -- user / ai
        agent TEXT,         -- gemini/muse/deepseek/lmstudio (null nếu user)
        content TEXT,
        created_at TEXT)""")
    con.commit()
    return con


def save_message(role: str, content: str, agent: str = None) -> dict:
    msg = {
        "id": "msg-" + uuid.uuid4().hex[:8],
        "role": role,
        "agent": agent,
        "content": content,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    con = _db()
    con.execute("INSERT INTO chat_messages VALUES(?,?,?,?,?)",
                (msg["id"], msg["role"], msg["agent"], msg["content"], msg["created_at"]))
    con.commit()
    con.close()
    return msg


def get_history(limit: int = 50) -> list:
    con = _db()
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM chat_messages ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    return [dict(r) for r in reversed(rows)]


def clear_history():
    con = _db()
    con.execute("DELETE FROM chat_messages")
    con.commit()
    con.close()
