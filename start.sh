#!/bin/bash
set -e

echo "=== Установка движков ==="

# --- Stockfish 18 (лидер) ---
echo "Скачиваем Stockfish 18..."
wget -q https://github.com/official-stockfish/Stockfish/releases/download/sf_18/stockfish-ubuntu-x86-64-bmi2.tar
tar -xf stockfish-ubuntu-x86-64-bmi2.tar
# Внутри архива создается папка stockfish, откуда мы забираем бинарник
cp stockfish/stockfish-ubuntu-x86-64-bmi2 ./stockfish
rm -rf stockfish # Удаляем папку, так как она больше не нужна
chmod +x ./stockfish

# --- Berserk (опционально) ---
echo "Скачиваем Berserk..."
# Скачиваем архив
wget -q https://github.com/jhonnold/berserk/releases/download/20250218/berserk-20250218-linux.zip
# Распаковываем его с помощью Python (он всегда есть в образе)
python3 -c "import zipfile; zipfile.ZipFile('berserk-20250218-linux.zip').extractall()"
# Перемещаем бинарник из распакованной папки
mv berserk-20250218-linux/berserk ./berserk_engine
rm -rf berserk-20250218-linux berserk-20250218-linux.zip
chmod +x ./berserk_engine

# --- Clover (опционально) ---
echo "Скачиваем Clover..."
wget -q https://github.com/lucametehau/CloverEngine/releases/download/3.0.3/clover_3.0.3_linux.zip
python3 -c "import zipfile; zipfile.ZipFile('clover_3.0.3_linux.zip').extractall()"
mv clover_3.0.3_linux/clover ./clover_engine
rm -rf clover_3.0.3_linux clover_3.0.3_linux.zip
chmod +x ./clover_engine

echo "=== Превращаем аккаунт в бота ==="
# Эта команда нужна только для превращения обычного аккаунта в бота.
# Если аккаунт уже бот, она просто вернёт ошибку, и бот продолжит работу.
curl -X POST -d '' https://lichess.org/api/bot/account/upgrade \
     -H "Authorization: Bearer $LICHESS_TOKEN" \
     -H "Content-Type: application/x-www-form-urlencoded" \
     -w "\nHTTP status: %{http_code}\n" || echo "Аккаунт уже бот или ошибка"

echo "=== Запускаем бота ==="
exec gunicorn -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT bot:app
