"""Lab Tracker MCP Server — theo dõi trạng thái các repo lab bằng DỮ LIỆU THẬT.

Use case: mỗi ngày trước khi nộp bài phải mở từng folder lab trong AI20K/Lab
để xem repo nào chưa commit, repo nào quên push. Có hơn 20 folder nên làm tay
rất mất thời gian. Server này chạy `git` thật trên từng repo và trả kết quả.

Tools:
    check_labs(dirty_only)          [v1] chuỗi tóm tắt, giữ cho client cũ
    check_labs_v2(...)              [v2] JSON chi tiết + filter
    find_lab(keyword)               tìm folder lab theo tên / tiêu đề README

Resource:
    server://info                   metadata để client kiểm tra version

Cách chạy:
    pip install -r requirements.txt
    python server.py                                  # stdio (mặc định)
    MCP_TRANSPORT=http MCP_AUTH_TOKEN=... python server.py   # streamable-http + auth
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer

SERVER_VERSION = "2.0.0"

# Thư mục chứa các lab: mặc định là folder cha của repo Day26
LAB_ROOT = Path(os.environ.get("LAB_ROOT", Path(__file__).resolve().parents[2]))

_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio")
_HOST = os.environ.get("MCP_HOST", "0.0.0.0")
_PORT = int(os.environ.get("MCP_PORT", "8090"))


# ── Bước 5: Authentication ───────────────────────────────────────────
VALID_TOKENS: dict[str, str] = {
    os.environ.get("MCP_AUTH_TOKEN", "dev-token-abc123"): "dev-user",
}


class StaticTokenVerifier(TokenVerifier):
    """Xác minh bearer token theo danh sách tĩnh.

    Token sai/thiếu -> trả None -> MCP SDK tự trả 401.
    Production nên thay bằng JWT decode hoặc OAuth introspection.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        client_id = VALID_TOKENS.get(token)
        if client_id is None:
            return None
        return AccessToken(token=token, client_id=client_id, scopes=["labs:read"])


# Auth chỉ bật khi chạy HTTP — stdio đã được bảo vệ bởi chính tiến trình cha
_auth_kwargs: dict = {}
if _TRANSPORT != "stdio":
    base_url = f"http://localhost:{_PORT}"
    _auth_kwargs = {
        "auth": AuthSettings(issuer_url=base_url, resource_server_url=base_url),
        "token_verifier": StaticTokenVerifier(),
    }

mcp = MCPServer(
    "lab-tracker",
    instructions=f"Lab Tracker v{SERVER_VERSION} — theo dõi trạng thái git của "
    f"các repo lab trong {LAB_ROOT}.",
    **_auth_kwargs,
)


# ── Lớp đọc dữ liệu thật ─────────────────────────────────────────────
def _git(repo: Path, *args: str) -> str:
    """Chạy `git *args` trong *repo*, trả stdout đã strip (rỗng nếu lỗi)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _parse_branch_line(line: str) -> tuple[str, int, int]:
    """Tách dòng `## main...origin/main [ahead 2, behind 1]` -> (branch, ahead, behind)."""
    head = line[3:] if line.startswith("## ") else line
    ahead = behind = 0
    if " [" in head:
        head, _, tail = head.partition(" [")
        for part in tail.rstrip("]").split(", "):
            if part.startswith("ahead "):
                ahead = int(part[6:])
            elif part.startswith("behind "):
                behind = int(part[7:])
    branch = head.split("...")[0]
    has_upstream = "..." in head
    return branch, ahead, (behind if has_upstream else -1)


def _scan_repo(repo: Path) -> dict:
    """Đọc trạng thái git thật của một repo."""
    # 1 lệnh lấy được cả branch, ahead/behind và danh sách file bẩn
    status = _git(repo, "status", "--porcelain", "--branch")
    lines = status.splitlines() if status else []
    branch, ahead, behind = _parse_branch_line(lines[0]) if lines else ("?", 0, -1)
    dirty = [ln[3:] for ln in lines[1:]]

    last = _git(repo, "log", "-1", "--format=%h\x1f%cI\x1f%s")
    sha, date, subject = (last.split("\x1f") + ["", "", ""])[:3] if last else ("", "", "")

    return {
        "name": repo.name,
        "path": str(repo),
        "branch": branch,
        "dirty_count": len(dirty),
        "dirty_files": dirty[:20],
        "unpushed": ahead,
        "behind": max(behind, 0),
        "has_upstream": behind >= 0,
        "last_commit": {"sha": sha, "date": date, "message": subject},
    }


# ponytail: quét tuần tự, ~2 lệnh git mỗi repo. Với >100 repo thì đổi sang
# ThreadPoolExecutor; ở mức vài chục folder thì chạy dưới 2 giây.
def _scan_all() -> list[dict]:
    """Quét mọi folder con của LAB_ROOT có .git."""
    if not LAB_ROOT.is_dir():
        return []
    repos = sorted(p for p in LAB_ROOT.iterdir() if (p / ".git").exists())
    return [_scan_repo(p) for p in repos]


# ── Bước 2 + 6: Tools ────────────────────────────────────────────────
@mcp.tool()
def check_labs(dirty_only: bool = False) -> str:
    """[v1] Liệt kê trạng thái git của các repo lab, mỗi repo một dòng.

    Giữ nguyên format chuỗi cho client cũ. Client mới nên dùng check_labs_v2.

    Args:
        dirty_only: Chỉ hiện repo có thay đổi chưa commit hoặc chưa push.
    """
    rows = _scan_all()
    if dirty_only:
        rows = [r for r in rows if r["dirty_count"] or r["unpushed"]]
    if not rows:
        return "Không có repo nào cần xử lý." if dirty_only else f"Không tìm thấy repo trong {LAB_ROOT}"

    out = []
    for r in rows:
        flags = []
        if r["dirty_count"]:
            flags.append(f"{r['dirty_count']} file chưa commit")
        if r["unpushed"]:
            flags.append(f"{r['unpushed']} commit chưa push")
        if not r["has_upstream"]:
            flags.append("chưa có remote")
        out.append(f"{r['name']} [{r['branch']}]: {', '.join(flags) or 'sạch'}")
    return "\n".join(out)


@mcp.tool()
def check_labs_v2(
    dirty_only: bool = False,
    include_files: bool = False,
    limit: int = 50,
) -> str:
    """[v2] Trạng thái git của các repo lab dưới dạng JSON chi tiết.

    Args:
        dirty_only: Chỉ trả repo có thay đổi chưa commit hoặc chưa push.
        include_files: Kèm danh sách file đang bẩn (tối đa 20 file/repo).
        limit: Số repo tối đa trả về (mặc định 50).
    """
    rows = _scan_all()
    if dirty_only:
        rows = [r for r in rows if r["dirty_count"] or r["unpushed"]]
    if not include_files:
        rows = [{k: v for k, v in r.items() if k != "dirty_files"} for r in rows]

    return json.dumps(
        {
            "api_version": "2.0",
            "lab_root": str(LAB_ROOT),
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "total": len(rows),
            "labs": rows[:limit],
        },
        ensure_ascii=False,
    )


@mcp.tool()
def find_lab(keyword: str) -> str:
    """Tìm folder lab theo tên hoặc theo tiêu đề trong README.

    Args:
        keyword: Từ khoá cần tìm, không phân biệt hoa thường (vd: "rag", "day18").
    """
    if not LAB_ROOT.is_dir():
        return json.dumps({"error": f"Không thấy {LAB_ROOT}"}, ensure_ascii=False)

    kw = keyword.lower()
    hits = []
    for p in sorted(LAB_ROOT.iterdir()):
        if not p.is_dir() or p.name.startswith("."):
            continue

        title = ""
        for readme in (p / "README.md", p / "readme.md"):
            if readme.is_file():
                for line in readme.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("#"):
                        title = line.lstrip("# ").strip()
                        break
                break

        if kw in p.name.lower() or kw in title.lower():
            hits.append({"name": p.name, "path": str(p), "title": title})

    return json.dumps({"keyword": keyword, "count": len(hits), "results": hits}, ensure_ascii=False)


# ── Bước 6: Resource để client dò version ────────────────────────────
@mcp.resource("server://info")
def server_info() -> str:
    """Metadata của server — version và trạng thái từng tool."""
    return json.dumps(
        {
            "name": "lab-tracker",
            "version": SERVER_VERSION,
            "tools": {
                "check_labs": {"version": "1.0.0", "deprecated": True},
                "check_labs_v2": {"version": "2.0.0", "deprecated": False},
                "find_lab": {"version": "1.0.0", "deprecated": False},
            },
            "migration_guide": "check_labs trả chuỗi; check_labs_v2 trả JSON có "
            "thêm path, behind, last_commit. Tham số dirty_only giữ nguyên tên.",
        },
        ensure_ascii=False,
    )


if __name__ == "__main__":
    if _TRANSPORT == "stdio":
        mcp.run()
    else:
        mcp.run(transport="streamable-http", host=_HOST, port=_PORT)
