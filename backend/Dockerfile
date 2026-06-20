# Hermes Monitor backend image.
# Single-stage, slim base, no build tools -- keeps the image small for the NAS.
FROM python:3.12-slim

# Non-root user inside the container (the fnOS privilege config maps this).
RUN groupadd --system app && useradd --system --gid app --create-home app

WORKDIR /app

# Install curl (for HEALTHCHECK) and Python deps.
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and templates.
COPY app ./app
COPY templates ./templates

# Persistent data lives under /app/data (mounted as a fnOS share volume).
RUN mkdir -p /app/data && chown -R app:app /app
USER app

ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    DB_PATH=/app/data/monitor.db

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://127.0.0.1:8000/docs || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
