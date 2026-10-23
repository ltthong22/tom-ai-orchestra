"""Đọc token usage từ bảng token_usage (DB local của Orchestra).

Dùng cho câu hỏi kiểu: "hôm nay xài bao nhiêu token", "AI nào tốn nhất".
"""
import os
import sqlite3
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "orchestra.db")

# Giá USD / 1M tokens (input, output) — đồng bộ với tokens/tracker.py
PRICING = {
    "gemini-3-flash-preview": (0.0, 0.0),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "claude": (3.00, 15.00),
    "lmstudio": (0.0, 0.0),
    "deepseek": (0.27, 1.10),
    "groq": (0.0, 0.0),
    "openrouter": (0.0, 0.0),
}

AI_LABEL = {
    "gemini": "Gemini", "Muse": "Muse", "lmstudio": "LM Studio",
    "deepseek": "DeepSeek", "claude": "Claude",
}


def _con():
    return sqlite3.connect(DB_PATH)


def get_stats(days: int = 30) -> dict:
    """Tổng hợp token theo AI trong N ngày gần nhất."""
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    con = _con()
    rows = con.execute(
        """SELECT agent, model,
                  COUNT(*) AS calls,
                  SUM(input_tokens) AS inp,
                  SUM(output_tokens) AS outp
           FROM token_usage WHERE ts >= ?
           GROUP BY agent, model ORDER BY inp+outp DESC""",
        (since,)).fetchall()
    con.close()
    by_ai, total_in, total_out, total_calls = {}, 0, 0, 0
    for agent, model, calls, inp, outp in rows:
        inp, outp = inp or 0, outp or 0
        price_in, price_out = PRICING.get(model or "", (0.0, 0.0))
        cost = inp / 1e6 * price_in + outp / 1e6 * price_out
        by_ai.setdefault(agent, {"calls": 0, "in": 0, "out": 0, "cost_usd": 0.0})
        by_ai[agent]["calls"] += calls
        by_ai[agent]["in"] += inp
        by_ai[agent]["out"] += outp
        by_ai[agent]["cost_usd"] += cost
        total_in += inp
        total_out += outp
        total_calls += calls
    return {
        "days": days,
        "total_tokens": total_in + total_out,
        "total_in": total_in, "total_out": total_out,
        "total_calls": total_calls,
        "total_cost_usd": round(sum(a["cost_usd"] for a in by_ai.values()), 4),
        "by_ai": {k: {"label": AI_LABEL.get(k, k), **v,
                       "cost_usd": round(v["cost_usd"], 4)}
                  for k, v in by_ai.items()},
    }


def format_stats(days: int = 30) -> str:
    """Text gọn để inject vào prompt cho AI."""
    s = get_stats(days)
    if not s["total_calls"]:
        return f"Chưa có dữ liệu token usage trong {days} ngày qua."
    lines = [f"📊 Token usage {days} ngày qua:",
             f"• Tổng: {s['total_tokens']:,} tokens "
             f"(in {s['total_in']:,}, out {s['total_out']:,}), "
             f"{s['total_calls']} calls, ~${s['total_cost_usd']}"]
    for ai, d in sorted(s["by_ai"].items(),
                        key=lambda x: x[1]["in"] + x[1]["out"], reverse=True):
        lines.append(f"• {d['label']}: {d['in']+d['out']:,} tokens, "
                     f"{d['calls']} calls, ~${d['cost_usd']}")
    return "\n".join(lines)


def get_today() -> str:
    """Token hôm nay (ngắn gọn)."""
    since = datetime.now().replace(hour=0, minute=0, second=0).isoformat(timespec="seconds")
    con = _con()
    rows = con.execute(
        """SELECT agent, COUNT(*), SUM(input_tokens), SUM(output_tokens)
           FROM token_usage WHERE ts >= ? GROUP BY agent""", (since,)).fetchall()
    con.close()
    if not rows:
        return "Hôm nay chưa xài token nào."
    parts = []
    total = 0
    for agent, calls, inp, outp in rows:
        t = (inp or 0) + (outp or 0)
        total += t
        parts.append(f"{AI_LABEL.get(agent, agent)} {t:,} ({calls} calls)")
    return f"Hôm nay: {total:,} tokens — " + ", ".join(parts)
