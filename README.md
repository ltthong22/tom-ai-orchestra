# 🎼 Tom AI Orchestra

Hệ thống điều phối đa AI: nhận task → chia cho AI phù hợp → double-check → gộp kết quả → trả về.

## Kiến trúc

```
tom-ai-orchestra/
├── orchestrator/       # Core: chia task, chọn agent, chạy song song, gộp kết quả
├── agents/             # Connector từng AI
│   ├── claude.py       # Việc khó: code, suy luận, quyết định
│   ├── gemini.py       # Việc nhẹ song song + verify độc lập (free tier)
│   └── lmstudio.py     # Local LLM qua http://localhost:1234/v1
├── skills/             # Index 90 skills, search + gợi ý tự động
│   └── skills_registry.json
├── tokens/             # Theo dõi token: log, ước lượng, cảnh báo, tối ưu
├── workflows/          # double_check (3 lớp) / parallel / pipeline
├── auth/               # Login đơn giản (user tom)
├── telegram_bot.py     # Nhận task qua Telegram, trả kết quả về
└── web/                # UI responsive: mobile + desktop, dark mode
```

## Luồng hoạt động

1. Anh gửi task (Telegram hoặc web UI)
2. Orchestrator phân tích → chia subtask
3. Mỗi AI làm phần của mình (song song khi được)
4. Double-check 3 lớp:
   - **Lớp 1:** agent chính làm
   - **Lớp 2:** Gemini verify độc lập (không biết kết quả lớp 1)
   - **Lớp 3:** gộp + chốt
5. Gửi kết quả về Telegram cho anh

## Cài đặt

```bash
git clone https://github.com/ltthong22/tom-ai-orchestra.git
cd tom-ai-orchestra
cp .env.example .env
# Điền key vào .env
```

Cần Python 3.10+. Không cần cài thêm package nào (chỉ dùng thư viện chuẩn).

## Cấu hình (.env)

| Biến | Bắt buộc | Mô tả |
|---|---|---|
| `GEMINI_API_KEY` | cho agent gemini | Lấy free tại https://aistudio.google.com/apikey |
| `ANTHROPIC_API_KEY` | cho agent claude | https://console.anthropic.com |
| `TELEGRAM_BOT_TOKEN` | cho bot | Tạo bot qua @BotFather |
| `TELEGRAM_OWNER_ID` | nên có | ID Telegram của anh (mặc định 8502284486) |
| `LMSTUDIO_BASE_URL` | cho agent lmstudio | Mặc định http://localhost:1234/v1 |

## Cách dùng

**Chạy thử nhanh:**
```bash
python3 quickstart.py
```

**Telegram bot:**
```bash
python3 telegram_bot.py
```
Lệnh: `/task <mô tả>` · `/parallel` (reply nhiều dòng) · `/status <id>` · `/tasks` · `/agents`

**Web UI:**
```bash
# Tạo user trước (lần đầu):
python3 -m auth.auth
# Chạy web:
python3 web/app.py   # mở http://localhost:8099
```

**Dùng trong code:**
```python
from orchestrator import Orchestrator
from workflows import double_check, parallel, pipeline

orch = Orchestrator()

# Double-check 3 lớp
res = double_check.run(orch, "Tóm tắt báo cáo", "Tóm tắt văn bản sau: ...")

# Chạy song song
res = parallel.run(orch, "Việc batch", [
    {"kind": "classify", "prompt": "Phân loại: ..."},
    {"kind": "summarize", "prompt": "Tóm tắt: ..."},
])

# Pipeline nối tiếp ({prev} = output bước trước)
res = pipeline.run(orch, "Chuỗi việc", [
    {"kind": "extract", "prompt": "Trích xuất ý chính: ..."},
    {"kind": "summarize", "prompt": "Tóm tắt lại ngắn gọn: {prev}"},
])
```

## Thêm AI mới

1. Tạo file `agents/tenmoi.py`, kế thừa `BaseAgent`, implement `run()`:

```python
from .base import BaseAgent, AgentResult

class TenMoiAgent(BaseAgent):
    name = "tenmoi"
    capabilities = ["summarize", "classify"]  # năng lực để orchestrator chọn
    parallel_safe = True

    def run(self, prompt, system=None, timeout=120) -> AgentResult:
        # gọi API của bạn ở đây
        return AgentResult(self.name, True, output="...")
```

2. Thêm vào `agents/__init__.py`:
```python
from .tenmoi import TenMoiAgent
AGENT_CLASSES = { ..., "tenmoi": TenMoiAgent }
```

3. (Tuỳ chọn) Thêm gợi ý chọn agent trong `orchestrator/core.py` → `KIND_AGENT_HINT`.

Ví dụ đã chuẩn bị sẵn pattern: `agents/claude.py` (copy để làm Groq/DeepSeek/OpenRouter — chỉ đổi endpoint + env var).

## Bảo mật

- Key qua env var / file `.env`, **không commit** (đã có `.gitignore`)
- Auth web: password hash sha256 + salt, session token
- Telegram bot chỉ trả lời đúng `TELEGRAM_OWNER_ID`

## License

MIT — dùng tự do.

## Web UI (mới)

Responsive mobile-first, dark mode mặc định, CSS thuần (không framework nặng).

- **📊 Tổng quan:** task gần đây + token tổng quan
- **➕ Giao việc:** form tạo task, tự gợi ý skill, ước lượng token trước khi chạy
- **📜 Lịch sử:** bảng task
- **🎟️ Token:** biểu đồ theo AI / ngày / task tốn nhất, ước tính chi phí
- **🧠 Skills:** tìm kiếm 90 skills, xem chi tiết
- **⚙️ Cài đặt:** trạng thái AI, ngưỡng cảnh báo token

Chạy: `python3 web/app.py` → `http://localhost:8099`
Trên điện thoại: `http://<ip-laptop>:8099` (cùng mạng WiFi)

## Skills (mới)

90 skills từ `~/workspace/custom-skills` đã được index. Khi giao việc,
orchestrator tự gợi ý skill phù hợp cho từng subtask.

```python
from skills.loader import search_skills, suggest_skills
search_skills("pdf")              # tìm skill
suggest_skills("tóm tắt báo cáo") # gợi ý cho task
```

## Token tracking (mới)

Mọi API call được ghi lại: model, input/output tokens, thời gian.
Ước lượng token TRƯỚC khi chạy, cảnh báo khi vượt ngưỡng
(env `TOKEN_WARN_THRESHOLD`, mặc định 100.000).

Task đơn giản (summarize/classify/extract/translate/quick_qa)
tự động dùng model rẻ (Gemini flash free) để tiết kiệm.
