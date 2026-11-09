FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POLARION_MCP_TRANSPORT=stdio

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

# stdio is the default; for HTTP mode run with:
#   docker run -p 8000:8000 -e POLARION_MCP_TRANSPORT=streamable-http \
#     -e POLARION_MCP_HOST=0.0.0.0 ... polarion-mcp-2023
ENTRYPOINT ["python", "-m", "polarion_mcp_2023"]
