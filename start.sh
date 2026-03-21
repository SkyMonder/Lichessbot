#!/bin/bash
set -e

echo "Скачиваем Stockfish 17.1..."
wget -q https://github.com/official-stockfish/Stockfish/releases/download/sf_17.1/stockfish-ubuntu-x86-64.tar
tar -xf stockfish-ubuntu-x86-64.tar

# Перемещаем бинарник в корень и даём права
if [ -f stockfish/stockfish-ubuntu-x86-64 ]; then
    mv stockfish/stockfish-ubuntu-x86-64 ./stockfish
    chmod +x ./stockfish
    rm -rf stockfish   # удаляем ненужную папку
else
    echo "Не найден бинарник Stockfish"
    exit 1
fi

echo "Запускаем бота..."
exec gunicorn -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT bot:app
