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

# Параметры активной рассылки вызовов
CHALLENGE_TIME_MIN = 5          # минут
CHALLENGE_INCREMENT_SEC = 3     # секунд
CHALLENGE_RATED = True
CHALLENGE_COLOR = "random"
CHALLENGE_INTERVAL = 600         # 10 минут между попытками (безопасно)
TARGET_RATING_MIN = 1000
TARGET_RATING_MAX = 3000

active_games = 0
games_lock = threading.Lock()

@app.get("/health")
def health():
    return {"status": "ok"}

def get_move_time_from_clock(clock_event):
    """Адаптивное время на ход в зависимости от контроля"""
    increment = clock_event.get('increment', 0)
    if increment <= 1:
        return 0.5       # пуля
    elif increment <= 3:
        return 2.0       # блиц
    else:
        return 5.0       # рапид/классика

def make_move_with_retry(game_id, board, move_time):
    """Делает ход с повторами при ошибках сети"""
    for attempt in range(3):
        try:
            with engine_lock:
                result = engine.play(board, chess.engine.Limit(time=move_time))
                move = result.move
            if not move:
                return False
            client.bots.make_move(game_id, move.uci())
            print(f"[{game_id}] >>> {move.uci()} ({move_time:.1f}s)")
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

def send_chat_message(game_id, message):
    """Отправляет сообщение в чат партии"""
    try:
        client.bots.post_message(game_id, message)
        print(f"[{game_id}] Чат: {message}")
        sys.stdout.flush()
    except Exception as e:
        print(f"[{game_id}] Ошибка отправки сообщения: {e}")

def play_game(game_id, initial_fen):
    """Обрабатывает одну партию с адаптивным временем и чатом"""
    global active_games
    with games_lock:
        active_games += 1
        print(f"[{game_id}] Активных игр: {active_games}")
    try:
        board = chess.Board(initial_fen) if initial_fen else chess.Board()
        print(f"[{game_id}] Старт: {board.fen()}")
        sys.stdout.flush()

        my_id = client.account.get()['id']
        white_id = black_id = None
        made_first_move = False
        move_time = 5.0
        greeted = False      # флаг, что приветствие уже отправлено

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
                        # Игра завершена, отправляем сообщение
                        print(f"[{game_id}] Завершена: {event.get('status')}")
                        if event.get('status') == 'mate':
                            # Определяем победителя
                            if board.is_checkmate():
                                winner_color = 'white' if board.turn == chess.BLACK else 'black'
                            else:
                                winner_color = None
                            if winner_color == 'white' and white_id == my_id:
                                send_chat_message(game_id, "Ъ")
                            elif winner_color == 'black' and black_id == my_id:
                                send_chat_message(game_id, "Ъ")
                            else:
                                send_chat_message(game_id, "ахуеть...")
                        return

                    if white_id is None or black_id is None:
                        continue

                    # Если это наш ход и мы ещё не поздоровались
                    if not greeted and ((board.turn == chess.WHITE and white_id == my_id) or (board.turn == chess.BLACK and black_id == my_id)):
                        send_chat_message(game_id, "Дарова, я ботинок, обозвали ботом из за моей силы, ща глянем, насколько ты харош.")
                        greeted = True

                    if board.turn == chess.WHITE and white_id == my_id:
                        print(f"[{game_id}] Ход белых ({move_time}s)")
                        if not made_first_move:
                            made_first_move = True
                        make_move_with_retry(game_id, board, move_time)
                    elif board.turn == chess.BLACK and black_id == my_id:
                        print(f"[{game_id}] Ход чёрных ({move_time}s)")
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
            time.sleep(30)  # пауза после успешной отправки
            return
        except berserk.exceptions.ApiError as e:
            if hasattr(e, 'response') and e.response is not None:
                if e.response.status_code == 429:
                    print("⚠️ Слишком много запросов (429). Делаю паузу 300 секунд.")
                    time.sleep(300)
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
        # Максимальная настройка силы
        engine.configure({"Skill Level": 20, "Hash": 256, "Threads": 2})
        print("Stockfish загружен и настроен на максимальную силу.")
        sys.stdout.flush()
    except Exception as e:
        print(f"Не удалось запустить Stockfish: {e}")
        traceback.print_exc()
        return

    print("Бот запущен. Ожидание вызовов...")
    sys.stdout.flush()

    # Запускаем поток рассылки вызовов
    challenger_thread = threading.Thread(target=challenge_loop, daemon=True)
    challenger_thread.start()
    print("Поток рассылки вызовов запущен (интервал 10 минут)")
    sys.stdout.flush()

    my_id = client.account.get()['id']
    print(f"Мой ID: {my_id}")

    # Главный цикл с долгоживущим стримом событий
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
                            print(f"Пропускаем свой вызов {ch['id']}")
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
                        print(f"Ошибка принятия вызова: {e}")
                        traceback.print_exc()
        except (berserk.exceptions.ApiError, requests.exceptions.ConnectionError) as e:
            if isinstance(e, berserk.exceptions.ApiError) and hasattr(e, 'response') and e.response is not None:
                if e.response.status_code == 429:
                    print("429 Too Many Requests. Делаем паузу 5 минут.")
                    time.sleep(300)
                    continue
            print(f"Ошибка соединения в главном цикле: {e}. Переподключение через 30 сек...")
            time.sleep(30)
            continue
        except Exception as e:
            print(f"Неожиданная ошибка в главном цикле: {e}")
            traceback.print_exc()
            time.sleep(30)
            continue

thread = threading.Thread(target=run_bot, daemon=True)
thread.start()
