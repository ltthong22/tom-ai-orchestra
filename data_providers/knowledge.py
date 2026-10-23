"""Kho Kiến Thức AI chung cho mọi agent Orchestra.

Đọc từ folder "Kho Kiến Thức AI" trên Google Drive:
- Skills/ — 93 skills
- Quy tac studio/ — quy tắc nghiệp vụ
- Tai chinh/ — số liệu Finance
- Yeu cau/ — yêu cầu & quyết định của anh Thống

Cache local 1h. force_reload() để học lại ngay.
"""
import json
import os
import subprocess
import time

CLI = "/opt/hatch/bin/hatch_gws_cli"
CACHE_DIR = "/tmp/ofc-kb-cache"
CACHE_TTL = 3600  # 1h

KB_ROOT = "1GYwsn2uiQ817qYRlZuFsCLUC9eJBk-nO"
SUBFOLDERS = {
    "skills": "1hnE7eMwefj5eapF-k3Z5hJU-N5vZ8NdZ",
    "rules": "1GoC9gWuYxGt_mFKm2MsykPnahcdg5pPF",
    "finance": "18Uy-PL9zGXlw_y6U82P9QRZXu5PWlhFX",
    "requests": "1SkpVgrIH8YkxAvSyYEHfha2lY41vJ6gW",
}

FOLDER_MIME = "application/vnd.google-apps.folder"


def _cli(*args, timeout=20):
    r = subprocess.run([CLI] + list(args), capture_output=True,
                       text=True, timeout=timeout)
    return json.loads(r.stdout) if r.stdout.strip() else {}


def _list_files(folder_id):
    """Liệt kê file trong 1 folder Drive."""
    try:
        res = _cli("drive", "files", "list", "--params", json.dumps({
            "q": f"'{folder_id}' in parents and trashed=false "
                 f"and mimeType != '{FOLDER_MIME}'",
            "fields": "files(id,name,modifiedTime,size)",
            "pageSize": 200,
        }))
        return res.get("files", [])
    except Exception:
        return []


def _read_file(file_id):
    """Đọc nội dung text của 1 file Drive."""
    try:
        tmp = f"/tmp/ofc-kb-dl-{file_id[:12]}.txt"
        subprocess.run(
            [CLI, "drive", "files", "get",
             "--params", json.dumps({"fileId": file_id, "alt": "media"}),
             "--output", tmp],
            capture_output=True, text=True, timeout=20)
        if os.path.exists(tmp):
            content = open(tmp, encoding="utf-8", errors="ignore").read()
            os.remove(tmp)
            return content
        return ""
    except Exception:
        return ""


def _cache_path(name):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, name + ".json")


def _cache_valid(name):
    p = _cache_path(name)
    return os.path.exists(p) and time.time() - os.path.getmtime(p) < CACHE_TTL


def load(force=False):
    """Nạp toàn bộ kho kiến thức. Trả về dict."""
    cp = _cache_path("kb")
    if not force and _cache_valid("kb"):
        try:
            return json.load(open(cp))
        except Exception:
            pass
    kb = {"skills": [], "rules": "", "finance": "", "requests": "",
          "counts": {}, "loaded_at": time.strftime("%H:%M %d/%m/%Y")}
    # skills: chỉ lấy index + tên file (nhẹ)
    files = _list_files(SUBFOLDERS["skills"])
    kb["counts"]["skills"] = len([f for f in files if f["name"] != "_INDEX.md"])
    kb["skills"] = [f["name"].replace(".md", "") for f in files
                    if f["name"] != "_INDEX.md"][:50]
    # rules: đọc full
    for f in _list_files(SUBFOLDERS["rules"]):
        kb["rules"] += _read_file(f["id"]) + "\n"
        break  # chỉ file đầu
    kb["counts"]["rules"] = kb["rules"].count("\n- ") + kb["rules"].count("\n-")
    # finance: đọc full
    for f in _list_files(SUBFOLDERS["finance"]):
        kb["finance"] = _read_file(f["id"])
        kb["finance_file"] = f["name"]
        kb["finance_updated"] = f.get("modifiedTime", "")[:10]
        break
    # requests: đọc full
    for f in _list_files(SUBFOLDERS["requests"]):
        kb["requests"] = _read_file(f["id"])
        break
    kb["counts"]["requests"] = 1 if kb["requests"] else 0
    try:
        json.dump(kb, open(cp, "w"))
    except Exception:
        pass
    return kb


def force_reload():
    """Cho học lại ngay — xóa cache rồi nạp lại."""
    return load(force=True)


def status():
    """Trạng thái nhanh cho badge (dùng cache)."""
    kb = load()
    return {
        "skills": kb["counts"].get("skills", 0),
        "rules": kb["counts"].get("rules", 0),
        "loaded_at": kb.get("loaded_at", "?"),
        "drive_link": "https://drive.google.com/drive/folders/" + KB_ROOT,
    }


def agent_context():
    """Context inject vào prompt agent: rules + finance mới nhất."""
    kb = load()
    parts = ["[KHO KIẾN THỨC CHUNG — mọi agent đều đã học]"]
    if kb["rules"]:
        parts.append(kb["rules"][:2000])
    if kb["finance"]:
        parts.append("[SỐ LIỆU TÀI CHÍNH MỚI NHẤT]\n" + kb["finance"][:1500])
    if kb["skills"]:
        parts.append(f"[SKILLS KHẢ DỤNG: {kb['counts'].get('skills', 0)} skills]")
    return "\n\n".join(parts)
