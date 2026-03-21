#!/bin/bash
set -e

echo "Скачиваем Stockfish 17.1..."
wget -q https://github.com/official-stockfish/Stockfish/releases/download/sf_17.1/stockfish-ubuntu-x86-64.tar
tar -xf stockfish-ubuntu-x86-64.tar
mv stockfish/stockfish-ubuntu-x86-64 ./stockfish
chmod +x ./stockfish

echo "Запускаем бота..."
gunicorn -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT bot:app
