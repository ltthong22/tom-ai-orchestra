"""Auth: username + password hash (pbkdf2_hmac sha256, 200k vòng), lưu SQLite.
Tương thích ngược: hash sha256 1 vòng cũ vẫn verify được; khi login đúng
bằng hash cũ thì tự re-hash sang pbkdf2 và lưu lại (migration trong suốt)."""
import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timedelta

DB_PATH = "data/orchestra.db"
PBKDF2_ROUNDS = 200_000
SESSION_TTL_DAYS = 7


def _hash(password: str, salt: str) -> str:
    """Hash mới: pbkdf2_hmac sha256 200k vòng. Format: 'pbkdf2$<hex>'."""
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS)
    return "pbkdf2$" + dk.hex()


def _hash_legacy(password: str, salt: str) -> str:
    """Hash cũ (sha256 1 vòng) — chỉ dùng để verify tương thích ngược."""
    return hashlib.sha256((salt + password).encode()).hexdigest()


def init_db(db_path: str = DB_PATH):
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE IF NOT EXISTS users(
        username TEXT PRIMARY KEY, salt TEXT, pw_hash TEXT, created_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS sessions(
        token TEXT PRIMARY KEY, username TEXT, created_at TEXT, csrf TEXT)""")
    # migration cho DB cũ chưa có cột csrf
    try:
        con.execute("ALTER TABLE sessions ADD COLUMN csrf TEXT")
    except sqlite3.OperationalError:
        pass
    con.commit()
    con.close()


def create_user(username: str, password: str, db_path: str = DB_PATH) -> bool:
    init_db(db_path)
    salt = secrets.token_hex(16)
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT INTO users VALUES(?,?,?,datetime('now'))",
            (username, salt, _hash(password, salt)))
        con.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        con.close()


def verify(username: str, password: str, db_path: str = DB_PATH) -> bool:
    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT salt, pw_hash FROM users WHERE username=?", (username,)).fetchone()
    if not row:
        con.close()
        return False
    salt, stored = row
    if stored.startswith("pbkdf2$"):
        ok = secrets.compare_digest(_hash(password, salt), stored)
    else:
        # hash cũ sha256 1 vòng — thử tương thích ngược, khớp thì re-hash sang pbkdf2
        ok = secrets.compare_digest(_hash_legacy(password, salt), stored)
        if ok:
            con.execute("UPDATE users SET pw_hash=? WHERE username=?",
                        (_hash(password, salt), username))
            con.commit()
    con.close()
    return ok


def make_session(username: str, db_path: str = DB_PATH) -> str:
    token = secrets.token_hex(32)
    csrf = secrets.token_urlsafe(32)
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO sessions VALUES(?,?,datetime('now'),?)",
                (token, username, csrf))
    con.commit()
    con.close()
    return token


def get_csrf(token: str, db_path: str = DB_PATH) -> str | None:
    """Lấy CSRF token của session; tự sinh nếu session cũ chưa có."""
    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT csrf FROM sessions WHERE token=?", (token,)).fetchone()
    if not row:
        con.close()
        return None
    csrf = row[0]
    if not csrf:
        csrf = secrets.token_urlsafe(32)
        con.execute("UPDATE sessions SET csrf=? WHERE token=?", (csrf, token))
        con.commit()
    con.close()
    return csrf


def check_session(token: str, db_path: str = DB_PATH) -> str | None:
    """Trả username nếu session hợp lệ và chưa quá 7 ngày; quá hạn thì hủy."""
    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT username, created_at FROM sessions WHERE token=?", (token,)).fetchone()
    con.close()
    if not row:
        return None
    username, created_at = row
    try:
        age = datetime.now() - datetime.fromisoformat(created_at)
    except Exception:
        return None
    if age > timedelta(days=SESSION_TTL_DAYS):
        destroy_session(token, db_path)
        return None
    return username


def destroy_session(token: str, db_path: str = DB_PATH):
    con = sqlite3.connect(db_path)
    con.execute("DELETE FROM sessions WHERE token=?", (token,))
    con.commit()
    con.close()


if __name__ == "__main__":
    import getpass
    init_db()
    u = input("Username [tom]: ").strip() or "tom"
    p = getpass.getpass("Password: ")
    if create_user(u, p):
        print(f"Đã tạo user '{u}'")
    else:
        print(f"User '{u}' đã tồn tại")
