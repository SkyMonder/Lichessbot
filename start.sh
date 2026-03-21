#!/bin/bash
set -e

echo "Скачиваем Stockfish 17.1..."
wget -q https://github.com/official-stockfish/Stockfish/releases/download/sf_17.1/stockfish-ubuntu-x86-64.tar
tar -xf stockfish-ubuntu-x86-64.tar

# Копируем бинарник из папки в корень и даём права
cp stockfish/stockfish-ubuntu-x86-64 ./stockfish
chmod +x ./stockfish
rm -rf stockfish   # удаляем ненужную папку

echo "Пытаемся превратить аккаунт в бота..."
curl -X POST -d '' https://lichess.org/api/bot/account/upgrade \
     -H "Authorization: Bearer $LICHESS_TOKEN" \
     -H "Content-Type: application/x-www-form-urlencoded" \
     -w "\nHTTP status: %{http_code}\n" || echo "Апгрейд не удался (возможно, аккаунт уже бот)"

echo "Запускаем бота..."
exec gunicorn -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT bot:app
