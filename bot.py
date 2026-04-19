import os
import sys
import threading
import time
import random
import traceback
import chess
import chess.engine
import chess.polyglot
import berserk
import requests
from pathlib import Path
from collections import defaultdict
from fastapi import FastAPI, HTTPException

# ------------------------------------------------------------------
# Настройки окружения
# ------------------------------------------------------------------
TOKEN = os.environ.get("LICHESS_TOKEN")
STOCKFISH_PATH = "./stockfish"
BERZERK_PATH = "./berserk_engine"
CLOVER_PATH = "./clover_engine"

if not TOKEN:
    raise RuntimeError("LICHESS_TOKEN environment variable not set")

session = berserk.TokenSession(TOKEN)
client = berserk.Client(session)

app = FastAPI()
running = True
active_games = 0
games_lock = threading.Lock()

# Параметры для дебютной книги и эндшпильных таблиц
OPENING_BOOK_PATH = "books/Perfect2023.bin"   # помести файл книги в папку books/
TABLEBASE_PATH = "tb"                         # папка с таблицами (3-4-5)

# Параметры движков (максимальная производительность)
STOCKFISH_HASH_MB = 256
STOCKFISH_THREADS = 2
OTHER_ENGINE_HASH_MB = 128
OTHER_ENGINE_THREADS = 1

# Параметры вызова (ручной / автоматический)
CHALLENGE_TIME_MIN = 5
CHALLENGE_INCREMENT_SEC = 3
CHALLENGE_RATED = True
CHALLENGE_COLOR = "random"
# Активная рассылка отключена – только ручные вызовы через /challenge
# Если хотите включить, раскомментируйте в run_bot() и установите интервал
# CHALLENGE_INTERVAL = 600

engines = {}          # словарь с движками
engine_lock = threading.Lock()

# ------------------------------------------------------------------
# FastAPI endpoints
# ------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/challenge/{username}")
def manual_challenge(username: str):
    """Отправить вызов указанному игроку (ручной режим)."""
    try:
        clock_limit_sec = CHALLENGE_TIME_MIN * 60
        client.challenges.create(
            username=username,
            rated=CHALLENGE_RATED,
            clock_limit=clock_limit_sec,
            clock_increment=CHALLENGE_INCREMENT_SEC,
            color=CHALLENGE_COLOR,
            variant="standard"
        )
        return {"status": "ok", "message": f"Challenge sent to {username}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ------------------------------------------------------------------
# Адаптивное время на ход
# ------------------------------------------------------------------
def get_move_time_from_clock(clock_event):
    increment = clock_event.get('increment', 0)
    if increment <= 1:          # пуля (0-1 сек)
        return 0.5
    elif increment <= 3:        # блиц (2-3 сек)
        return 2.0
    else:                       # рапид/классика
        return 5.0

# ------------------------------------------------------------------
# Инициализация движков (Stockfish, Berserk, Clover)
# ------------------------------------------------------------------
def init_engines():
    global engines
    engines = {}
    try:
        # Stockfish 18 (лидер)
        print("Загружаем Stockfish 18 (лидер)...")
        stockfish = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
        stockfish.configure({
            "Skill Level": 20,
            "Hash": STOCKFISH_HASH_MB,
            "Threads": STOCKFISH_THREADS,
            "Contempt": 0,
            "Move Overhead": 100,
            "Slow Mover": 100,
        })
        if TABLEBASE_PATH and Path(TABLEBASE_PATH).exists():
            stockfish.configure({"SyzygyPath": TABLEBASE_PATH, "SyzygyProbeDepth": 1})
        engines['stockfish'] = stockfish

        # Berserk
        if Path(BERZERK_PATH).exists():
            print("Загружаем Berserk...")
            berserk_eng = chess.engine.SimpleEngine.popen_uci(BERZERK_PATH)
            berserk_eng.configure({
                "Skill Level": 20,
                "Hash": OTHER_ENGINE_HASH_MB,
                "Threads": OTHER_ENGINE_THREADS,
                "Contempt": 15,
            })
            engines['berserk'] = berserk_eng

        # Clover
        if Path(CLOVER_PATH).exists():
            print("Загружаем Clover...")
            clover_eng = chess.engine.SimpleEngine.popen_uci(CLOVER_PATH)
            clover_eng.configure({
                "Skill Level": 20,
                "Hash": OTHER_ENGINE_HASH_MB,
                "Threads": OTHER_ENGINE_THREADS,
                "Contempt": 0,
            })
            engines['clover'] = clover_eng

        print(f"Загружено движков: {len(engines)}")
    except Exception as e:
        print(f"Ошибка при загрузке движков: {e}")
        traceback.print_exc()
        if not engines:
            sys.exit(1)

# ------------------------------------------------------------------
# Выбор лучшего хода (дебютная книга + голосование движков)
# ------------------------------------------------------------------
def get_best_move(board, move_time):
    # 1. Дебютная книга
    if OPENING_BOOK_PATH and Path(OPENING_BOOK_PATH).exists():
        try:
            with chess.polyglot.open_reader(OPENING_BOOK_PATH) as reader:
                for entry in reader.find_all(board):
                    move = entry.move()
                    print(f"Ход из дебютной книги: {move.uci()}")
                    return move.uci()
        except Exception as e:
            print(f"Ошибка чтения дебютной книги: {e}")

    # 2. Опрос движков
    candidates = defaultdict(list)
    for name, eng in engines.items():
        try:
            # Анализ с ограничением по времени
            analysis = eng.analyse(board, chess.engine.Limit(time=move_time))
            move = analysis.get('pv')[0] if analysis.get('pv') else None
            score = analysis.get('score')
            if not move:
                continue
            # Преобразуем оценку в числовой вес
            if score.is_mate():
                weight = 10000 if score.mate() > 0 else -10000
            else:
                w = score.white().score() if board.turn == chess.WHITE else -score.white().score()
                weight = max(-500, min(500, w)) / 100.0
            candidates[move.uci()].append(weight)
        except Exception as e:
            print(f"Ошибка движка {name}: {e}")

    if not candidates:
        return None
    # Выбираем ход с максимальной средней оценкой
    best_move = max(candidates.items(), key=lambda x: sum(x[1]) / len(x[1]))
    print(f"Голосование: выбран {best_move[0]} (оценки: {best_move[1]})")
    return best_move[0]

# ------------------------------------------------------------------
# Отправка хода с повторными попытками
# ------------------------------------------------------------------
def make_move_with_retry(game_id, board, move_time):
    for attempt in range(3):
        try:
            move_uci = get_best_move(board, move_time)
            if not move_uci:
                return False
            client.bots.make_move(game_id, move_uci)
            print(f"[{game_id}] >>> {move_uci} ({move_time:.1f}s)")
            sys.stdout.flush()
            return True
        except (berserk.exceptions.ApiError, requests.exceptions.ConnectionError) as e:
            print(f"[{game_id}] Ошибка хода (попытка {attempt+1}/3): {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                return False
        except Exception as e:
            print(f"[{game_id}] Неожиданная ошибка: {e}")
            traceback.print_exc()
            return False
    return False

# ------------------------------------------------------------------
# Игровой процесс (один поток на партию)
# ------------------------------------------------------------------
def play_game(game_id, initial_fen):
    global active_games
    with games_lock:
        active_games += 1
        print(f"[{game_id}] Активных игр: {active_games}")

    try:
        board = chess.Board(initial_fen) if initial_fen else chess.Board()
        print(f"[{game_id}] Начальная позиция: {board.fen()}")
        sys.stdout.flush()

        my_id = client.account.get()['id']
        white_id = black_id = None
        made_first_move = False
        move_time = 5.0

        while True:
            try:
                stream = client.bots.stream_game_state(game_id)
                for event in stream:
                    if 'clock' in event:
                        move_time = get_move_time_from_clock(event['clock'])

                    if event['type'] == 'gameFull':
                        white_id = event.get('white', {}).get('id')
                        black_id = event.get('black', {}).get('id')
                        moves = event.get('state', {}).get('moves', '')
                        if moves:
                            for move in moves.split():
                                board.push_uci(move)
                    elif event['type'] == 'gameState':
                        moves = event.get('moves', '')
                        if moves:
                            current = moves.split()
                            while len(current) > len(board.move_stack):
                                board.push_uci(current[len(board.move_stack)])
                        if white_id is None:
                            white_id = event.get('white', {}).get('id')
                        if black_id is None:
                            black_id = event.get('black', {}).get('id')
                    else:
                        continue

                    if event.get('status') and event.get('status') != 'started':
                        print(f"[{game_id}] Игра завершена. Статус: {event.get('status')}")
                        return

                    if white_id is None or black_id is None:
                        continue

                    if board.turn == chess.WHITE and white_id == my_id:
                        print(f"[{game_id}] Ход белых (время {move_time}s)")
                        if not made_first_move:
                            made_first_move = True
                        make_move_with_retry(game_id, board, move_time)
                    elif board.turn == chess.BLACK and black_id == my_id:
                        print(f"[{game_id}] Ход чёрных (время {move_time}s)")
                        if not made_first_move:
                            made_first_move = True
                        make_move_with_retry(game_id, board, move_time)

            except (berserk.exceptions.ApiError, requests.exceptions.ConnectionError) as e:
                print(f"[{game_id}] Ошибка потока игры: {e}. Переподключение через 5 сек...")
                time.sleep(5)
                continue
            except Exception as e:
                print(f"[{game_id}] Критическая ошибка в игре: {e}")
                traceback.print_exc()
                break
    except Exception as e:
        print(f"[{game_id}] Внешняя ошибка: {e}")
        traceback.print_exc()
    finally:
        with games_lock:
            active_games -= 1
            print(f"[{game_id}] Активных игр: {active_games}")

# ------------------------------------------------------------------
# Проверка и восстановление активных игр при старте
# ------------------------------------------------------------------
def resume_active_games():
    """При запуске бота находит все текущие игры аккаунта и подключается к ним."""
    try:
        print("Проверяем активные игры...")
        # Получаем список текущих игр бота (используем API account)
        ongoing = client.games.get_ongoing()
        for game in ongoing:
            game_id = game['gameId']
            initial_fen = game.get('initialFen')
            print(f"Найдена активная игра {game_id}, подключаемся...")
            threading.Thread(target=play_game, args=(game_id, initial_fen), daemon=True).start()
        print(f"Восстановлено игр: {len(ongoing)}")
    except Exception as e:
        print(f"Ошибка при восстановлении активных игр: {e}")

# ------------------------------------------------------------------
# Основной цикл бота (приём вызовов)
# ------------------------------------------------------------------
def run_bot():
    init_engines()

    print("Бот запущен. Ожидание вызовов...")
    sys.stdout.flush()

    # Восстановить активные игры (если бот перезапустился)
    resume_active_games()

    # Активная рассылка вызовов (отключена по умолчанию – только ручные)
    # Если хотите включить, раскомментируйте:
    # challenger_thread = threading.Thread(target=challenge_loop, daemon=True)
    # challenger_thread.start()
    # print("Поток рассылки вызовов запущен")

    my_id = client.account.get()['id']
    print(f"Мой ID: {my_id}")

    while running:
        try:
            event_stream = client.bots.stream_incoming_events()
            for event in event_stream:
                print(f"Входящее событие: {event['type']}")
                sys.stdout.flush()
                if event['type'] == 'challenge':
                    try:
                        ch = event['challenge']
                        challenger = ch['challenger']['id']
                        if challenger == my_id:
                            print(f"Пропускаем собственный вызов {ch['id']}")
                            continue
                        print(f"Получен вызов от {challenger}")
                        initial_fen = ch.get('initialFen')
                        client.bots.accept_challenge(ch['id'])
                        print(f"Вызов принят")
                        threading.Thread(
                            target=play_game,
                            args=(ch['id'], initial_fen),
                            daemon=True
                        ).start()
                    except Exception as e:
                        print(f"Ошибка принятия вызова: {e}")
                        traceback.print_exc()
        except (berserk.exceptions.ApiError, requests.exceptions.ConnectionError) as e:
            if isinstance(e, berserk.exceptions.ApiError) and hasattr(e, 'response') and e.response is not None:
                if e.response.status_code == 429:
                    print("429 Too Many Requests. Пауза 5 минут.")
                    time.sleep(300)
                    continue
            print(f"Ошибка соединения: {e}. Переподключение через 30 сек...")
            time.sleep(30)
            continue
        except Exception as e:
            print(f"Неожиданная ошибка: {e}")
            traceback.print_exc()
            time.sleep(30)
            continue

# ------------------------------------------------------------------
# Запуск
# ------------------------------------------------------------------
thread = threading.Thread(target=run_bot, daemon=True)
thread.start()
