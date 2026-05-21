FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Streamlit writes ~/.streamlit/credentials.toml on first launch.
    # Without a writable HOME the dashboard crashes. /app is owned by
    # our non-root user below.
    HOME=/app \
    # Suppress Streamlit's "share your email" prompt + telemetry, which
    # also tries to write into HOME on first launch.
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

# system deps a couple of wheels need at runtime, then clean up
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# install python deps FIRST for better build caching
COPY requirements.txt .
RUN pip install -r requirements.txt

# copy the rest of the project
COPY . .

# Non-root runtime user. UID/GID match the host `lab` user (1000:1000
# on a fresh Ubuntu VPS), so the bind-mounted ./data on the host is
# writable WITHOUT needing `chmod 777` on the host. If your host user
# has a different uid, pass `--build-arg APP_UID=...` / APP_GID=... .
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd -g ${APP_GID} lab \
    && useradd -u ${APP_UID} -g ${APP_GID} -r -s /usr/sbin/nologin -d /app lab \
    && mkdir -p /app/data \
    && chown -R lab:lab /app
USER lab

# Healthcheck targets the Streamlit dashboard; the in-process worker
# threads are supervised by the dashboard itself (see core/worker_runner.py).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8501/_stcore/health || exit 1

# Default command is overridden in docker-compose.yml.
CMD ["streamlit", "run", "dashboard.py", \
     "--server.port", "8501", \
     "--server.address", "0.0.0.0", \
     "--server.headless", "true"]
