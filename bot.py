import os
import sys
import threading
import time
import random
import traceback
import chess
import chess.engine
import berserk
import requests
from fastapi import FastAPI, HTTPException

# ------------------------------------------------------------------
# Настройки
# ------------------------------------------------------------------
TOKEN = os.environ.get("LICHESS_TOKEN")
STOCKFISH_PATH = "./stockfish"
BERSERK_PATH = "./berserk_engine"
CLOVER_PATH = "./clover_engine"

if not TOKEN:
    raise RuntimeError("LICHESS_TOKEN environment variable not set")

session = berserk.TokenSession(TOKEN)
client = berserk.Client(session)

app = FastAPI()
running = True

# Движки
engines = {}
engine_lock = threading.Lock()

# Счётчик активных игр
active_games = 0
games_lock = threading.Lock()

# ------------------------------------------------------------------
# Health‑check (для keep‑alive на Render)
# ------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}

# ------------------------------------------------------------------
# --- НОВАЯ ФУНКЦИЯ ЧАТА: Отправка приветствия ---
# ------------------------------------------------------------------
def send_greeting(game_id, opponent_username):
    """Отправляет приветственное сообщение сопернику в начале игры."""
    greetings = [
        f"Привет, {opponent_username}! Желаю хорошей игры! 🤝",
        f"Здравствуй, {opponent_username}. Да победит сильнейший. 🧠",
        f"Надеюсь на интересную партию, {opponent_username}! 🎉",
    ]
    message = random.choice(greetings)
    try:
        client.bots.post_message(game_id, message, spectator=False)
        print(f"[{game_id}] Приветствие отправлено: {message}")
    except Exception as e:
        print(f"[{game_id}] Не удалось отправить приветствие: {e}")

# ------------------------------------------------------------------
# --- НОВАЯ ФУНКЦИЯ ЧАТА: Отправка сообщения по результату игры ---
# ------------------------------------------------------------------
def send_game_result_message(game_id, board, my_id):
    """Анализирует исход партии и отправляет соответствующее сообщение."""
    result_message = None
    # 1. Проверяем, не закончилась ли игра матом
    if board.is_checkmate():
        if board.turn == my_id: # Мы получили мат
            result_message = "😞 Это был мат. Отличная игра, поздравляю!"
        else: # Мы поставили мат
            result_message = "🏆 Мат! Было приятно сыграть, спасибо за партию!"
    # 2. Если не мат, проверяем на пат или недостаток материала
    elif board.is_stalemate() or board.is_insufficient_material():
        result_message = "🤝 Ничья. Это было напряжённое сражение!"
    # 3. Если игра прервана по другим причинам (таймаут, отказ)
    elif board.is_game_over():
        result_message = "Игра завершена. Спасибо за партию! 👋"
    
    if result_message:
        try:
            client.bots.post_message(game_id, result_message, spectator=False)
            print(f"[{game_id}] Сообщение об итоге игры отправлено: {result_message}")
        except Exception as e:
            print(f"[{game_id}] Не удалось отправить сообщение об итоге игры: {e}")

# ------------------------------------------------------------------
# Адаптивное время на ход (в секундах)
# ------------------------------------------------------------------
def get_move_time_from_clock(clock_event):
    increment = clock_event.get('increment', 0)
    if increment <= 1:      # пуля
        return 0.5
    elif increment <= 3:    # блиц
        return 2.0
    else:                   # рапид/классика
        return 5.0

# ------------------------------------------------------------------
# Инициализация движков
# ------------------------------------------------------------------
def init_engines():
    global engines
    engines = {}
    try:
        # Stockfish (лидер)
        print("Загружаем Stockfish 18...")
        sf = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
        sf.configure({
            "Skill Level": 20,
            "Hash": 256,
            "Threads": 2,
            "Contempt": 0,
            "Move Overhead": 100,
            "Slow Mover": 100,
        })
        engines['stockfish'] = sf

        # Berserk (если файл существует)
        if os.path.exists(BERSERK_PATH):
            print("Загружаем Berserk...")
            be = chess.engine.SimpleEngine.popen_uci(BERSERK_PATH)
            be.configure({
                "Skill Level": 20,
                "Hash": 128,
                "Threads": 1,
                "Contempt": 15,
            })
            engines['berserk'] = be
        else:
            print("Berserk не найден, пропускаем")

        # Clover (если файл существует)
        if os.path.exists(CLOVER_PATH):
            print("Загружаем Clover...")
            ce = chess.engine.SimpleEngine.popen_uci(CLOVER_PATH)
            ce.configure({
                "Skill Level": 20,
                "Hash": 128,
                "Threads": 1,
                "Contempt": 0,
            })
            engines['clover'] = ce
        else:
            print("Clover не найден, пропускаем")

        print(f"Загружено движков: {len(engines)}")
        if not engines:
            raise RuntimeError("Не загружен ни один движок")
    except Exception as e:
        print(f"Ошибка загрузки движков: {e}")
        traceback.print_exc()
        sys.exit(1)

# ------------------------------------------------------------------
# Голосование: выбираем лучший ход
# ------------------------------------------------------------------
def get_best_move(board, move_time):
    candidates = {}
    for name, eng in engines.items():
        try:
            analysis = eng.analyse(board, chess.engine.Limit(time=move_time))
            move = analysis.get('pv')[0] if analysis.get('pv') else None
            score = analysis.get('score')
            if not move:
                continue
            # Нормализуем оценку
            if score.is_mate():
                weight = 10000 if score.mate() > 0 else -10000
            else:
                w = score.white().score() if board.turn == chess.WHITE else -score.white().score()
                weight = max(-500, min(500, w)) / 100.0
            move_uci = move.uci()
            candidates.setdefault(move_uci, []).append(weight)
        except Exception as e:
            print(f"Ошибка движка {name}: {e}")
    if not candidates:
        return None
    # Ход с максимальной средней оценкой
    best = max(candidates.items(), key=lambda x: sum(x[1]) / len(x[1]))
    print(f"Голосование: выбран {best[0]} (оценки: {best[1]})")
    return best[0]

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
            print(f"[{game_id}] Ошибка (попытка {attempt+1}/3): {e}")
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
# Обработка партии (с интегрированными функциями чата)
# ------------------------------------------------------------------
def play_game(game_id, initial_fen):
    global active_games
    with games_lock:
        active_games += 1
        print(f"[{game_id}] Активных игр: {active_games}")
    try:
        board = chess.Board(initial_fen) if initial_fen else chess.Board()
        print(f"[{game_id}] Старт: {board.fen()}")
        sys.stdout.flush()

        my_id = client.account.get()['id']
        # Получаем никнейм соперника для приветствия
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
                        # --- ИНТЕГРАЦИЯ ФУНКЦИИ ЧАТА: Приветствие ---
                        opponent_username = black_id if white_id == my_id else white_id
                        send_greeting(game_id, opponent_username)
                        # --- ------------------------------------ ---
                        moves = event.get('state', {}).get('moves', '')
                        if moves:
                            for m in moves.split():
                                board.push_uci(m)
                    elif event['type'] == 'gameState':
                        moves = event.get('moves', '')
                        if moves:
                            cur = moves.split()
                            while len(cur) > len(board.move_stack):
                                board.push_uci(cur[len(board.move_stack)])
                        if white_id is None:
                            white_id = event.get('white', {}).get('id')
                        if black_id is None:
                            black_id = event.get('black', {}).get('id')
                    else:
                        continue

                    if event.get('status') and event.get('status') != 'started':
                        print(f"[{game_id}] Завершена: {event.get('status')}")
                        # --- ИНТЕГРАЦИЯ ФУНКЦИИ ЧАТА: Сообщение о результате ---
                        send_game_result_message(game_id, board, my_id)
                        # --- -------------------------------------------- ---
                        return

                    if white_id is None or black_id is None:
                        continue

                    if board.turn == chess.WHITE and white_id == my_id:
                        if not made_first_move:
                            made_first_move = True
                        make_move_with_retry(game_id, board, move_time)
                    elif board.turn == chess.BLACK and black_id == my_id:
                        if not made_first_move:
                            made_first_move = True
                        make_move_with_retry(game_id, board, move_time)

            except (berserk.exceptions.ApiError, requests.exceptions.ConnectionError) as e:
                print(f"[{game_id}] Ошибка потока: {e}. Переподключение через 5 сек...")
                time.sleep(5)
                continue
            except Exception as e:
                print(f"[{game_id}] Критическая ошибка: {e}")
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
# Ручной вызов через HTTP
# ------------------------------------------------------------------
@app.get("/challenge/{username}")
def manual_challenge(username: str):
    try:
        clock = 5 * 60
        client.challenges.create(
            username=username,
            rated=True,
            clock_limit=clock,
            clock_increment=3,
            color="random",
            variant="standard"
        )
        return {"status": "ok", "message": f"Challenge sent to {username}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ------------------------------------------------------------------
# Основной поток
# ------------------------------------------------------------------
def run_bot():
    init_engines()
    print("Бот запущен. Ожидание вызовов...")
    sys.stdout.flush()

    my_id = client.account.get()['id']
    print(f"Мой ID: {my_id}")

    while running:
        try:
            event_stream = client.bots.stream_incoming_events()
            for event in event_stream:
                if event['type'] == 'challenge':
                    ch = event['challenge']
                    challenger = ch['challenger']['id']
                    if challenger == my_id:
                        continue
                    print(f"Получен вызов от {challenger}")
                    initial_fen = ch.get('initialFen')
                    client.bots.accept_challenge(ch['id'])
                    threading.Thread(
                        target=play_game,
                        args=(ch['id'], initial_fen),
                        daemon=True
                    ).start()
        except (berserk.exceptions.ApiError, requests.exceptions.ConnectionError) as e:
            if hasattr(e, 'response') and e.response and e.response.status_code == 429:
                print("429 Too Many Requests. Пауза 5 минут.")
                time.sleep(300)
            else:
                print(f"Ошибка соединения: {e}. Пауза 30 сек.")
                time.sleep(30)
        except Exception as e:
            print(f"Неожиданная ошибка: {e}")
            traceback.print_exc()
            time.sleep(30)

threading.Thread(target=run_bot, daemon=True).start()
