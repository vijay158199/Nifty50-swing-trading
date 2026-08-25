# Reliance ICT/SMC weekly swing dashboard - single container: FastAPI app +
# in-process APScheduler (live monitor + Friday weekly report).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install deps first so this layer is cached across code-only changes.
# requirements-turso.txt (the Turso/libSQL DB driver) is split out from the
# base requirements.txt because it needs a source build with a Rust
# toolchain on Windows, where local dev happens - it's included here too
# since this Linux image installs it from a prebuilt wheel, no Rust needed.
COPY backend/requirements.txt backend/requirements-turso.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements.txt -r backend/requirements-turso.txt

COPY backend ./backend
COPY frontend ./frontend

# Matches the local dev convention (`cd backend && python run.py`) so all the
# relative paths in app/config.py (DATA_DIR = backend/../data, etc.) resolve
# the same way in the container as they do on a dev machine.
WORKDIR /app/backend

EXPOSE 8080

# Shell form (not exec/JSON-array form) so $PORT actually gets expanded -
# Render injects its own PORT env var dynamically (default 10000, host
# picks it); a plain `docker run` locally gets 8080.
CMD python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
