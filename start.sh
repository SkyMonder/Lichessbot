#!/bin/bash
# Скачиваем готовый бинарник Stockfish (Linux, x86-64, modern)
wget https://github.com/official-stockfish/Stockfish/releases/download/sf_17.1/stockfish-ubuntu-x86-64.tar
tar -xf stockfish-ubuntu-x86-64.tar
chmod +x stockfish/stockfish-ubuntu-x86-64
# Копируем в корень для удобства
cp stockfish/stockfish-ubuntu-x86-64 stockfish

# Запускаем бота
gunicorn -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT bot:app
