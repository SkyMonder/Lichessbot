#!/bin/bash
# Скачиваем исходники и компилируем Stockfish
wget https://github.com/official-stockfish/Stockfish/archive/sf_17.1.tar.gz
tar -xzf sf_17.1.tar.gz
cd Stockfish-sf_17.1/src
make -j2 build ARCH=x86-64
cp stockfish ../../
cd ../..
chmod +x stockfish

# Запускаем бота
gunicorn -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT bot:app
