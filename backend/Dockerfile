FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Install theHarvester CLI in a dedicated venv — python:3.11-slim is now on
# Debian trixie which dropped the `theharvester` apt package, and the PyPI
# `theHarvester` name is a squatter (real releases live only on the laramies
# GitHub repo). Upstream 4.8+ tags require Python 3.12; 4.7.1 is the last
# 3.11-compatible tag. The dedicated venv sandboxes the transitive deps so
# they never collide with the backend's FastAPI/SQLAlchemy pins, and the
# binary is symlinked onto /usr/local/bin. `git` is a build-time dep only —
# purged after the install to keep the image small.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && python3 -m venv /opt/theharvester \
    && /opt/theharvester/bin/pip install --no-cache-dir --upgrade pip setuptools wheel \
    && /opt/theharvester/bin/pip install --no-cache-dir "git+https://github.com/laramies/theHarvester.git@4.7.1" \
    && ln -s /opt/theharvester/bin/theHarvester /usr/local/bin/theHarvester \
    && apt-get purge -y --auto-remove git \
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
