FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
WORKDIR /app
COPY requirements.txt .
RUN uv pip install --system --no-cache-dir -r requirements.txt
COPY . .
ENV UVICORN_PORT=8080
ENV UVICORN_HOST=0.0.0.0
CMD ["uvicorn", "mona.app:app"]
