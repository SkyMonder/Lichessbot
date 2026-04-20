#!/bin/bash
set -e

echo "=== Установка движков ==="

# --- Stockfish 18 (лидер) ---
echo "Скачиваем Stockfish 18..."
wget -q https://github.com/official-stockfish/Stockfish/releases/download/sf_18/stockfish-ubuntu-x86-64-bmi2.tar
tar -xf stockfish-ubuntu-x86-64-bmi2.tar
# Покажем содержимое для отладки
echo "Содержимое папки stockfish:"
ls -la stockfish/
# Найдём любой исполняемый файл внутри папки stockfish (не папку)
BINARY=$(find stockfish -type f -executable 2>/dev/null | head -1)
if [ -z "$BINARY" ]; then
    # Если не нашли исполняемый, возьмём любой файл, кроме папок
    BINARY=$(find stockfish -type f | head -1)
fi
if [ -z "$BINARY" ]; then
    echo "Не удалось найти бинарник Stockfish в папке stockfish"
    exit 1
fi
echo "Найден бинарник: $BINARY"
rm -rf ./stockfish_bin
mv "$BINARY" ./stockfish
rm -rf stockfish
chmod +x ./stockfish

# --- Berserk (опционально) ---
echo "Скачиваем Berserk..."
wget -q https://github.com/jhonnold/berserk/releases/download/20250218/berserk-20250218-linux.zip
python3 -c "import zipfile; zipfile.ZipFile('berserk-20250218-linux.zip').extractall()" || true
if [ -f "berserk-20250218-linux/berserk" ]; then
    rm -rf ./berserk_engine
    mv berserk-20250218-linux/berserk ./berserk_engine
    chmod +x ./berserk_engine
else
    echo "Berserk не найден в архиве, пропускаем"
fi
rm -rf berserk-20250218-linux berserk-20250218-linux.zip

# --- Clover (опционально) ---
echo "Скачиваем Clover..."
wget -q https://github.com/lucametehau/CloverEngine/releases/download/3.0.3/clover_3.0.3_linux.zip
python3 -c "import zipfile; zipfile.ZipFile('clover_3.0.3_linux.zip').extractall()" || true
if [ -f "clover_3.0.3_linux/clover" ]; then
    rm -rf ./clover_engine
    mv clover_3.0.3_linux/clover ./clover_engine
    chmod +x ./clover_engine
else
    echo "Clover не найден в архиве, пропускаем"
fi
rm -rf clover_3.0.3_linux clover_3.0.3_linux.zip

echo "=== Превращаем аккаунт в бота ==="
curl -X POST -d '' https://lichess.org/api/bot/account/upgrade \
     -H "Authorization: Bearer $LICHESS_TOKEN" \
     -H "Content-Type: application/x-www-form-urlencoded" \
     -w "\nHTTP status: %{http_code}\n" || echo "Аккаунт уже бот или ошибка"

echo "=== Запускаем бота ==="
exec gunicorn -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT bot:app
