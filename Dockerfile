# graphify MCP server — single-graph and multi-graph targets.
#
# Single-graph (existing, default):
#   docker build -t graphify .
#   docker run -p 8080:8080 -v "$(pwd)/graphify-out:/data" graphify \
#       /data/graph.json --transport http --host 0.0.0.0 --api-key "$SECRET"
#
# Multi-graph:
#   docker build --target multi -t graphify-multi .
#   docker run -p 8080:8080 -v "$(pwd)/my-graphs:/graphs:ro" graphify-multi

FROM python:3.12-slim AS base
WORKDIR /app
COPY . /app

# The [mcp] extra pulls mcp + starlette + uvicorn, which the HTTP transport needs.
RUN pip install --no-cache-dir ".[mcp]"

# Run as a non-root user — the server is network-exposed.
RUN useradd --create-home --uid 10001 graphify
USER graphify

# --- Single-graph target (default, backward-compatible) ---
FROM base AS single
EXPOSE 8080
ENTRYPOINT ["python", "-m", "graphify.serve"]
CMD ["/data/graph.json", "--transport", "http", "--host", "0.0.0.0", "--port", "8080"]

# --- Multi-graph target ---
FROM base AS multi
EXPOSE 8080
VOLUME /graphs
ENV GRAPHS_DIR=/graphs
ENV SCAN_INTERVAL=30
ENV PORT=8080
ENTRYPOINT ["graphify-mcp", "--transport", "http", "--host", "0.0.0.0"]
