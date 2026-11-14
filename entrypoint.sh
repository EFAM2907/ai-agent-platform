#!/bin/sh
# Container entrypoint: validate config, migrate, then serve.
set -e

# 1. Validate configuration BEFORE touching the database: importing the
#    settings runs the Settings validators, so an invalid production
#    configuration (e.g. missing STS_PRIVATE_KEY) fails here.
echo "[entrypoint] validating configuration..."
python -c "from app.core.config import settings; print(\"[entrypoint] configuration OK (environment=%s)\" % settings.environment)"

# 2. Apply pending migrations. migrations/env.py takes a PostgreSQL advisory
#    lock, so replicas starting at the same time run them one at a time.
echo "[entrypoint] running database migrations..."
alembic upgrade head

# 3. exec replaces the shell: uvicorn becomes PID 1 and receives SIGTERM
#    directly, which allows a graceful shutdown.
echo "[entrypoint] starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
