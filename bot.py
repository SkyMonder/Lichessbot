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
from fastapi import FastAPI

TOKEN = os.environ.get("LICHESS_TOKEN")
STOCKFISH_PATH = "./stockfish"

if not TOKEN:
    raise RuntimeError("LICHESS_TOKEN environment variable not set")

session = berserk.TokenSession(TOKEN)
client = berserk.Client(session)

app = FastAPI()
engine = None
engine_lock = threading.Lock()
running = True

# Параметры вызова
CHALLENGE_TIME_MIN = 5
CHALLENGE_INCREMENT_SEC = 3
CHALLENGE_RATED = True
CHALLENGE_COLOR = "random"
CHALLENGE_INTERVAL = 300                # 5 минут между попытками
TARGET_RATING_MIN = 1000
TARGET_RATING_MAX = 3000

active_games = 0
games_lock = threading.Lock()

@app.get("/health")
def health():
    return {"status": "ok"}

def get_move_time_from_clock(clock_info):
    """
    Определяет время на ход в секундах на основе контроля времени.
    clock_info: dict с полями initial (минуты) и increment (секунды).
    """
    if not clock_info:
        return 5.0  # по умолчанию
    initial = clock_info.get('initial', 0)
    increment = clock_info.get('increment', 0)
    # Пуля: 1+0, 2+0, 1+1
    if initial <= 2 and increment == 0:
        return 0.5   # полсекунды, чтобы не проигрывать по времени
    # Быстрая пуля: 1+1, 2+1
    elif initial <= 2 and increment <= 1:
        return 0.8
    # Блиц: 3+0, 3+2, 5+0, 5+3
    elif initial <= 5 and increment <= 3:
        return 2.0
    # Рапид и классика
    else:
        return 5.0

def make_move_with_retry(game_id, board, time_limit_seconds, max_retries=3):
    """Отправляет ход с повторными попытками"""
    for attempt in range(max_retries):
        try:
            with engine_lock:
                result = engine.play(board, chess.engine.Limit(time=time_limit_seconds))
                move = result.move
            if not move:
                return False
            client.bots.make_move(game_id, move.uci())
            print(f"[{game_id}] >>> Ход {move.uci()} (время {time_limit_seconds:.1f}с)")
            sys.stdout.flush()
            return True
        except (berserk.exceptions.ApiError, requests.exceptions.ConnectionError) as e:
            print(f"[{game_id}] Ошибка отправки хода (попытка {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"[{game_id}] Не удалось отправить ход после {max_retries} попыток")
                traceback.print_exc()
                return False
        except Exception as e:
            print(f"[{game_id}] Неожиданная ошибка при ходе: {e}")
            traceback.print_exc()
            return False
    return False

def play_game(game_id, initial_fen):
    """Обрабатывает одну партию с адаптивным временем на ход"""
    global active_games
    with games_lock:
        active_games += 1
        print(f"[{game_id}] Активных игр: {active_games}")
    try:
        board = chess.Board(initial_fen) if initial_fen else chess.Board()
        print(f"[{game_id}] Игра начата. Начальная позиция: {board.fen()}")
        sys.stdout.flush()

        my_id = client.account.get()['id']
        print(f"[{game_id}] Мой ID: {my_id}")
        sys.stdout.flush()

        white_id = None
        black_id = None
        made_first_move = False
        clock_info = None

        while True:
            try:
                stream = client.bots.stream_game_state(game_id)
                for event in stream:
                    print(f"[{game_id}] EVENT: {event['type']}")
                    sys.stdout.flush()

                    if event['type'] == 'gameFull':
                        white_id = event.get('white', {}).get('id')
                        black_id = event.get('black', {}).get('id')
                        # Сохраняем контроль времени (присутствует в gameFull)
                        clock_info = event.get('clock')
                        moves = event.get('state', {}).get('moves', '')
                        if moves:
                            for move in moves.split():
                                board.push_uci(move)
                        print(f"[{game_id}] gameFull: white={white_id}, black={black_id}, clock={clock_info}")
                    elif event['type'] == 'gameState':
                        moves = event.get('moves', '')
                        if moves:
                            current_moves = moves.split()
                            while len(current_moves) > len(board.move_stack):
                                board.push_uci(current_moves[len(board.move_stack)])
                        # clock может обновляться в gameState
                        if event.get('clock'):
                            clock_info = event.get('clock')
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
                        time_limit = get_move_time_from_clock(clock_info)
                        print(f"[{game_id}] Ход белых (бота) по {event['type']} (время на ход: {time_limit:.1f}с)")
                        if not made_first_move:
                            made_first_move = True
                        make_move_with_retry(game_id, board, time_limit)
                    elif board.turn == chess.BLACK and black_id == my_id:
                        time_limit = get_move_time_from_clock(clock_info)
                        print(f"[{game_id}] Ход чёрных (бота) по {event['type']} (время на ход: {time_limit:.1f}с)")
                        if not made_first_move:
                            made_first_move = True
                        make_move_with_retry(game_id, board, time_limit)

            except (berserk.exceptions.ApiError, requests.exceptions.ConnectionError) as e:
                print(f"[{game_id}] Ошибка соединения в потоке игры: {e}. Переподключаемся через 5 секунд...")
                time.sleep(5)
                continue
            except Exception as e:
                print(f"[{game_id}] Неожиданная ошибка в игре: {e}")
                traceback.print_exc()
                break
    except Exception as e:
        print(f"[{game_id}] Ошибка в игре (внешний уровень): {e}")
        traceback.print_exc()
    finally:
        with games_lock:
            active_games -= 1
            print(f"[{game_id}] Активных игр: {active_games}")

def send_challenge(username):
    """Отправляет вызов с повторными попытками"""
    for attempt in range(3):
        try:
            clock_limit_sec = CHALLENGE_TIME_MIN * 60
            print(f"Отправка вызова {username} ({CHALLENGE_TIME_MIN}+{CHALLENGE_INCREMENT_SEC})")
            client.challenges.create(
                username=username,
                rated=CHALLENGE_RATED,
                clock_limit=clock_limit_sec,
                clock_increment=CHALLENGE_INCREMENT_SEC,
                color=CHALLENGE_COLOR,
                variant="standard"
            )
            print(f"✓ Вызов отправлен {username}")
            sys.stdout.flush()
            time.sleep(30)
            return
        except berserk.exceptions.ApiError as e:
            if hasattr(e, 'response') and e.response is not None:
                if e.response.status_code == 429:
                    print("⚠️ Слишком много запросов (429). Делаю паузу 180 секунд.")
                    time.sleep(180)
                else:
                    print(f"✗ Ошибка вызова {username} (попытка {attempt+1}/3): {e.response.status_code} {e.response.text}")
                    if attempt == 2:
                        return
                    time.sleep(5)
            else:
                print(f"✗ Ошибка вызова {username}: {e}")
                return
        except Exception as e:
            print(f"✗ Ошибка вызова {username}: {e}")
            traceback.print_exc()
            return

def challenge_loop():
    """Периодически отправляет вызовы, если нет активных игр"""
    while running:
        with games_lock:
            if active_games > 0:
                print(f"Идёт игра ({active_games}), пропускаем отправку вызова.")
                time.sleep(60)
                continue
        time.sleep(CHALLENGE_INTERVAL)
        print("challenge_loop: ищу кандидатов...")
        sys.stdout.flush()
        try:
            leaders = client.users.get_leaderboard(perf_type="blitz", count=200)
            candidates = []
            for entry in leaders:
                rating = entry['perfs']['blitz'].get('rating')
                if rating and TARGET_RATING_MIN <= rating <= TARGET_RATING_MAX:
                    candidates.append(entry['username'])
            if candidates:
                target = random.choice(candidates)
                print(f"Выбран {target} из {len(candidates)} кандидатов")
                send_challenge(target)
            else:
                print("Нет подходящих игроков в лидерборде")
        except Exception as e:
            print(f"Ошибка в challenge_loop: {e}")
            traceback.print_exc()
            sys.stdout.flush()

def run_bot():
    global engine
    try:
        print("Загружаем Stockfish...")
        sys.stdout.flush()
        engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
        engine.configure({"Skill Level": 20})
        print("Stockfish загружен и настроен на максимальную силу.")
        sys.stdout.flush()
    except Exception as e:
        print(f"Не удалось запустить Stockfish: {e}")
        traceback.print_exc()
        return

    print("Бот запущен. Ожидание вызовов...")
    sys.stdout.flush()

    challenger_thread = threading.Thread(target=challenge_loop, daemon=True)
    challenger_thread.start()
    print("Поток рассылки вызовов запущен")
    sys.stdout.flush()

    my_id = client.account.get()['id']

    while running:
        try:
            for event in client.bots.stream_incoming_events():
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
                        print(f"Вызов от {challenger} принят")
                        threading.Thread(
                            target=play_game,
                            args=(ch['id'], initial_fen),
                            daemon=True
                        ).start()
                    except Exception as e:
                        print(f"Ошибка при принятии вызова: {e}")
                        traceback.print_exc()
        except (berserk.exceptions.ApiError, requests.exceptions.ConnectionError) as e:
            print(f"Главный цикл: ошибка соединения ({e}). Переподключаемся через 10 секунд...")
            time.sleep(10)
            continue
        except Exception as e:
            print(f"Главный цикл: неожиданная ошибка: {e}")
            traceback.print_exc()
            time.sleep(10)
            continue

thread = threading.Thread(target=run_bot, daemon=True)
thread.start()
