# Token Usage Dashboard

Trang thống kê token usage của FULL agents trong Orchestra. Chỉ đọc dữ liệu, không đặt giới hạn.

## Xem ở đâu

- Local: http://localhost:8099/tokens (đăng nhập user `tom` như bình thường)
- Trên điện thoại: mở qua link Railway đã deploy (sau khi deploy bản này)
- Nav: nút **🎟️ Token Usage** trên thanh điều hướng

## Có gì trên trang

1. **Bộ lọc**: khoảng ngày (7 / 14 / 30 ngày) + theo AI (Tất cả / từng con)
2. **Tổng quan**: thẻ Hôm nay / 7 ngày qua / 30 ngày qua (tổng tokens, in/out, số calls)
3. **Chi phí ước tính** (USD, theo bảng giá public trong `tokens/tracker.py`)
4. **Theo từng AI** (màu riêng): 🔵 Gemini `#4285F4` · 🟣 Muse `#A855F7` · 🟢 LM Studio `#22C55E` · 🔷 DeepSeek `#0EA5E9` · 🟠 Claude `#F97316`
5. **Biểu đồ cột chồng SVG** (thuần, không CDN): token mỗi ngày, xếp chồng theo AI
6. **Bảng chi tiết ngày × AI**: số calls, input, output, tổng tokens

## Nguồn dữ liệu

- SQLite `data/orchestra.db`, bảng `token_usage`:
  `id, ts, task_id, subtask_id, agent, model, input_tokens, output_tokens, duration_ms, estimated`
- Ghi bởi `tokens/tracker.py` (`TokenTracker.log`) — các agent gọi khi chạy API
- `estimated=1` nghĩa là số token ước lượng (API không trả token count), `estimated=0` là số thật
- Trang này CHỈ SELECT, không INSERT/UPDATE/DELETE gì

## Code

- Route: `GET /tokens` → `H._tokens_page(user, q)` trong `web/app.py` (query `?days=7|14|30&agent=all|gemini|muse|lmstudio|deepseek|claude`, giá trị lạ tự về mặc định)
- Không sửa logic chat/agent/auth. Chưa commit/push — deploy Railway khi anh duyệt.
