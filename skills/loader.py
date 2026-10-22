"""Load và index toàn bộ skills từ ~/workspace/custom-skills/.

Mỗi skill là 1 thư mục local-*, có file SKILL.md.
Tạo skills_registry.json để tra cứu nhanh.
"""
import json
import os
import re

SKILLS_DIR = os.path.expanduser("~/workspace/custom-skills")
REGISTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills_registry.json")

_cache = None


def _extract_summary(md_text: str, max_lines: int = 8) -> str:
    """Lấy mô tả ngắn từ SKILL.md: dòng đầu có ý nghĩa."""
    lines = []
    for line in md_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            continue
        # bỏ markdown link/image syntax
        line = re.sub(r"!\[.*?\]\(.*?\)", "", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        if len(line) > 20:
            lines.append(line)
        if len(lines) >= max_lines:
            break
    return " ".join(lines)[:400]


def _extract_triggers(md_text: str) -> list:
    """Tìm từ khóa trigger trong SKILL.md (dòng 'Use when', 'Trigger', ...)."""
    triggers = []
    for line in md_text.splitlines():
        low = line.lower().strip()
        if low.startswith(("use when", "use this when", "trigger", "dùng khi")):
            triggers.append(line.strip()[:200])
    return triggers[:5]


def load_all_skills(force: bool = False) -> dict:
    """Đọc tất cả skills, trả về dict {skill_name: {...}}. Cache trong RAM."""
    global _cache
    if _cache is not None and not force:
        return _cache
    skills = {}
    if not os.path.isdir(SKILLS_DIR):
        return skills
    for name in sorted(os.listdir(SKILLS_DIR)):
        path = os.path.join(SKILLS_DIR, name)
        md_path = os.path.join(path, "SKILL.md")
        if not os.path.isdir(path) or not os.path.isfile(md_path):
            continue
        try:
            with open(md_path, encoding="utf-8", errors="ignore") as f:
                md = f.read()
        except OSError:
            continue
        # tiêu đề = dòng # đầu tiên
        title = name
        m = re.search(r"^#\s+(.+)$", md, re.M)
        if m:
            title = m.group(1).strip()
        skills[name] = {
            "name": name,
            "title": title,
            "summary": _extract_summary(md),
            "triggers": _extract_triggers(md),
            "path": md_path,
            "size_kb": round(os.path.getsize(md_path) / 1024, 1),
        }
    _cache = skills
    return skills


def save_registry() -> str:
    """Ghi skills_registry.json, trả về đường dẫn."""
    skills = load_all_skills(force=True)
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"count": len(skills), "skills": skills},
            f, ensure_ascii=False, indent=1,
        )
    return REGISTRY_PATH


def search_skills(query: str, limit: int = 5) -> list:
    """Tìm skill theo từ khóa (tên, tiêu đề, mô tả, trigger)."""
    q = query.lower()
    skills = load_all_skills()
    scored = []
    for name, s in skills.items():
        score = 0
        blob = " ".join([name, s["title"], s["summary"], " ".join(s["triggers"])]).lower()
        for word in q.split():
            if len(word) < 2:
                continue
            if word in name.lower():
                score += 3
            elif word in s["title"].lower():
                score += 2
            elif word in blob:
                score += 1
        if score > 0:
            scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:limit]]


def suggest_skills(task_description: str, limit: int = 3) -> list:
    """Gợi ý skill phù hợp cho 1 task. Dùng cho orchestrator."""
    return search_skills(task_description, limit=limit)


def get_skill(name: str) -> dict | None:
    """Lấy chi tiết 1 skill (kèm nội dung SKILL.md đầy đủ)."""
    skills = load_all_skills()
    s = skills.get(name)
    if not s:
        return None
    try:
        with open(s["path"], encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except OSError:
        content = ""
    return {**s, "content": content[:20000]}
