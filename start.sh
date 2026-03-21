#!/bin/bash
set -e

# Скачиваем готовый бинарник Stockfish (Ubuntu, x86-64)
wget https://github.com/official-stockfish/Stockfish/releases/download/sf_17.1/stockfish-ubuntu-x86-64.tar
tar -xf stockfish-ubuntu-x86-64.tar

# Копируем бинарник из папки stockfish в корень проекта
if [ -f stockfish/stockfish-ubuntu-x86-64 ]; then
    cp -f stockfish/stockfish-ubuntu-x86-64 ./stockfish
elif [ -f stockfish/stockfish ]; then
    cp -f stockfish/stockfish ./stockfish
else
    echo "Stockfish binary not found after extraction"
    exit 1
fi

# Делаем исполняемым
chmod +x ./stockfish

# Запускаем сервер
gunicorn -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT bot:app
