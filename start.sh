#!/bin/bash
set -e
echo "=== Запускаем главного бота ==="
exec gunicorn -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT bot:app
