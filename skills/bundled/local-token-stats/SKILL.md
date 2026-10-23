---
name: local-token-stats
description: Đọc số liệu token usage thật của Tom AI Orchestra từ bảng token_usage (SQLite local). Dùng khi user hỏi "hôm nay xài bao nhiêu token", "AI nào tốn nhất", "token 7 ngày/30 ngày". Trả về tổng token, in/out, số calls, chi phí USD ước tính theo từng AI (Gemini, Muse, LM Studio, DeepSeek, Claude).
---

# Token Stats — đọc token usage của Orchestra

Skill này cho phép AI trong Tom AI Orchestra trả lời câu hỏi về token usage
bằng SỐ LIỆU THẬT từ database, không ước đoán.

## Dùng khi
User hỏi về token: "hôm nay xài bao nhiêu token", "token tuần này",
"AI nào tốn token nhất", "chi phí AI tháng này".

## Cách hoạt động
Orchestra tự inject dữ liệu vào prompt (xem `web/app.py::_data_context`).
Module đọc: `data_providers/token_stats.py`.

## API (Python, chạy trên máy Orchestra)
```python
from data_providers import token_stats
token_stats.get_today()          # text ngắn: token hôm nay
token_stats.format_stats(7)      # text: tổng hợp 7 ngày
token_stats.format_stats(30)     # text: tổng hợp 30 ngày
token_stats.get_stats(30)        # dict chi tiết theo AI
```

## Nguồn dữ liệu
- Bảng `token_usage` trong `data/orchestra.db` (ghi bởi `tokens/tracker.py`
  mỗi khi agent gọi API).
- Giá USD/1M tokens định nghĩa trong `data_providers/token_stats.py::PRICING`
  (đồng bộ với `tokens/tracker.py::MODEL_PRICING`).

## Lưu ý
- Chỉ đọc, không ghi.
- Số liệu ước tính chi phí cho model free = $0.
