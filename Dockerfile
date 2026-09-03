# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.12.5-python3.12-trixie-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Keep dependency installation in a cacheable layer.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-install-project --no-editable

COPY src ./src
COPY services ./services
COPY benchmarks/demo_items/faults ./benchmarks/demo_items/faults
RUN uv sync --locked --no-editable \
    && useradd --create-home --uid 10001 oate \
    && chown -R oate:oate /app

USER oate

CMD ["uvicorn", "services.demo_items.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
