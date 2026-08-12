# Multi-stage build: uv resolves the locked dependencies, the runtime image
# stays slim and runs as a non-root user.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev
COPY . .
RUN uv sync --locked --no-dev

FROM python:3.12-slim-bookworm
WORKDIR /app
RUN useradd --create-home appuser
COPY --from=builder --chown=appuser:appuser /app /app
USER appuser
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "myntos.main:app", "--host", "0.0.0.0", "--port", "8000"]
