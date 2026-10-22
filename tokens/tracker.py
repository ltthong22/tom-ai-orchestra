"""Theo dõi token usage cho mọi API call.

- Mỗi call ghi: model, input/output tokens, thời gian, task_id, agent
- Ước lượng token TRƯỚC khi chạy (prompt length + history)
- Cảnh báo khi vượt ngưỡng (config TOKEN_WARN_THRESHOLD)
- Gợi ý model rẻ hơn cho task đơn giản
"""
import os
import sqlite3
import time
from datetime import datetime, date

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "orchestra.db")

# Giá ước tính USD / 1M tokens (input, output). Cập nhật khi cần.
MODEL_PRICING = {
    "gemini-3-flash-preview": (0.0, 0.0),       # free tier
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "claude": (3.00, 15.00),                     # ước tính chung
    "lmstudio": (0.0, 0.0),                      # local = miễn phí
    "groq": (0.0, 0.0),                          # free tier
    "deepseek": (0.27, 1.10),
    "openrouter": (0.0, 0.0),                    # nhiều model free
}

# Model rẻ đề xuất thay thế cho task đơn giản
CHEAP_ALTERNATIVE = {
    "gemini-2.5-pro": "gemini-3-flash-preview",
    "claude": "gemini-3-flash-preview",
}

# Task đơn giản (kind) -> luôn dùng model rẻ
SIMPLE_KINDS = {"summarize", "classify", "extract", "translate", "quick_qa"}

_tracker = None


def estimate_tokens(text: str) -> int:
    """Ước lượng token từ text: ~4 ký tự = 1 token (tiếng Anh),
    tiếng Việt ~2.5 ký tự = 1 token. Lấy trung bình an toàn."""
    if not text:
        return 0
    # heuristic: 3.2 chars/token
    return max(1, int(len(text) / 3.2))


def estimate_cost(model: str, in_tokens: int, out_tokens: int) -> float:
    """Ước lượng chi phí USD."""
    pin, pout = MODEL_PRICING.get(model, (0.0, 0.0))
    return (in_tokens * pin + out_tokens * pout) / 1_000_000


def suggest_cheaper_model(model: str, kind: str = "") -> str | None:
    """Gợi ý model rẻ hơn nếu task đơn giản hoặc model đắt."""
    if kind in SIMPLE_KINDS:
        return "gemini-3-flash-preview"
    return CHEAP_ALTERNATIVE.get(model)


class TokenTracker:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()
        self.warn_threshold = int(os.environ.get("TOKEN_WARN_THRESHOLD", "100000"))

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        con = sqlite3.connect(self.db_path)
        con.execute("""CREATE TABLE IF NOT EXISTS token_usage(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, task_id TEXT, subtask_id TEXT,
            agent TEXT, model TEXT,
            input_tokens INTEGER, output_tokens INTEGER,
            duration_ms INTEGER, estimated INTEGER DEFAULT 0)""")
        con.execute("""CREATE INDEX IF NOT EXISTS idx_token_ts ON token_usage(ts)""")
        con.execute("""CREATE INDEX IF NOT EXISTS idx_token_task ON token_usage(task_id)""")
        con.commit()
        con.close()

    def log(self, agent: str, model: str, prompt: str, output: str,
            duration_ms: int = 0, task_id: str = "", subtask_id: str = "",
            input_tokens: int = 0, output_tokens: int = 0):
        """Ghi 1 API call. Nếu API không trả token count, ước lượng."""
        if not input_tokens:
            input_tokens = estimate_tokens(prompt)
            estimated = 1
        else:
            estimated = 0
        if not output_tokens:
            output_tokens = estimate_tokens(output)
        con = sqlite3.connect(self.db_path)
        con.execute(
            """INSERT INTO token_usage
               (ts, task_id, subtask_id, agent, model,
                input_tokens, output_tokens, duration_ms, estimated)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (datetime.now().isoformat(timespec="seconds"),
             task_id, subtask_id, agent, model,
             input_tokens, output_tokens, duration_ms, estimated))
        con.commit()
        con.close()

    def check_budget(self, prompt: str, kind: str = "") -> dict:
        """Ước lượng TRƯỚC khi chạy. Trả về dict có warning nếu vượt ngưỡng."""
        est_in = estimate_tokens(prompt)
        # output ước lượng = 30% input cho task thường
        est_out = int(est_in * 0.3)
        total = est_in + est_out
        warn = total > self.warn_threshold
        cheaper = suggest_cheaper_model("", kind)
        return {
            "est_input": est_in,
            "est_output": est_out,
            "est_total": total,
            "warn": warn,
            "threshold": self.warn_threshold,
            "suggest_model": cheaper,
        }

    def summary_by_agent(self, days: int = 30) -> list:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """SELECT agent, model,
                      SUM(input_tokens) AS tin, SUM(output_tokens) AS tout,
                      COUNT(*) AS calls,
                      SUM(duration_ms) AS ms
               FROM token_usage
               WHERE ts >= datetime('now', ?)
               GROUP BY agent, model ORDER BY tin DESC""",
            (f"-{days} days",)).fetchall()
        con.close()
        out = []
        for r in rows:
            d = dict(r)
            d["est_cost_usd"] = round(estimate_cost(d["model"], d["tin"] or 0, d["tout"] or 0), 4)
            out.append(d)
        return out

    def summary_by_task(self, limit: int = 20) -> list:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """SELECT task_id,
                      SUM(input_tokens) AS tin, SUM(output_tokens) AS tout,
                      COUNT(*) AS calls
               FROM token_usage
               WHERE task_id != ''
               GROUP BY task_id ORDER BY tin DESC LIMIT ?""",
            (limit,)).fetchall()
        con.close()
        return [dict(r) for r in rows]

    def daily(self, days: int = 14) -> list:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """SELECT substr(ts,1,10) AS day,
                      SUM(input_tokens) AS tin, SUM(output_tokens) AS tout,
                      COUNT(*) AS calls
               FROM token_usage
               WHERE ts >= datetime('now', ?)
               GROUP BY day ORDER BY day DESC""",
            (f"-{days} days",)).fetchall()
        con.close()
        return [dict(r) for r in rows]

    def total(self) -> dict:
        con = sqlite3.connect(self.db_path)
        r = con.execute(
            "SELECT SUM(input_tokens), SUM(output_tokens), COUNT(*) FROM token_usage"
        ).fetchone()
        con.close()
        return {"input": r[0] or 0, "output": r[1] or 0, "calls": r[2] or 0}


def get_tracker() -> TokenTracker:
    global _tracker
    if _tracker is None:
        _tracker = TokenTracker()
    return _tracker
