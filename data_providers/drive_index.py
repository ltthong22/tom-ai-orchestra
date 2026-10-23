"""Chỉ mục Drive cho Orchestra: đồng bộ DANH SÁCH + metadata file/folder
vào SQLite local để AI tìm kiếm nhanh. KHÔNG tải nội dung file về.

Bảng drive_index: id, name, mime_type, parent_id, path, size, modified_time,
web_link, indexed_at. Tìm kiếm qua FTS5 (drive_index_fts).

Dùng: build_index() chạy nền (lần đầu ~vài phút), search(q) để tìm.
"""
import json
import os
import sqlite3
import subprocess
import time

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "orchestra.db")
CLI = "/opt/hatch/bin/hatch_gws_cli"

# Folder gốc cần index (id lấy từ memory của anh Thống)
ROOTS = {
    "Khach": "1qx_4oW3_kypvVHl9ZKZUDpA1wigCeLCH",
    "Khach 2": "1H70PzxqKAcIzrhgE8-LDkB2IymNHCAOr",
    "Tom Studio Projects": "16_RI2uuQC21enUngdqHI7eJLP2MasWRQ",
    "Studio Manager": "1dqyRbEw6fud5wxRhlRdSEbgtZx5WabBP",
}

FOLDER_MIME = "application/vnd.google-apps.folder"


def _db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS drive_index(
        id TEXT PRIMARY KEY, name TEXT, mime_type TEXT, parent_id TEXT,
        path TEXT, size INTEGER DEFAULT 0, modified_time TEXT,
        web_link TEXT, indexed_at TEXT)""")
    con.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS drive_index_fts
        USING fts5(name, path, content='drive_index', content_rowid='rowid')""")
    return con


def _cli(*args):
    r = subprocess.run([CLI] + list(args), capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:300])
    try:
        return json.loads(r.stdout)
    except Exception:
        return {}


def _list_children(folder_id, page_token=None):
    params = {"q": f"'{folder_id}' in parents and trashed=false",
              "pageSize": 1000,
              "fields": "nextPageToken,files(id,name,mimeType,parents,size,modifiedTime,webViewLink)"}
    if page_token:
        params["pageToken"] = page_token
    return _cli("drive", "files", "list", "--params", json.dumps(params))


def build_index() -> dict:
    """Crawl toàn bộ folder gốc, lưu metadata. Trả về thống kê."""
    con = _db()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    total_files, total_folders = 0, 0

    def crawl(folder_id, path):
        nonlocal total_files, total_folders
        page_token = None
        while True:
            res = _list_children(folder_id, page_token)
            for f in res.get("files", []):
                fid = f["id"]
                name = f.get("name", "")
                mime = f.get("mimeType", "")
                fpath = f"{path}/{name}"
                size = int(f.get("size") or 0)
                con.execute(
                    """INSERT OR REPLACE INTO drive_index
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (fid, name, mime, folder_id, fpath, size,
                     f.get("modifiedTime", ""), f.get("webViewLink", ""), now))
                if mime == FOLDER_MIME:
                    total_folders += 1
                    crawl(fid, fpath)
                else:
                    total_files += 1
            page_token = res.get("nextPageToken")
            if not page_token:
                break
        con.commit()

    for label, fid in ROOTS.items():
        crawl(fid, label)
    # rebuild FTS
    con.execute("DELETE FROM drive_index_fts")
    con.execute("""INSERT INTO drive_index_fts(rowid, name, path)
                   SELECT rowid, name, path FROM drive_index""")
    con.commit()
    stats = {"files": total_files, "folders": total_folders,
             "indexed_at": now}
    con.execute("""CREATE TABLE IF NOT EXISTS drive_index_meta(k TEXT PRIMARY KEY, v TEXT)""")
    con.execute("INSERT OR REPLACE INTO drive_index_meta VALUES('last_build', ?)",
                (json.dumps(stats),))
    con.commit()
    con.close()
    return stats


def search(query: str, limit: int = 15) -> list:
    """Tìm file/folder theo tên. Trả về list dict."""
    con = _db()
    con.row_factory = sqlite3.Row
    # FTS5 query: escape ký tự đặc biệt, tìm từng từ
    terms = [t for t in query.split() if t]
    if not terms:
        return []
    fts_q = " AND ".join(f'"{t}"' for t in terms)
    try:
        rows = con.execute(
            """SELECT d.id, d.name, d.mime_type, d.path, d.size,
                      d.modified_time, d.web_link
               FROM drive_index_fts f JOIN drive_index d ON d.rowid = f.rowid
               WHERE drive_index_fts MATCH ? LIMIT ?""",
            (fts_q, limit)).fetchall()
    except Exception:
        # fallback LIKE nếu FTS lỗi
        like = f"%{query}%"
        rows = con.execute(
            """SELECT id, name, mime_type, path, size, modified_time, web_link
               FROM drive_index WHERE name LIKE ? OR path LIKE ? LIMIT ?""",
            (like, like, limit)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def index_status() -> dict:
    con = _db()
    row = con.execute(
        "SELECT v FROM drive_index_meta WHERE k='last_build'").fetchone()
    n = con.execute("SELECT COUNT(*) FROM drive_index").fetchone()[0]
    con.close()
    return {"count": n,
            "last_build": json.loads(row[0]) if row else None}


def format_search(query: str, limit: int = 10) -> str:
    """Text gọn để inject vào prompt."""
    st = index_status()
    if not st["count"]:
        return "Chỉ mục Drive chưa được xây dựng."
    hits = search(query, limit)
    if not hits:
        return f"Không tìm thấy gì cho '{query}' trong Drive đã index ({st['count']:,} mục)."
    lines = [f"📁 Tìm '{query}' trong Drive ({len(hits)} kết quả):"]
    for h in hits:
        kind = "📂" if h["mime_type"] == FOLDER_MIME else "📄"
        size = f" ({h['size']/1e6:.1f}MB)" if h["size"] else ""
        lines.append(f"{kind} {h['path']}{size}")
    return "\n".join(lines)
