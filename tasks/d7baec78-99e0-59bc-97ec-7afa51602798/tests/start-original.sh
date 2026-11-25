#!/bin/sh
# Start the unmodified original httpbin (Flask + gunicorn) on 127.0.0.1:8080.
set -e
cd /opt/original
exec /opt/orig-venv/bin/gunicorn \
    --worker-class gthread \
    --workers 1 \
    --threads 4 \
    --bind "127.0.0.1:${PORT:-8080}" \
    httpbin:app
