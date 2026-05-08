FROM python:3.11-slim-bookworm

RUN apt-get update -o Acquire::Retries=5 \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080

CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT}
