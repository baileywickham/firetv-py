FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (cached when source changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Then install the project
COPY src ./src
COPY README.md ./README.md
RUN uv sync --frozen --no-dev

# Persistent state directory (mount a PVC here)
RUN mkdir -p /data
ENV FIRETV_STATE_DIR=/data

EXPOSE 51828

ENTRYPOINT ["/app/.venv/bin/firetv-homekit"]
