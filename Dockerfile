FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV UVICORN_PORT=8080
ENV UVICORN_HOST=0.0.0.0
CMD ["uvicorn", "mona.app:app"]
