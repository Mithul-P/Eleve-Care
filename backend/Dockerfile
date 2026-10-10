# Build context: repository root (the backend imports the sibling /ml package).
#   docker build -f backend/Dockerfile -t elevcare-backend .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/ml \
    ML_ARTIFACT_PATH=/app/ml/artifacts/models.joblib

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY ml/ ml/
# Train the models at build time so containers start in seconds (~1 min, deterministic seed).
RUN python -m elevcare_ml.train --out /app/ml/artifacts

COPY backend/ backend/
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
WORKDIR /app/backend

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"
# PORT is honoured for PaaS hosts (Render/Railway/Fly set it); defaults to 8000.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
