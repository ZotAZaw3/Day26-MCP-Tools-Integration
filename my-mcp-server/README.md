# Lab Tracker MCP Server

MCP server theo dõi trạng thái git của toàn bộ repo lab trong `AI20K/Lab/`.
Toàn bộ dữ liệu đọc thật từ filesystem + `git`, không hard-code.

## Bước 1 — Use case

**Công việc hiện tại:** trước mỗi hạn nộp bài, kiểm tra xem trong hơn 20 folder
lab có repo nào còn file chưa commit hoặc còn commit chưa push lên GitHub.

**Tôi đang làm thủ công như thế nào:** mở lần lượt từng folder trong VS Code
hoặc `cd` vào từng thư mục rồi gõ `git status` và `git log origin/main..HEAD`.
Hơn 20 folder nên mất khoảng 10 phút, và rất dễ bỏ sót một repo.

**Input:** đường dẫn thư mục chứa các lab (`LAB_ROOT`), tuỳ chọn lọc "chỉ hiện
repo có vấn đề", từ khoá khi cần tìm một lab cụ thể.

**Output:** danh sách repo kèm branch, số file chưa commit, số commit chưa push,
và commit gần nhất.

## Bước 2 — Tools

| Tool | Input | Output |
|------|-------|--------|
| `check_labs` | `dirty_only: bool = False` | `str` — mỗi repo một dòng: `tên [branch]: 2 file chưa commit, 1 commit chưa push` |
| `check_labs_v2` | `dirty_only: bool = False`, `include_files: bool = False`, `limit: int = 50` | `str` (JSON) — `{api_version, lab_root, scanned_at, total, labs[]}`; mỗi lab có `name, path, branch, dirty_count, unpushed, behind, has_upstream, last_commit{sha,date,message}` |
| `find_lab` | `keyword: str` | `str` (JSON) — `{keyword, count, results[{name, path, title}]}`, khớp theo tên folder hoặc tiêu đề `README.md` |

Tác vụ thật được thực hiện: `git status --porcelain --branch` và
`git log -1` trên từng repo, cộng với việc đọc tiêu đề `README.md`.

## Bước 3 — Chạy server

```bash
pip install -r requirements.txt

python server.py                 # stdio (mặc định)
```

Biến môi trường:

| Biến | Mặc định | Ý nghĩa |
|------|----------|---------|
| `LAB_ROOT` | thư mục cha của repo Day26 | Nơi chứa các folder lab |
| `MCP_TRANSPORT` | `stdio` | Đặt `http` để chạy streamable-http |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8090` | Địa chỉ khi chạy HTTP |
| `MCP_AUTH_TOKEN` | `dev-token-abc123` | Bearer token hợp lệ (chỉ dùng khi HTTP) |

## Bước 4 — Đăng ký với Claude Code

```bash
claude mcp add lab-tracker -- python C:/Users/Long/Desktop/Việc/AI20K/Lab/Day26-MCP-Tools-Integration/my-mcp-server/server.py
```

Kiểm tra bằng `claude mcp list`, sau đó hỏi bằng ngôn ngữ tự nhiên:

```
Lab nào của tôi còn chưa commit hoặc chưa push?
Tìm giúp tôi các lab liên quan tới RAG.
```

Luồng: `User → Claude Code → chọn tool → MCP Client → MCP Server → chạy git thật → kết quả`.

## Bước 5 — Authentication

Chuyển transport sang `streamable-http` kèm `TokenVerifier`:

```bash
MCP_TRANSPORT=http MCP_AUTH_TOKEN=dev-token-abc123 python server.py
# server lắng nghe tại http://localhost:8090/mcp

python client.py --http     # terminal khác
```

`StaticTokenVerifier.verify_token()` trả `None` khi token không nằm trong
`VALID_TOKENS`, MCP SDK sẽ tự trả `401`.

Kết quả kiểm thử:

```
Token đúng : OK — thấy 3 tool
Không token: TỪ CHỐI — 401
Token sai  : TỪ CHỐI — 401
```

Server bind `0.0.0.0` nên máy khác trong cùng LAN gọi được qua
`http://<ip-máy-chủ>:8090/mcp` với cùng bearer token.

## Bước 6 — Versioning

`check_labs` (v1) trả chuỗi, `check_labs_v2` trả JSON chi tiết hơn.

```jsonc
// v1 — check_labs
"K4-Day08-RAG-Pipeline-FIFO [main]: 2 file chưa commit"

// v2 — check_labs_v2
{
  "api_version": "2.0",
  "name": "K4-Day08-RAG-Pipeline-FIFO",
  "path": "C:/.../K4-Day08-RAG-Pipeline-FIFO",
  "branch": "main",
  "dirty_count": 2,
  "unpushed": 0,
  "behind": 0,
  "has_upstream": true,
  "last_commit": {"sha": "a1b2c3d", "date": "2026-08-27T09:12:00+07:00", "message": "..."}
}
```

Nguyên tắc giữ tương thích ngược:

- v1 không bị xoá và không đổi format — client cũ vẫn chạy.
- Tham số mới của v2 (`include_files`, `limit`) đều optional có default.
- Tên tham số `dirty_only` giữ nguyên giữa hai version.

Resource `server://info` cho client tự dò:

```json
{
  "name": "lab-tracker",
  "version": "2.0.0",
  "tools": {
    "check_labs":    {"version": "1.0.0", "deprecated": true},
    "check_labs_v2": {"version": "2.0.0", "deprecated": false},
    "find_lab":      {"version": "1.0.0", "deprecated": false}
  }
}
```

`client.py` đọc resource này, dùng `check_labs_v2` nếu có và fallback về
`check_labs` nếu không:

```bash
python client.py
```

## Files

```
my-mcp-server/
├── server.py          # 3 tools + resource + TokenVerifier
├── client.py          # test versioning (stdio) và auth (--http)
├── requirements.txt
└── README.md
```
