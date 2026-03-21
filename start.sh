#!/bin/bash
# Устанавливаем Stockfish на Render
apt-get update && apt-get install -y stockfish
# Запускаем бота с HTTP-сервером для health check
gunicorn -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT bot:app
