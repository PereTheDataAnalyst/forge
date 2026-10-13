#!/bin/sh
# Container start for N-ATLAS Forge: apply database migrations, then serve the API and web app.
# Migrations are safe to repeat. Set FORGE_SKIP_MIGRATIONS=true to skip them, for example when a
# release step runs them separately.
set -eu

cd /app/backend

if [ "${FORGE_SKIP_MIGRATIONS:-false}" != "true" ]; then
  echo "[entrypoint] applying database migrations"
  alembic upgrade head
fi

echo "[entrypoint] starting the server on port ${PORT:-8000}"
# Client addresses are resolved by the application from X-Forwarded-For (TRUSTED_PROXY_COUNT),
# so the server's own proxy handling is off. The application writes its own access log.
exec uvicorn app.main:create_app --factory \
  --host 0.0.0.0 --port "${PORT:-8000}" \
  --no-proxy-headers --no-server-header --no-access-log
