FROM python:3.12-slim

LABEL maintainer="dead-drop-teams"
LABEL description="Dead Drop MCP server for multi-agent coordination"

WORKDIR /app

# Install dependencies
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# Default database path inside container
ENV DEAD_DROP_DB_PATH=/data/messages.db
ENV DEAD_DROP_PORT=9400
ENV DEAD_DROP_HOST=0.0.0.0
ENV DEAD_DROP_ROOM_TOKEN=""
ENV DEAD_DROP_TEAM=""

# Runtime directory for protocol docs and role profiles
COPY docs/PROTOCOL.md /data/PROTOCOL.md
COPY docs/roles/ /data/roles/

# Data volume for persistent SQLite DB
VOLUME /data

EXPOSE 9400

# Health check — POST a valid MCP ping to the endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,json; r=urllib.request.Request('http://localhost:9400/mcp',data=json.dumps({'jsonrpc':'2.0','id':0,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'healthcheck','version':'1.0'}}}).encode(),headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream'}); urllib.request.urlopen(r)" || exit 1

CMD ["python", "-m", "dead_drop.server", "--http", "--host", "0.0.0.0", "--port", "9400"]
