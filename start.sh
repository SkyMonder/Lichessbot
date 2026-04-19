#!/bin/bash
set -e

echo "Скачиваем и настраиваем движки..."

# --- Stockfish 18 (основной) ---
wget -q https://github.com/official-stockfish/Stockfish/releases/download/sf_18/stockfish-ubuntu-x86-64-bmi2.tar
tar -xf stockfish-ubuntu-x86-64-bmi2.tar
cp stockfish/stockfish-ubuntu-x86-64-bmi2 ./stockfish
rm -rf stockfish
chmod +x ./stockfish

# --- Berserk (атакующий) ---
wget -q https://github.com/jhonnold/berserk/releases/download/20250218/berserk-20250218-linux.zip
unzip -j berserk-20250218-linux.zip "berserk-20250218-linux/berserk" -d .
mv berserk berserk_engine
chmod +x ./berserk_engine

# --- Clover (сбалансированный) ---
wget -q https://github.com/lucasmartin/Clover/releases/download/3.0.3/clover_3.0.3_linux.zip
unzip -j clover_3.0.3_linux.zip "clover_3.0.3_linux/clover" -d .
mv clover clover_engine
chmod +x ./clover_engine

# --- Установка unzip, если отсутствует ---
apt-get update && apt-get install -y unzip

echo "Превращаем аккаунт в бота (если нужно)..."
curl -X POST -d '' https://lichess.org/api/bot/account/upgrade \
     -H "Authorization: Bearer $LICHESS_TOKEN" \
     -H "Content-Type: application/x-www-form-urlencoded" \
     -w "\nHTTP status: %{http_code}\n" || echo "Апгрейд не удался (возможно, аккаунт уже бот)"

echo "Запускаем бота..."
exec gunicorn -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT bot:app
