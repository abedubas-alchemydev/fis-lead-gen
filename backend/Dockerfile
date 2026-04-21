FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Install theHarvester CLI (Debian bookworm main; provides the `theHarvester`
# binary on PATH for the email_extractor theHarvester provider). Upstream's
# 4.8+ tags require Python 3.12 and 4.7.x conflicts with FastAPI/SQLAlchemy
# pins, so installing via apt is the least-painful path — runs as a subprocess
# in its own system Python env, isolated from the backend's pip dependencies.
RUN apt-get update \
    && apt-get install -y --no-install-recommends theharvester \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Run migrations then start the server
# In production, migrations can be run as a separate init step
EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2"]
