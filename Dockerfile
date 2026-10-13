# N-ATLAS Forge production image: one container serves the API and the web app on port 8000.
# Built from the repository root by the deployment platform. See docs/deployment.md.
#
# Runtime configuration (database, Redis, secrets, public URL, inference worker) arrives as
# environment variables. Nothing secret is baked into the image.

# ---- Stage 1: build the web app ------------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: runtime ----------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    APP_ENV=production \
    LOG_FORMAT=json \
    ENABLE_API_DOCS=false \
    FRONTEND_DIST_DIR=/app/frontend-dist

WORKDIR /app
COPY backend/ /app/backend/
RUN pip install /app/backend

COPY --from=web /web/dist /app/frontend-dist
COPY docker/entrypoint.sh /app/docker/entrypoint.sh
RUN sed -i 's/\r$//' /app/docker/entrypoint.sh \
    && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin forge

USER forge
WORKDIR /app/backend
EXPOSE 8000

# The liveness probe does not touch PostgreSQL, Redis or the model worker, so the container is
# reported healthy as soon as the process serves requests.
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/v1/health/live' % os.environ.get('PORT', '8000'), timeout=4)"

CMD ["sh", "/app/docker/entrypoint.sh"]
