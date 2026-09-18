FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY app/ ./app/
COPY BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json .
COPY verify_solution.py .

# Expose port (Render overrides with dynamic $PORT)
EXPOSE ${PORT}

# Run FastAPI service with single worker to operate under 512MB RAM limit
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
