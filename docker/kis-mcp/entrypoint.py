"""KIS MCP Server entrypoint — httpcore/httpx DEBUG 로그 억제 후 FastMCP 실행."""
import logging
import os
import sys

# KIS_ACCOUNT_TYPE에 따라 실/모의 키 선택
if os.environ.get("KIS_ACCOUNT_TYPE", "VIRTUAL").upper() == "VIRTUAL":
    for real, paper in [("KIS_APP_KEY", "KIS_PAPER_APP_KEY"),
                        ("KIS_APP_SECRET", "KIS_PAPER_APP_SECRET"),
                        ("KIS_CANO", "KIS_PAPER_CANO")]:
        val = os.environ.get(paper)
        if val:
            os.environ[real] = val

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
