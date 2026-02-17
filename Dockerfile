FROM python:3.12-slim

LABEL maintainer="dead-drop-teams"
LABEL description="Dead Drop MCP server for multi-agent coordination"

WORKDIR /app

# Install dependencies
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e .

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

# Health check — hit the MCP endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${DEAD_DROP_PORT}/mcp')" || exit 1

CMD ["python", "-m", "dead_drop.server", "--http", "--host", "0.0.0.0", "--port", "9400"]
