"""Client test cho lab-tracker server — kiểm chứng Bước 5 (auth) và Bước 6 (versioning).

    python client.py           # stdio: đọc server://info, chọn tool mới, fallback tool cũ
    python client.py --http    # HTTP: thử token đúng / thiếu / sai
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

SERVER_URL = "http://localhost:8090/mcp"
GOOD_TOKEN = "dev-token-abc123"


# ── Bước 6: client dò version rồi chọn tool ──────────────────────────
async def check_versioning() -> None:
    params = StdioServerParameters(command=sys.executable, args=["server.py"])

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            info = json.loads((await session.read_resource("server://info")).contents[0].text)
            print(f"Server: {info['name']} v{info['version']}")

            available = {t.name for t in (await session.list_tools()).tools}
            print(f"Tools: {sorted(available)}\n")

            # Dùng tool mới nếu server có, không thì fallback về tool cũ
            if "check_labs_v2" in available and not info["tools"]["check_labs_v2"]["deprecated"]:
                print("-> dùng check_labs_v2 (JSON)")
                res = await session.call_tool("check_labs_v2", {"dirty_only": True})
                data = json.loads(res.content[0].text)
                print(f"   {data['total']} repo cần xử lý (api {data['api_version']})")
                for lab in data["labs"][:5]:
                    print(f"   - {lab['name']}: {lab['dirty_count']} bẩn, {lab['unpushed']} chưa push")
            else:
                print("-> fallback check_labs (chuỗi)")
                res = await session.call_tool("check_labs", {"dirty_only": True})
                print(res.content[0].text)

            # Client cũ vẫn chạy được trên server v2
            old = await session.call_tool("check_labs", {"dirty_only": True})
            print(f"\n[client cũ vẫn hoạt động] {len(old.content[0].text.splitlines())} dòng")


# ── Bước 5: token đúng / thiếu / sai ─────────────────────────────────
_INIT_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "auth-test", "version": "1.0"},
    },
}


async def _try_token(label: str, token: str | None) -> None:
    """Kiểm tra một token: xem status HTTP thô, rồi mới mở session MCP nếu được phép.

    Phải xem ở tầng HTTP vì SDK gói lỗi 401 vào MCPError chung, mất status code.
    """
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    async with httpx.AsyncClient(headers=headers, timeout=10) as http:
        resp = await http.post(
            SERVER_URL,
            json=_INIT_BODY,
            headers={"Accept": "application/json, text/event-stream"},
        )
        if resp.status_code in (401, 403):
            print(f"{label}: TỪ CHỐI — HTTP {resp.status_code}")
            return

        # Token hợp lệ -> mở session MCP thật và liệt kê tool
        async with streamable_http_client(SERVER_URL, http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                print(f"{label}: OK — thấy {len(tools.tools)} tool")


async def check_auth() -> None:
    print(f"Kiểm thử auth trên {SERVER_URL}\n")
    await _try_token("Token đúng ", GOOD_TOKEN)
    await _try_token("Không token", None)
    await _try_token("Token sai  ", "totally-wrong-token")


if __name__ == "__main__":
    asyncio.run(check_auth() if "--http" in sys.argv else check_versioning())
