import os
import sys
import threading
import time
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

active_games = 0
games_lock = threading.Lock()

@app.get("/health")
def health():
    return {"status": "ok"}

def get_move_time_from_clock(clock_event):
    increment = clock_event.get('increment', 0)
    if increment <= 1:
        return 0.5
    elif increment <= 3:
        return 2.0
    else:
        return 5.0

def make_move_with_retry(game_id, board, move_time):
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
                            current_moves = moves.split()
                            while len(current_moves) > len(board.move_stack):
                                board.push_uci(current_moves[len(board.move_stack)])
                        if white_id is None:
                            white_id = event.get('white', {}).get('id')
                        if black_id is None:
                            black_id = event.get('black', {}).get('id')
                    else:
                        continue

                    if event.get('status') and event.get('status') != 'started':
                        print(f"[{game_id}] Завершена: {event.get('status')}")
                        return

                    if white_id is None or black_id is None:
                        continue

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

def run_bot():
    global engine
    try:
        print("Загружаем Stockfish...")
        sys.stdout.flush()
        engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
        engine.configure({"Skill Level": 20, "Hash": 256, "Threads": 2})
        print("Stockfish загружен и настроен на максимальную силу.")
        sys.stdout.flush()
    except Exception as e:
        print(f"Не удалось запустить Stockfish: {e}")
        traceback.print_exc()
        return

    print("Бот запущен. Ожидание вызовов...")
    sys.stdout.flush()

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
                        # Принимаем все вызовы (включая свои, чтобы бот играл партии,
                        # которые мы отправляем вручную)
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
