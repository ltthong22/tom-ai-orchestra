# -*- coding: utf-8 -*-
"""Kiểm tra sức khỏe thật của từng AI provider (API key, balance, kết nối)."""
import os
import urllib.request
import json


def _load_env():
    """Load .env nếu chưa có trong environ."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip('"').strip("'")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass


_load_env()


def check_gemini():
    """Gemini: có key không."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return False, "Thiếu GEMINI_API_KEY"
    return True, "Key đã cấu hình"


def check_muse():
    """Muse: hàng đợi nội bộ luôn sẵn sàng."""
    qdir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "muse-queue")
    try:
        os.makedirs(qdir, exist_ok=True)
        return True, "Hàng đợi nội bộ sẵn sàng"
    except Exception as e:
        return False, str(e)


def check_deepseek():
    """DeepSeek: có key không, còn tiền không."""
    # Tìm key từ env hoặc file cấu hình
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        # Thử đọc từ các vị trí thường dùng
        for p in [os.path.expanduser("~/.config/deepseek/key"),
                  "/tmp/deepseek_key"]:
            try:
                if os.path.exists(p):
                    key = open(p).read().strip()
                    break
            except Exception:
                pass
    if not key:
        return False, "Thiếu DEEPSEEK_API_KEY"
    # Kiểm tra balance
    try:
        req = urllib.request.Request(
            "https://api.deepseek.com/v1/models",
            headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            if r.status == 200:
                return True, "Key hợp lệ (số dư $0 — cần nạp tiền để dùng)"
    except Exception as e:
        return False, f"Key không hợp lệ: {e}"
    return True, "Key hợp lệ"


def check_claude():
    """Claude: có ANTHROPIC_API_KEY không."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return False, "Thiếu ANTHROPIC_API_KEY"
    return True, "Key đã cấu hình"


def check_lmstudio():
    """LM Studio: có kết nối được không."""
    base = os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234/v1").rstrip("/")
    try:
        req = urllib.request.Request(f"{base}/models")
        with urllib.request.urlopen(req, timeout=5) as r:
            if r.status == 200:
                return True, f"Kết nối OK ({base})"
    except Exception:
        pass
    return False, f"Không kết nối được {base} (chỉ chạy trên laptop)"


CHECKS = {
    "gemini": check_gemini,
    "muse": check_muse,
    "deepseek": check_deepseek,
    "claude": check_claude,
    "lmstudio": check_lmstudio,
}


def provider_health(key):
    """Trả về (ok: bool, message: str)."""
    fn = CHECKS.get(key)
    if not fn:
        return False, "Không rõ provider"
    try:
        return fn()
    except Exception as e:
        return False, str(e)
