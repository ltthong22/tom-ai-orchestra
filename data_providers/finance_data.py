"""Đọc số liệu tài chính từ Finance Manager (app riêng của anh Thống).

Cách hoạt động: đăng nhập HTTP bằng tài khoản trong env rồi đọc trang
dashboard (chỉ GET, không POST gì ngoài /login). KHÔNG ghi/sửa dữ liệu.

Env cần thiết:
  FINANCE_APP_URL  (vd https://mediafinance.up.railway.app)
  FINANCE_APP_USER / FINANCE_APP_PASS

Nếu chưa cấu hình -> trả về thông báo rõ ràng, không crash.
"""
import os
import re
import time
import urllib.request
import urllib.parse
import http.cookiejar

_CACHE = {"ts": 0, "data": None}
CACHE_TTL = 300  # 5 phút


def _cfg():
    return (os.environ.get("FINANCE_APP_URL", "").rstrip("/"),
            os.environ.get("FINANCE_APP_USER", ""),
            os.environ.get("FINANCE_APP_PASS", ""))


def _login(base, user, pw):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    data = urllib.parse.urlencode({"username": user, "password": pw}).encode()
    req = urllib.request.Request(base + "/login", data=data, method="POST")
    try:
        with opener.open(req, timeout=20) as r:
            # login thành công -> redirect về / (303) hoặc 200 có session
            return opener if r.status in (200, 303) else None
    except Exception:
        return None


def _get(opener, base, path):
    try:
        with opener.open(base + path, timeout=20) as r:
            if r.status == 200:
                return r.read().decode("utf-8", errors="ignore")
    except Exception:
        pass
    return None


def _num(text):
    """'9.433.217đ' -> 9433217"""
    d = re.sub(r"[^\d]", "", text or "")
    return int(d) if d else 0


def fetch_dashboard() -> dict | None:
    """Lấy số liệu dashboard. Trả về None nếu chưa cấu hình hoặc lỗi."""
    global _CACHE
    if time.time() - _CACHE["ts"] < CACHE_TTL and _CACHE["data"]:
        return _CACHE["data"]
    base, user, pw = _cfg()
    if not (base and user and pw):
        return None
    opener = _login(base, user, pw)
    if not opener:
        return None
    html = _get(opener, base, "/")
    if not html or "Tổng quan" not in html:
        return None
    # Parse các thẻ stat: <div class="k">Tên</div><div class="v...">Giá trị</div>
    stats = {}
    for m in re.finditer(
            r'<div class="k">([^<]+)</div>\s*<div class="v[^"]*">([^<]+)</div>', html):
        stats[m.group(1).strip()] = m.group(2).strip()
    # Công nợ quá hạn
    overdue = re.findall(r"còn nợ <b[^>]*>([^<]+)</b>", html)
    data = {"stats": stats, "overdue": overdue[:10]}
    _CACHE = {"ts": time.time(), "data": data}
    return data


def is_configured() -> bool:
    base, user, pw = _cfg()
    return bool(base and user and pw)


def format_summary() -> str:
    """Text gọn để inject vào prompt cho AI."""
    if not is_configured():
        return ("Chưa nối Finance Manager (thiếu FINANCE_APP_URL/USER/PASS). "
                "Nhờ anh Thống cung cấp mật khẩu app tài chính để bật.")
    d = fetch_dashboard()
    if not d:
        return "Không đọc được Finance Manager lúc này (lỗi đăng nhập hoặc mạng)."
    lines = ["💰 Số liệu tài chính (Finance Manager):"]
    for k, v in d["stats"].items():
        lines.append(f"• {k}: {v}")
    if d["overdue"]:
        lines.append("Công nợ quá 2 tuần: " + ", ".join(d["overdue"][:5]))
    return "\n".join(lines)
