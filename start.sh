#!/bin/bash
set -e

echo "=== Установка движков ==="

# --- Stockfish 18 (основной) ---
echo "Установка Stockfish 18..."
mkdir -p temp_stockfish
cd temp_stockfish
wget -q https://github.com/official-stockfish/Stockfish/releases/download/sf_18/stockfish-ubuntu-x86-64-bmi2.tar
tar -xf stockfish-ubuntu-x86-64-bmi2.tar
cp stockfish/stockfish-ubuntu-x86-64-bmi2 ../stockfish
cd ..
rm -rf temp_stockfish
chmod +x ./stockfish

# --- Berserk (опционально) ---
echo "Установка Berserk..."
mkdir -p temp_berserk
cd temp_berserk
wget -q https://github.com/jhonnold/berserk/releases/download/20250218/berserk-20250218-linux.zip
python3 -c "import zipfile; zipfile.ZipFile('berserk-20250218-linux.zip').extractall()" 2>/dev/null || true
if [ -f "berserk-20250218-linux/berserk" ]; then
    cp berserk-20250218-linux/berserk ../berserk_engine
    chmod +x ../berserk_engine
fi
cd ..
rm -rf temp_berserk

# --- Clover (опционально) ---
echo "Установка Clover..."
mkdir -p temp_clover
cd temp_clover
wget -q https://github.com/lucametehau/CloverEngine/releases/download/3.0.3/clover_3.0.3_linux.zip
python3 -c "import zipfile; zipfile.ZipFile('clover_3.0.3_linux.zip').extractall()" 2>/dev/null || true
if [ -f "clover_3.0.3_linux/clover" ]; then
    cp clover_3.0.3_linux/clover ../clover_engine
    chmod +x ../clover_engine
fi
cd ..
rm -rf temp_clover

echo "=== Превращаем аккаунт в бота ==="
curl -X POST -d '' https://lichess.org/api/bot/account/upgrade \
     -H "Authorization: Bearer $LICHESS_TOKEN" \
     -w "\nHTTP status: %{http_code}\n" || echo "Аккаунт уже бот"

echo "=== Запускаем бота ==="
exec gunicorn -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT bot:app
