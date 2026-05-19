FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# system deps that some wheels need at runtime, then clean up
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# install python deps first for better build caching
COPY requirements.txt .
RUN pip install -r requirements.txt

# copy the whole project (all bots share one image)
COPY . .

# non-root runtime user; /app/data is the only writable mount
RUN useradd -u 10001 -r -s /usr/sbin/nologin lab \
    && mkdir -p /app/data \
    && chown -R lab:lab /app
USER lab

# default health: a basic streamlit endpoint check works for the
# dashboard service; workers override HEALTHCHECK in compose if needed
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8501/_stcore/health || exit 1

# default command is overridden per-service in docker-compose.yml
CMD ["python", "-m", "bots.worker"]
