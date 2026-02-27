FROM python:3.13-slim

WORKDIR /app

# 시스템 의존성
RUN apt-get update && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

# KIS MCP Server 소스 클론
RUN git clone https://github.com/migusdn/KIS_MCP_Server.git .

# Python 의존성 설치
RUN pip install --no-cache-dir httpx "mcp[cli]" xmltodict python-dotenv fastmcp

# server.py의 FastMCP를 fastmcp 3.x 호환으로 수정
RUN sed -i 's/from mcp.server.fastmcp.server import FastMCP/from fastmcp import FastMCP/' /app/server.py \
    && sed -i 's/FastMCP("KIS MCP Server", dependencies=\[.*\])/FastMCP("KIS MCP Server")/' /app/server.py

# httpcore/httpx DEBUG 로그 억제 entrypoint
COPY entrypoint.py /app/entrypoint.py

EXPOSE 3000

# entrypoint.py → 로그 억제 후 FastMCP 실행
CMD ["python", "entrypoint.py"]
