"""KIS MCP Server entrypoint — httpcore/httpx DEBUG 로그 억제 후 FastMCP 실행."""
import logging
import sys

# 노이즈 라이브러리 로그 레벨을 WARNING으로 설정
for name in ("httpcore", "httpx", "hpack", "h11", "httpcore.http11", "httpcore.connection"):
    logging.getLogger(name).setLevel(logging.WARNING)

if __name__ == "__main__":
    from fastmcp.cli import app as typer_app

    sys.argv = [
        "fastmcp", "run", "server.py:mcp",
        "--transport", "sse",
        "--host", "0.0.0.0",
        "--port", "3000",
    ]
    typer_app()
