# 1) сборка фронтенда
FROM node:22.11.0-alpine3.20 AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
# Необязательный секрет ca_bundle — только для сборки за корпоративным TLS-прокси; без него ничего не меняется.
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -f /run/secrets/ca_bundle ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/ca_bundle; fi; \
    npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# 2) зависимости Python в отдельном виртуальном окружении
FROM python:3.12.7-slim-bookworm AS pydeps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY backend/requirements.txt /tmp/requirements.txt
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -f /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; \
    python -m venv /opt/venv && /opt/venv/bin/pip install -r /tmp/requirements.txt

# 3) лёгкий рантайм без компиляторов и dev-инструментов, процесс не от root
FROM python:3.12.7-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    DATA_DIR=/app/data \
    STATIC_DIR=/app/static \
    APP_PORT=8080
RUN groupadd --system app && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=pydeps /opt/venv /opt/venv
COPY backend/app ./app
COPY data ./data
COPY --from=frontend /fe/dist ./static
RUN chown -R app:app /app
USER app
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=4s --start-period=60s --retries=6 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
