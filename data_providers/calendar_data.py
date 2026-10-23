"""Đọc lịch Google Calendar (chỉ đọc) — sự kiện sắp tới.

Dùng hatch_gws_cli calendar. Nhẹ, không lưu DB.
"""
import json
import subprocess
from datetime import datetime, timedelta

CLI = "/opt/hatch/bin/hatch_gws_cli"


def _cli(*args):
    r = subprocess.run([CLI] + list(args), capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return {}
    try:
        return json.loads(r.stdout)
    except Exception:
        return {}


def upcoming(days: int = 7, limit: int = 15) -> list:
    """Sự kiện trong N ngày tới (dùng helper +agenda của CLI)."""
    r = subprocess.run(
        [CLI, "calendar", "+agenda", "--days", str(days), "--format", "json"],
        capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return []
    try:
        res = json.loads(r.stdout)
    except Exception:
        return []
    events = []
    items = res.get("events", [])
    for e in items[:limit]:
        if not isinstance(e, dict):
            continue
        start = e.get("start", "")
        events.append({
            "title": e.get("summary", "(không tên)"),
            "start": str(start)[:16],
            "location": e.get("location", ""),
            "calendar": e.get("calendar", ""),
        })
    return events


def format_upcoming(days: int = 7) -> str:
    evs = upcoming(days)
    if not evs:
        return f"Không có sự kiện nào trong {days} ngày tới."
    lines = [f"📅 Lịch {days} ngày tới:"]
    for e in evs:
        loc = f" @ {e['location']}" if e["location"] else ""
        lines.append(f"• {e['start'][:16]} — {e['title']}{loc}")
    return "\n".join(lines)
