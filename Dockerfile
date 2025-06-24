FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock .
RUN uv sync --no-dev --frozen --no-cache
COPY . .
EXPOSE 8080
CMD ["/app/.venv/bin/uvicorn", "mona.app:app", "--host", "0.0.0.0", "--port", "8080"]
