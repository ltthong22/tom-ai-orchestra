---
name: local-drive-search
description: Tìm kiếm file/folder trên Google Drive của TomStudio qua chỉ mục metadata local (tên, đường dẫn, dung lượng, ngày sửa). Dùng khi user hỏi "tìm ảnh cưới Tài Nhạn", "folder Pizzaioli ở đâu", "file báo giá ở đâu". Chỉ đọc metadata, không tải file về trừ khi user yêu cầu file cụ thể.
---

# Drive Search — tìm file/folder trên Drive TomStudio

Skill này cho phép AI trong Tom AI Orchestra tìm file/folder trên toàn bộ
Google Drive của anh Thống (Khach, Khach 2, Tom Studio Projects,
Studio Manager...) bằng chỉ mục metadata local — nhanh, không cần quét
Drive mỗi lần hỏi.

## Dùng khi
User hỏi tìm đồ trên Drive: "tìm ảnh cưới Tài Nhạn", "folder Pizzaioli ở đâu",
"file hợp đồng A Tai", "có bao nhiêu ảnh trong folder X".

## Cách hoạt động
1. **Index (1 lần, chạy nền):** `data_providers/drive_index.py::build_index()`
   crawl 4 folder gốc, lưu id/tên/đường dẫn/dung lượng/ngày sửa vào
   SQLite `data/orchestra.db` (bảng `drive_index` + FTS5).
2. **Tìm:** `search("tài nhạn cưới")` → list file/folder khớp.
3. Orchestra tự inject kết quả vào prompt (`web/app.py::_data_context`).

## API (Python)
```python
from data_providers import drive_index
drive_index.build_index()              # xây lại chỉ mục (chạy nền, vài phút)
drive_index.index_status()             # {"count": N, "last_build": {...}}
drive_index.search("pizzaioli", 10)    # list dict: id, name, path, size...
drive_index.format_search("cưới tài nhạn")  # text gọn để đưa vào prompt
```

## Đọc file cụ thể (on-demand)
Khi đã có file id từ kết quả tìm kiếm và user cần đọc nội dung:
```sh
/opt/hatch/bin/hatch_gws_cli drive files get \
  --params '{"fileId":"<id>","alt":"media"}' --output /tmp/<ten-file>
```
Google Docs/Sheets/Slides → dùng connector google_docs/google_sheets
tương ứng, không tải binary qua Drive.

## Nguyên tắc
- Chỉ đọc metadata khi index; không tải toàn bộ file về (hàng TB).
- Không đổi tên/di chuyển/xóa file trên Drive.
- Nên rebuild index định kỳ (cron) hoặc khi user báo có file mới.
