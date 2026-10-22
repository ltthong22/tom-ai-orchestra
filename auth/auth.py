"""Auth đơn giản: username + password hash (sha256 + salt), lưu SQLite."""
import hashlib
import os
import secrets
import sqlite3

DB_PATH = "data/orchestra.db"


def _hash(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode()).hexdigest()


def init_db(db_path: str = DB_PATH):
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE IF NOT EXISTS users(
        username TEXT PRIMARY KEY, salt TEXT, pw_hash TEXT, created_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS sessions(
        token TEXT PRIMARY KEY, username TEXT, created_at TEXT)""")
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
    con.close()
    if not row:
        return False
    return _hash(password, row[0]) == row[1]


def make_session(username: str, db_path: str = DB_PATH) -> str:
    token = secrets.token_hex(32)
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO sessions VALUES(?,?,datetime('now'))",
                (token, username))
    con.commit()
    con.close()
    return token


def check_session(token: str, db_path: str = DB_PATH) -> str | None:
    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT username FROM sessions WHERE token=?", (token,)).fetchone()
    con.close()
    return row[0] if row else None


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
