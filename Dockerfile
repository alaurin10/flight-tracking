# flighttrack on an always-on box (mini PC, NAS, Raspberry Pi 4+).
#
#   docker compose up -d          # builds, starts `flighttrack serve`
#   docker compose logs -f
#   open http://<box>:8080/
#
# One process: the daily job on a schedule, plus the report server.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install '.[full]'

# config.yaml, data/ and out/ are mounted by compose so they survive rebuilds.
VOLUME ["/app/data", "/app/out"]
EXPOSE 8080

HEALTHCHECK --interval=5m --timeout=10s --start-period=30s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/status.json', timeout=8).status==200 else 1)"

CMD ["flighttrack", "serve", "--host", "0.0.0.0", "--port", "8080", "--at", "03:15"]
