# HOOD runtime image.
#
# Security model (unchanged in the container): the app binds 127.0.0.1 only and
# first-run owner setup is refused from any non-loopback client. Expose it only
# through the reverse proxy in docker-compose.yml, which shares this container's
# network namespace so it reaches HOOD over loopback and preserves that model.
#
# Agent missions need user+network namespaces (`unshare -rn`). Docker's default
# seccomp/userns policy may block that; if so the agent sandbox fails closed
# (missions end UNVERIFIED) and the rest of HOOD still runs. To enable agent
# execution, run with a profile that permits unprivileged user namespaces.
FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
    HOOD_DATA_DIR=/data

# util-linux provides `unshare` for the sandbox; curl is for the healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends util-linux curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first for layer caching.
COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rX /opt/pw-browsers

# Application code.
COPY . .

# Run as an unprivileged account; it owns only the data dir.
RUN useradd --create-home --uid 10001 hood \
    && mkdir -p /data \
    && chown -R hood:hood /data /app
USER hood

EXPOSE 8990

# Liveness: the console must serve and the API must answer over loopback.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS -H "Host: 127.0.0.1:8990" http://127.0.0.1:8990/api/auth/status || exit 1

CMD ["python", "hood_cli.py", "ui", "--port", "8990"]
