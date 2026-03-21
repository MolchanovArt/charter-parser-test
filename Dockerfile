FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    UV_CACHE_DIR=/tmp/uv-cache \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/charter-parser-venv

RUN apt-get update \
    && apt-get install -y --no-install-recommends make ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /opt/build

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --extra dev
RUN mkdir -p /tmp/uv-cache /tmp/.cache && chmod -R 777 /tmp/uv-cache /tmp/.cache /tmp

WORKDIR /workspace

CMD ["bash"]
