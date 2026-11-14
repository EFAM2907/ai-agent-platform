# syntax=docker/dockerfile:1

# ---- Builder stage -------------------------------------------------
# Pinned patch + Debian codename: reproducible builds (never `latest`).
# Debian-based slim, not alpine: asyncpg, cryptography and argon2 ship
# prebuilt wheels for glibc (manylinux), not for musl.
FROM python:3.14.10-slim-trixie AS builder

# A venv in a fixed path lets the final stage copy the whole
# dependency tree with a single COPY.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# requirements.txt is copied BEFORE the source so this layer stays
# cached until the dependencies actually change.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- Final stage ---------------------------------------------------
FROM python:3.14.10-slim-trixie

# No .pyc files in the image; unbuffered stdout/stderr so logs show up
# in `docker logs` immediately.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# Unprivileged user: a compromised process must not be root.
RUN useradd --system --create-home --uid 10001 --shell /usr/sbin/nologin appuser

# Only the installed dependencies; pip, caches and build leftovers stay
# in the builder stage.
COPY --from=builder /opt/venv /opt/venv

# The prompt loader reads app/llm/prompts/templates/*.yaml through a
# path relative to the working directory, so the layout under /app must
# match the repo.
WORKDIR /app

# Explicit copies (no `COPY . .`): only what the API needs at runtime.
# --chown makes the files readable by appuser.
COPY --chown=appuser:appuser app/ ./app/
COPY --chown=appuser:appuser migrations/ ./migrations/
COPY --chown=appuser:appuser alembic.ini ./alembic.ini
COPY --chown=appuser:appuser entrypoint.sh ./entrypoint.sh

USER appuser

EXPOSE 8000

# Exec form, invoked through `sh` so it does not depend on the executable
# bit (not preserved on Windows checkouts). entrypoint.sh validates the
# configuration, runs `alembic upgrade head` (advisory-locked, safe with
# several replicas) and then execs uvicorn as PID 1. No --reload.
CMD ["sh", "./entrypoint.sh"]
