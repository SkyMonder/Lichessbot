#!/bin/bash
set -e

echo "=== Установка Stockfish 18 ==="
mkdir -p temp_stockfish
cd temp_stockfish
wget -q https://github.com/official-stockfish/Stockfish/releases/download/sf_18/stockfish-ubuntu-x86-64-bmi2.tar
tar -xf stockfish-ubuntu-x86-64-bmi2.tar
cp stockfish/stockfish-ubuntu-x86-64-bmi2 ../stockfish
cd ..
rm -rf temp_stockfish
chmod +x ./stockfish

echo "=== Превращаем аккаунт в бота ==="
curl -X POST -d '' https://lichess.org/api/bot/account/upgrade \
     -H "Authorization: Bearer $LICHESS_TOKEN" \
     -w "\nHTTP status: %{http_code}\n" || echo "Аккаунт уже бот"

echo "=== Запускаем бота ==="
exec gunicorn -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT bot:app
