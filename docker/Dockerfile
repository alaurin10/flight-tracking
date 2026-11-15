# Flight Watch: one image for every service. docker-compose.yml runs the worker
# (`flighttrack worker`, which collects) and the page server (`flighttrack serve
# --no-schedule`); the API joins them in M5.
# Built on the server by `docker compose up -d --build`; CI only checks that it builds.
# The base image is pinned by digest (Dependabot proposes updates).

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.11.32

WORKDIR /app/backend
# Dependencies first, so code changes don't reinstall them. The image includes the
# fast-flights fallback parser.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --extra fallback --no-install-project
COPY backend/flighttrack ./flighttrack
RUN uv sync --frozen --no-dev --extra fallback

# Tunables only; what is tracked lives in the database under /data.
COPY config /app/config

# Runs as uid 1000, the usual first user on a Linux host, so ./data stays yours.
RUN useradd --create-home --uid 1000 flight && mkdir -p /data && chown flight /data
USER flight

# The commit this image was built from.
ARG GIT_COMMIT=""
ENV GIT_COMMIT=$GIT_COMMIT \
    PATH=/app/backend/.venv/bin:$PATH \
    DATA_DIR=/data \
    CONFIG_DIR=/app/config
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/status.json', timeout=8)"]
# The page server. The worker service overrides the command and the health check.
CMD ["flighttrack", "serve", "--no-schedule", "--host", "0.0.0.0", "--port", "8080"]
